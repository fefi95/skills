#!/usr/bin/env python3
"""Plan Board: turn a plan YAML into a self-contained HTML report.

Commands:
  validate       plan.yaml                     check the plan, print warnings
  render         plan.yaml [--out F] [--open]  build the HTML report
  status         plan.yaml STEP_ID STATUS      update one step's status
  next           plan.yaml                     list steps in progress or ready
  parse-feature  file.feature [-o plan.yaml]   draft a plan from Gherkin

The YAML file is the single source of truth. Needs Python 3.8+ and PyYAML.
"""
import argparse
import copy
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import webbrowser
from pathlib import Path, PurePosixPath

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required: pip install pyyaml")

HERE = Path(__file__).resolve().parent
ASSETS = HERE.parent / "assets"
ACTIONS = ("create", "edit", "delete")
STATUSES = ("not_started", "in_progress", "done")
COMPLEXITIES = ("low", "medium", "high")
GITIGNORE_ENTRY = ".claude/tmp/"
DEFAULT_OUT_DIR = Path(".claude/tmp/plan-board")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class PlanError(Exception):
    pass


# ---------------------------------------------------------------- load / save

def load_plan(path):
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return normalize(raw)


def write_atomic(path, text):
    """Write via a temp file in the same folder and rename, so a failure never
    truncates the target. Follows symlinks, keeps file permissions and line endings."""
    path = Path(os.path.realpath(path))
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        if path.exists():
            shutil.copymode(path, tmp)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_plan(path, plan):
    write_atomic(path, yaml.safe_dump(plan, sort_keys=False, allow_unicode=True, width=100))


def read_text(path):
    with open(path, encoding="utf-8", newline="") as f:  # keep CRLF as it is
        return f.read()


def _data_step(data, step_id):
    steps = data.get("steps") if isinstance(data, dict) else data
    for s in steps or []:
        if isinstance(s, dict) and s.get("id") == step_id:
            return s
    return None


def set_step_status(text, step_id, status):
    """Return `text` with one step's status changed. Touches only that value, so
    comments, unknown keys and formatting elsewhere in the file are kept. The result
    is checked: parsed, it must equal the old plan with only that status changed."""
    try:
        root = yaml.compose(text)
    except yaml.YAMLError as e:
        raise PlanError(f"cannot read the plan as one YAML document: {e}")
    steps = None
    if isinstance(root, yaml.MappingNode):
        for k, v in root.value:
            if k.value == "steps" and isinstance(v, yaml.SequenceNode):
                steps = v.value
    elif isinstance(root, yaml.SequenceNode):
        steps = root.value
    if steps is None:
        raise PlanError("plan has no 'steps' list")
    nl = "\r\n" if "\r\n" in text else "\n"
    new = None
    for node in steps:
        if not isinstance(node, yaml.MappingNode):
            continue
        pairs = {k.value: (k, v) for k, v in node.value if isinstance(k, yaml.ScalarNode)}
        if "id" not in pairs or str(pairs["id"][1].value).strip() != step_id:
            continue
        if "status" in pairs:
            v = pairs["status"][1]
            gap = "" if v.start_mark.index == 0 or text[v.start_mark.index - 1] in " \t" else " "
            new = text[:v.start_mark.index] + gap + status + text[v.end_mark.index:]
        elif node.flow_style:  # {id: a, ...}: add before the closing brace
            at = node.end_mark.index - 1
            lead = "" if text[:at].rstrip().endswith(",") else ", "
            new = text[:at] + lead + "status: " + status + text[at:]
        else:
            last = node.value[-1][1]
            at = last.end_mark.index
            if last.end_mark.column != 0:  # inline value: continue to end of its line
                end = text.find("\n", at)
                if end < 0:
                    text += nl
                    end = len(text) - 1
                at = end + 1
            indent = " " * node.value[0][0].start_mark.column
            new = text[:at] + indent + "status: " + status + nl + text[at:]
        break
    if new is None:
        raise PlanError(f"no step with id '{step_id}'")
    try:
        expected = copy.deepcopy(yaml.safe_load(text))
        _data_step(expected, step_id)["status"] = status
        ok = yaml.safe_load(new) == expected
    except (yaml.YAMLError, TypeError):
        ok = False
    if not ok:
        raise PlanError(f"could not edit '{step_id}' safely (YAML aliases or unusual layout?); "
                        "change its status by hand")
    return new


def _str(v):
    return "" if v is None else str(v).strip()


def _str_list(v):
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    if not isinstance(v, list):
        raise PlanError(f"expected a list of text, got: {v!r}")
    return [_str(x) for x in v if _str(x)]


def _decisions(v):
    v = v or []
    if not isinstance(v, list) or not all(isinstance(d, dict) for d in v):
        raise PlanError("plan.decisions must be a list of {title, detail}")
    return v


def normalize(raw):
    """Return a plan dict with every field present. Raises PlanError on bad input."""
    if isinstance(raw, list):  # bare list of steps is accepted
        raw = {"steps": raw}
    if not isinstance(raw, dict) or not isinstance(raw.get("steps"), list):
        raise PlanError("plan must be a mapping with a 'steps' list")
    meta = raw.get("plan") or {}
    if not isinstance(meta, dict):
        raise PlanError("'plan' must be a mapping")
    arch = meta.get("architecture") or {}
    if not isinstance(arch, dict):
        raise PlanError("'plan.architecture' must be a mapping with before/after")
    plan = {
        "plan": {
            "title": _str(meta.get("title")) or "Untitled plan",
            "summary": _str(meta.get("summary")),
            "complexity": _str(meta.get("complexity")).lower(),
            "open_questions": _str_list(meta.get("open_questions")),
            "risks": _str_list(meta.get("risks")),
            "decisions": [
                {"title": _str(d.get("title")), "detail": _str(d.get("detail"))}
                for d in _decisions(meta.get("decisions"))
            ],
            "architecture": {
                "before": _str(arch.get("before")),
                "after": _str(arch.get("after")),
            },
            "inferred": _str_list(meta.get("inferred")),
        },
        "steps": [],
    }
    if plan["plan"]["complexity"] and plan["plan"]["complexity"] not in COMPLEXITIES:
        raise PlanError(f"plan.complexity must be one of {COMPLEXITIES}")
    for i, s in enumerate(raw["steps"]):
        if not isinstance(s, dict):
            raise PlanError(f"step #{i + 1} is not a mapping")
        sid = s.get("id")
        if sid is None or sid == "":
            raise PlanError(f"step #{i + 1} has no id")
        if not isinstance(sid, str):
            raise PlanError(f"step #{i + 1}: id {sid!r} must be text; put quotes around it")
        sid = sid.strip()
        if not ID_RE.match(sid):
            raise PlanError(f"step id '{sid}' may only use letters, digits, '.', '_' and '-'")
        status = _str(s.get("status")) or "not_started"
        if status not in STATUSES:
            raise PlanError(f"step '{sid}': status must be one of {STATUSES}")
        files = []
        for f in s.get("files") or []:
            if not isinstance(f, dict):
                raise PlanError(f"step '{sid}': each file must be a mapping with path and action")
            action = _str(f.get("action")) or "edit"
            if action not in ACTIONS:
                raise PlanError(f"step '{sid}': file action must be one of {ACTIONS}")
            if not _str(f.get("path")):
                raise PlanError(f"step '{sid}': file entry without a path")
            files.append({"path": _str(f["path"]), "action": action})
        ac = []
        for a in s.get("acceptance_criteria") or []:
            if not isinstance(a, dict):
                raise PlanError(f"step '{sid}': each criterion must have given/when/then")
            ac.append({k: _str(a.get(k)) for k in ("given", "when", "then")})
        plan["steps"].append({
            "id": sid,
            "title": _str(s.get("title")) or sid,
            "depends_on": _str_list(s.get("depends_on")),
            "files": files,
            "acceptance_criteria": ac,
            "notes": _str(s.get("notes")),
            "status": status,
            "inferred": _str_list(s.get("inferred")),
        })
    return plan


def path_parts(path):
    """Split a plan path into folder/file parts. Raises PlanError for non-paths."""
    parts = tuple(p for p in PurePosixPath(path.replace("\\", "/")).parts if p != ".")
    if not parts:
        raise PlanError(f"'{path}' is not a file path")
    return parts


# ------------------------------------------------------------------ analysis

def analyze(plan):
    """Validate the graph and compute derived data. Returns (analysis, warnings)."""
    steps = plan["steps"]
    ids = [s["id"] for s in steps]
    seen = set()
    for i in ids:
        if i in seen:
            raise PlanError(f"duplicate step id '{i}'")
        seen.add(i)
    for s in steps:
        for d in s["depends_on"]:
            if d not in seen:
                raise PlanError(f"step '{s['id']}' depends on unknown step '{d}'")
            if d == s["id"]:
                raise PlanError(f"step '{s['id']}' depends on itself")
    by_id = {s["id"]: s for s in steps}

    # A path can't be both a file and a folder (breaks the file tree).
    paths = {path_parts(f["path"]) for s in steps for f in s["files"]}
    for p in paths:
        for i in range(1, len(p)):
            if p[:i] in paths:
                raise PlanError(
                    f"'{'/'.join(p[:i])}' is listed as a file but also holds '{'/'.join(p)}'")

    # Topological order (stable: ties keep file order). Detects cycles.
    indeg = {i: len(by_id[i]["depends_on"]) for i in ids}
    children = {i: [] for i in ids}
    for s in steps:
        for d in s["depends_on"]:
            children[d].append(s["id"])
    order, ready = [], [i for i in ids if indeg[i] == 0]
    while ready:
        n = ready.pop(0)
        order.append(n)
        for c in children[n]:
            indeg[c] -= 1
            if indeg[c] == 0:
                ready.append(c)
        ready.sort(key=ids.index)
    if len(order) != len(ids):
        stuck = [i for i in ids if i not in order]
        raise PlanError("dependency cycle involving: " + ", ".join(stuck))

    # Critical path = longest dependency chain (by step count).
    depth, prev = {}, {}
    for n in order:
        best = None
        for d in by_id[n]["depends_on"]:
            if best is None or depth[d] > depth[best]:
                best = d
        depth[n] = (depth[best] if best else 0) + 1
        prev[n] = best
    critical = []
    if ids:
        end = max(order, key=lambda i: (depth[i], -ids.index(i)))
        while end:
            critical.append(end)
            end = prev[end]
        critical.reverse()
    crit_set = set(critical)
    crit_edges = {(a, b) for a, b in zip(critical, critical[1:])}

    uncovered = [s["id"] for s in steps if not s["acceptance_criteria"]]
    n_files = len(paths)
    actions = {}
    for s in steps:
        for f in s["files"]:
            actions.setdefault(path_parts(f["path"]), []).append(f["action"])
    complexity = plan["plan"]["complexity"]
    if not complexity:
        score = len(steps) + n_files // 2 + len(critical)
        complexity = "low" if score <= 8 else "medium" if score <= 20 else "high"

    warnings = []
    for i in uncovered:
        warnings.append(f"step '{i}' has no acceptance criteria")
    for s in steps:
        if not s["files"]:
            warnings.append(f"step '{s['id']}' lists no files")
    for p, acts in actions.items():
        if len(set(acts)) > 1:
            warnings.append(f"'{'/'.join(p)}' is listed with different actions: "
                            + ", ".join(sorted(set(acts))) + " (the tree shows the last)")
    for item in plan["plan"]["inferred"]:
        warnings.append(f"inferred, please confirm: {item}")
    return {
        "order": order,
        "critical": critical,
        "crit_set": crit_set,
        "crit_edges": crit_edges,
        "uncovered": uncovered,
        "complexity": complexity,
        "n_files": n_files,
    }, warnings


# ------------------------------------------------------------------- mermaid

def mm(text):
    """Escape text for use inside a quoted mermaid label."""
    out = text.replace("#", "#35;").replace("&", "#amp;").replace("`", "#96;")
    return out.replace("<", "#lt;").replace(">", "#gt;").replace('"', "#quot;")


def dependency_mermaid(plan, an):
    steps = plan["steps"]
    idx = {s["id"]: n for n, s in enumerate(steps)}
    lines = ["flowchart LR"]
    for n, s in enumerate(steps):
        crit = ' <span class="flag crit">critical path</span>' if s["id"] in an["crit_set"] else ""
        gap = ' <span class="flag gap">no criteria</span>' if s["id"] in an["uncovered"] else ""
        counts = f'{len(s["files"])} file{"" if len(s["files"]) == 1 else "s"}'
        label = (
            f'<div class="mcard st-{s["status"]}">'
            f'<div class="mtop"><span class="mnum">{n + 1}</span>'
            f'<span class="spill {s["status"]}">{s["status"].replace("_", " ")}</span></div>'
            f'<div class="mtitle">{mm(s["title"])}</div>'
            f'<div class="mmeta">{counts} · {len(s["acceptance_criteria"])} criteria</div>'
            f'<div>{crit}{gap}</div></div>'
        )
        label = label.replace('"', "'")  # titles are already escaped; only our own attributes remain
        lines.append(f'  n{n}["{label}"]:::card')
    link_i, crit_links = 0, []
    for s in steps:
        for d in s["depends_on"]:
            lines.append(f"  n{idx[d]} --> n{idx[s['id']]}")
            if (d, s["id"]) in an["crit_edges"]:
                crit_links.append(link_i)
            link_i += 1
    lines.append("  classDef card fill:transparent,stroke:transparent,color:#5a2d2d;")
    if crit_links:
        lines.append(
            f"  linkStyle {','.join(map(str, crit_links))} stroke:#e8782a,stroke-width:4px;"
        )
    return "\n".join(lines)


def file_tree_mermaid(plan):
    tree = {}
    for s in plan["steps"]:
        for f in s["files"]:
            node = tree
            parts = path_parts(f["path"])
            for p in parts[:-1]:
                node = node.setdefault(p, {})
            node[parts[-1]] = f["action"]  # str leaf = file
    lines = ["flowchart LR"]
    counter = [0]

    def nid():
        counter[0] += 1
        return f"t{counter[0]}"

    def walk(node, parent):
        for name in sorted(node, key=lambda k: (not isinstance(node[k], dict), k)):
            val = node[name]
            me = nid()
            if isinstance(val, dict):
                lines.append(f'  {me}["<span class=\'ic folder\'></span>{mm(name)}"]:::dir')
                walk(val, me)
            else:
                lines.append(f'  {me}["<span class=\'ic {val}\'></span>{mm(name)}"]:::{val}')
            lines.append(f"  {parent} --> {me}")

    lines.append('  t0["<span class=\'ic folder\'></span>project"]:::dir')
    walk(tree, "t0")
    lines += [
        "  classDef dir fill:#fff3e0,stroke:#8b5a44,stroke-width:3px,color:#5a2d2d;",
        "  classDef create fill:#dbe8fb,stroke:#7a3b3b,stroke-width:2.5px,color:#5a2d2d;",
        "  classDef edit fill:#ffe9cf,stroke:#7a3b3b,stroke-width:2.5px,color:#5a2d2d;",
        "  classDef delete fill:#f9ccd2,stroke:#7a3b3b,stroke-width:2.5px,color:#5a2d2d;",
    ]
    return "\n".join(lines)


# -------------------------------------------------------------------- render

def build_data(plan, an, source=""):
    steps = []
    for n, s in enumerate(plan["steps"]):
        d = dict(s)
        d["num"] = n + 1
        d["critical"] = s["id"] in an["crit_set"]
        d["covered"] = s["id"] not in an["uncovered"]
        steps.append(d)
    p = plan["plan"]
    return {
        "title": p["title"],
        "summary": p["summary"],
        "complexity": an["complexity"],
        "open_questions": p["open_questions"],
        "risks": p["risks"],
        "decisions": p["decisions"],
        "inferred": p["inferred"],
        "architecture": p["architecture"],
        "steps": steps,
        "critical": an["critical"],
        "order": an["order"],
        "source": source,
        "n_files": an["n_files"],
        "dep_mermaid": dependency_mermaid(plan, an),
        "tree_mermaid": file_tree_mermaid(plan),
    }


def render_html(plan, source=""):
    an, _ = analyze(plan)
    data = build_data(plan, an, source)
    tpl = (ASSETS / "template.html").read_text(encoding="utf-8")
    mermaid_js = (ASSETS / "mermaid.min.js").read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False)
    for ch, esc in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"),
                    ("\u2028", "\\u2028"), ("\u2029", "\\u2029")):
        payload = payload.replace(ch, esc)  # data can't open/close HTML tags or comments
    # Function replacements: avoid backslash handling in re/str replacement text.
    out = tpl.replace("/*__DATA__*/null", payload, 1)
    out = out.replace("/*__MERMAID__*/", mermaid_js.replace("</script", "<\\/script"), 1)
    out = out.replace("__TITLE__", html.escape(data["title"]), 1)
    return out


def slugify(text):
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "plan-" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]  # e.g. non-Latin titles


def git_root(start):
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=start,
                             capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return Path(out) if out else None


def ensure_gitignore(start):
    """Add GITIGNORE_ENTRY to the .gitignore at the git root. Returns path or None."""
    root = git_root(start)
    if root is None:
        return None
    gi = root / ".gitignore"
    existing = gi.read_text(encoding="utf-8") if gi.exists() else ""
    lines = [l.strip() for l in existing.splitlines()]
    if any(l in (GITIGNORE_ENTRY, GITIGNORE_ENTRY.rstrip("/"), "/" + GITIGNORE_ENTRY,
                 ".claude/", ".claude") for l in lines):
        return gi
    sep = "" if not existing or existing.endswith("\n") else "\n"
    gi.write_text(existing + sep + GITIGNORE_ENTRY + "\n", encoding="utf-8")
    return gi


def cmd_render(args):
    plan = load_plan(args.plan)
    page = render_html(plan, args.plan)
    if args.out:
        out = Path(args.out)
    else:
        # Under the git root, so it is ignored even when run from a subfolder.
        root = git_root(Path.cwd()) or Path.cwd()
        out = root / DEFAULT_OUT_DIR / (slugify(plan["plan"]["title"]) + ".html")
        ensure_gitignore(root)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    print(f"Report written: {out.resolve()}")
    if args.open:
        if not webbrowser.open(out.resolve().as_uri()):
            print("Could not open a browser; open the file above yourself.")


def cmd_validate(args):
    plan = load_plan(args.plan)
    an, warnings = analyze(plan)
    print(f"OK: {len(plan['steps'])} steps, {an['n_files']} files, "
          f"complexity {an['complexity']}")
    print("Critical path: " + " -> ".join(an["critical"]))
    for w in warnings:
        print("  warning: " + w)


def cmd_status(args):
    if args.status not in STATUSES:
        sys.exit(f"status must be one of {STATUSES}")
    path = Path(os.path.realpath(args.plan))
    text = read_text(path)
    plan = normalize(yaml.safe_load(text))
    analyze(plan)  # refuse to touch a plan that is already invalid
    steps = {s["id"]: s for s in plan["steps"]}
    if args.step_id not in steps:
        raise PlanError(f"no step with id '{args.step_id}'")
    if args.status != "not_started" and not args.force:
        waiting = [d for d in steps[args.step_id]["depends_on"] if steps[d]["status"] != "done"]
        if waiting:
            raise PlanError(f"'{args.step_id}' waits for: {', '.join(waiting)} "
                            "(finish those first, or use --force)")
    new = set_step_status(text, args.step_id, args.status)
    analyze(normalize(yaml.safe_load(new)))
    write_atomic(path, new)
    print(f"{args.step_id}: {args.status}")


def cmd_next(args):
    plan = load_plan(args.plan)
    an, _ = analyze(plan)
    st = {s["id"]: s for s in plan["steps"]}
    running = [i for i in an["order"] if st[i]["status"] == "in_progress"]
    ready = [i for i in an["order"] if st[i]["status"] == "not_started"
             and all(st[d]["status"] == "done" for d in st[i]["depends_on"])]
    if running:
        print("in progress: " + ", ".join(running))
    if ready:
        print("ready: " + ", ".join(ready))
    if not running and not ready:
        left = [i for i in an["order"] if st[i]["status"] != "done"]
        print("blocked: " + ", ".join(left) if left else "all steps done")


# ------------------------------------------------------------------- gherkin

_STEP_RE = re.compile(r"^(Given|When|Then|And|But|\*)\s+(.*)$")
_SCEN_RE = re.compile(r"^(Scenario Outline|Scenario Template|Scenario|Example)\s*:\s*(.*)$")


def parse_feature(text):
    """Parse Gherkin into a plan dict (not yet normalized). One scenario = one step."""
    feature, summary, background = "", [], []
    scenarios, tags, cur, mode = [], [], None, None
    last_kw, in_doc = None, False

    def add_step(target, kw, body):
        nonlocal last_kw
        if kw in ("And", "But", "*"):
            kw = last_kw or "Given"
        last_kw = kw
        target.append((kw, body))

    def append_to_last(target, extra):
        if target:
            kw, body = target[-1]
            target[-1] = (kw, (body + " " + extra).strip())

    for line in text.splitlines():
        s = line.strip()
        if s.startswith('"""') or s.startswith("```"):
            in_doc = not in_doc  # docstring text belongs to the step above it
            continue
        if in_doc:
            if mode == "scenario" and cur:
                append_to_last(cur["steps"], s)
            elif mode == "background":
                append_to_last(background, s)
            continue
        if not s or s.startswith("#"):
            continue
        if s.startswith("@"):
            tags += s.split("#")[0].split()
            continue
        if s.startswith("Feature:"):
            feature, mode, tags = s[8:].strip(), "feature", []  # tags above Feature aren't scenario tags
            continue
        if s.startswith("Background:"):
            mode, last_kw, tags = "background", None, []
            continue
        if s.startswith("Rule:"):
            mode, tags = "feature", []
            continue
        m = _SCEN_RE.match(s)
        if m:
            cur = {"title": m.group(2).strip(), "tags": tags, "steps": [], "examples": [],
                   "outline": m.group(1) not in ("Scenario", "Example")}
            tags, last_kw, mode = [], None, "scenario"
            scenarios.append(cur)
            continue
        if s.startswith("Examples:") or s.startswith("Scenarios:"):
            mode, tags = "examples", []  # tags above Examples belong to the table
            continue
        if s.startswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if mode == "examples" and cur:
                cur["examples"].append(cells)
            elif mode == "scenario" and cur:
                append_to_last(cur["steps"], s)
            elif mode == "background":
                append_to_last(background, s)
            continue
        m = _STEP_RE.match(s)
        if m and mode in ("scenario", "background"):
            add_step(cur["steps"] if mode == "scenario" else background, m.group(1), m.group(2))
            continue
        if mode == "feature":
            summary.append(s)

    inferred = []
    steps, used = [], set()
    for n, sc in enumerate(scenarios):
        tagmap = {}
        for t in sc["tags"]:
            if ":" in t:
                k, v = t[1:].split(":", 1)
                tagmap.setdefault(k, []).append(v)
        sid = (tagmap.get("id") or [slugify(sc["title"])])[0]
        base, k = sid, 2
        while sid in used:
            sid, k = f"{base}-{k}", k + 1
        used.add(sid)
        # Group Given/When/Then into criteria; a new When after a Then starts a new one.
        bg_given = [b for kw, b in background if kw == "Given"]
        ac, curr = [], None
        for kw, body in sc["steps"]:
            if curr is None or (kw == "When" and curr["then"]) or \
                    (kw == "Given" and (curr["when"] or curr["then"])):
                curr = {"given": list(bg_given), "when": [], "then": []}
                ac.append(curr)
            curr[kw.lower()].append(body)
        files = []
        for v in tagmap.get("file", []):
            action, _, path = v.partition(":")
            if action in ACTIONS and path:
                files.append({"path": path, "action": action})
        deps = [d for v in tagmap.get("depends", []) for d in v.split(",") if d]
        notes = ""
        if sc["examples"]:
            head, *rows = sc["examples"]
            notes = "Examples: " + " / ".join(
                ", ".join(f"{h}={c}" for h, c in zip(head, row)) for row in rows)
        elif sc["outline"]:
            notes = "Scenario outline without an examples table."
        steps.append({
            "id": sid,
            "title": sc["title"] or f"Scenario {n + 1}",
            "depends_on": deps,
            "files": files,
            "acceptance_criteria": [
                {k2: "; ".join(c[k2]) for k2 in ("given", "when", "then")} for c in ac
            ],
            "notes": notes,
            "status": "not_started",
        })
    if scenarios and not any(s["depends_on"] for s in steps):
        inferred.append("No dependencies declared (tag scenarios with @depends:<id>); "
                        "steps are shown unconnected.")
    if scenarios and not any(s["files"] for s in steps):
        inferred.append("No files declared (tag scenarios with @file:create:<path>); "
                        "list the files each step touches.")
    return {
        "plan": {"title": feature or "Untitled feature", "summary": " ".join(summary),
                 "inferred": inferred},
        "steps": steps,
    }


def cmd_parse_feature(args):
    text = Path(args.feature).read_text(encoding="utf-8")
    plan = normalize(parse_feature(text))
    out = Path(args.output) if args.output else Path(args.feature).with_suffix(".plan.yaml")
    save_plan(out, plan)
    print(f"Draft plan written: {out} ({len(plan['steps'])} steps)")
    for w in plan["plan"]["inferred"]:
        print("  flag: " + w)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("validate"); p.add_argument("plan"); p.set_defaults(fn=cmd_validate)
    p = sub.add_parser("render"); p.add_argument("plan")
    p.add_argument("--out", help="output HTML path (default: .claude/tmp/plan-board/)")
    p.add_argument("--open", action="store_true", help="open in the default browser")
    p.set_defaults(fn=cmd_render)
    p = sub.add_parser("status"); p.add_argument("plan"); p.add_argument("step_id")
    p.add_argument("status")
    p.add_argument("--force", action="store_true", help="allow starting a step before its dependencies are done")
    p.set_defaults(fn=cmd_status)
    p = sub.add_parser("next", help="list steps that are in progress or ready to start")
    p.add_argument("plan"); p.set_defaults(fn=cmd_next)
    p = sub.add_parser("parse-feature"); p.add_argument("feature")
    p.add_argument("-o", "--output"); p.set_defaults(fn=cmd_parse_feature)
    args = ap.parse_args(argv)
    try:
        args.fn(args)
    except (PlanError, OSError, UnicodeDecodeError, yaml.YAMLError) as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
