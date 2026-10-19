#!/usr/bin/env python3
"""Plan Board: turn a plan YAML into a self-contained HTML report.

Commands:
  validate       plan.yaml                     check the plan, print warnings
  render         plan.yaml [--out F] [--open]  build the HTML report
  status         plan.yaml STEP_ID STATUS      update one step's status
  parse-feature  file.feature [-o plan.yaml]   draft a plan from Gherkin

The YAML file is the single source of truth. Needs Python 3.8+ and PyYAML.
"""
import argparse
import html
import json
import os
import re
import subprocess
import sys
import webbrowser
from pathlib import Path

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


class PlanError(Exception):
    pass


# ---------------------------------------------------------------- load / save

def load_plan(path):
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return normalize(raw)


def save_plan(path, plan):
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(plan, f, sort_keys=False, allow_unicode=True, width=100)


def _str(v):
    return "" if v is None else str(v).strip()


def _str_list(v):
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    return [_str(x) for x in v if _str(x)]


def normalize(raw):
    """Return a plan dict with every field present. Raises PlanError on bad input."""
    if isinstance(raw, list):  # bare list of steps is accepted
        raw = {"steps": raw}
    if not isinstance(raw, dict) or not isinstance(raw.get("steps"), list):
        raise PlanError("plan must be a mapping with a 'steps' list")
    meta = raw.get("plan") or {}
    arch = meta.get("architecture") or {}
    plan = {
        "plan": {
            "title": _str(meta.get("title")) or "Untitled plan",
            "summary": _str(meta.get("summary")),
            "complexity": _str(meta.get("complexity")).lower(),
            "open_questions": _str_list(meta.get("open_questions")),
            "risks": _str_list(meta.get("risks")),
            "decisions": [
                {"title": _str(d.get("title")), "detail": _str(d.get("detail"))}
                for d in (meta.get("decisions") or [])
                if isinstance(d, dict)
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
        sid = _str(s.get("id"))
        if not sid:
            raise PlanError(f"step #{i + 1} has no id")
        status = _str(s.get("status")) or "not_started"
        if status not in STATUSES:
            raise PlanError(f"step '{sid}': status must be one of {STATUSES}")
        files = []
        for f in s.get("files") or []:
            action = _str(f.get("action")) or "edit"
            if action not in ACTIONS:
                raise PlanError(f"step '{sid}': file action must be one of {ACTIONS}")
            if not _str(f.get("path")):
                raise PlanError(f"step '{sid}': file entry without a path")
            files.append({"path": _str(f["path"]), "action": action})
        ac = []
        for a in s.get("acceptance_criteria") or []:
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
    n_files = len({f["path"] for s in steps for f in s["files"]})
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
    out = text.replace("#", "#35;").replace("&", "#amp;")
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
            parts = Path(f["path"]).parts
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
        "  classDef create fill:#e4daf6,stroke:#7a3b3b,stroke-width:2.5px,color:#5a2d2d;",
        "  classDef edit fill:#ffe9cf,stroke:#7a3b3b,stroke-width:2.5px,color:#5a2d2d;",
        "  classDef delete fill:#f9c28e,stroke:#7a3b3b,stroke-width:2.5px,color:#5a2d2d;",
    ]
    return "\n".join(lines)


# -------------------------------------------------------------------- render

def build_data(plan, an):
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
        "n_files": an["n_files"],
        "dep_mermaid": dependency_mermaid(plan, an),
        "tree_mermaid": file_tree_mermaid(plan),
    }


def render_html(plan):
    an, _ = analyze(plan)
    data = build_data(plan, an)
    tpl = (ASSETS / "template.html").read_text(encoding="utf-8")
    mermaid_js = (ASSETS / "mermaid.min.js").read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    # Function replacements: avoid backslash handling in re/str replacement text.
    out = tpl.replace("/*__DATA__*/null", payload, 1)
    out = out.replace("/*__MERMAID__*/", mermaid_js.replace("</script", "<\\/script"), 1)
    out = out.replace("__TITLE__", html.escape(data["title"]), 1)
    return out


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "plan"


def ensure_gitignore(start):
    """Add GITIGNORE_ENTRY to the .gitignore at the git root. Returns path or None."""
    try:
        root = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], cwd=start,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    gi = Path(root) / ".gitignore"
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
    page = render_html(plan)
    if args.out:
        out = Path(args.out)
    else:
        out = DEFAULT_OUT_DIR / (slugify(plan["plan"]["title"]) + ".html")
        ensure_gitignore(Path.cwd())
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    print(f"Report written: {out.resolve()}")
    if args.open:
        webbrowser.open(out.resolve().as_uri())


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
    plan = load_plan(args.plan)
    for s in plan["steps"]:
        if s["id"] == args.step_id:
            s["status"] = args.status
            break
    else:
        sys.exit(f"no step with id '{args.step_id}'")
    save_plan(args.plan, plan)
    print(f"{args.step_id}: {args.status}")


# ------------------------------------------------------------------- gherkin

_STEP_RE = re.compile(r"^(Given|When|Then|And|But|\*)\s+(.*)$")
_SCEN_RE = re.compile(r"^(Scenario Outline|Scenario Template|Scenario|Example)\s*:\s*(.*)$")


def parse_feature(text):
    """Parse Gherkin into a plan dict (not yet normalized). One scenario = one step."""
    feature, summary, background = "", [], []
    scenarios, tags, cur, mode = [], [], None, None
    last_kw = None

    def add_step(target, kw, body):
        nonlocal last_kw
        if kw in ("And", "But", "*"):
            kw = last_kw or "Given"
        last_kw = kw
        target.append((kw, body))

    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith('"""') or s.startswith("```"):
            continue
        if s.startswith("@"):
            tags += s.split()
            continue
        if s.startswith("Feature:"):
            feature, mode = s[8:].strip(), "feature"
            continue
        if s.startswith("Background:"):
            mode, last_kw = "background", None
            continue
        if s.startswith("Rule:"):
            mode = "feature"
            continue
        m = _SCEN_RE.match(s)
        if m:
            cur = {"title": m.group(2).strip(), "tags": tags, "steps": [],
                   "outline": m.group(1) != "Scenario" and m.group(1) != "Example"}
            tags, last_kw, mode = [], None, "scenario"
            scenarios.append(cur)
            continue
        if s.startswith("Examples:") or s.startswith("Scenarios:"):
            mode = "examples"
            continue
        if mode == "examples" or s.startswith("|"):
            if mode == "scenario" and cur and cur["steps"]:
                kw, body = cur["steps"][-1]
                cur["steps"][-1] = (kw, body + " " + s)
            elif mode == "background" and background:
                kw, body = background[-1]
                background[-1] = (kw, body + " " + s)
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
        ac, curr, bg_given = [], None, [b for kw, b in background if kw == "Given"]
        for kw, body in sc["steps"]:
            if curr is None or (kw == "When" and curr["then"]):
                curr = {"given": list(bg_given), "when": [], "then": []}
                ac.append(curr)
            if kw == "Given" and (curr["when"] or curr["then"]):
                curr = {"given": [], "when": [], "then": []}
                ac.append(curr)
            curr[kw.lower()].append(body)
        files = []
        for v in tagmap.get("file", []):
            action, _, path = v.partition(":")
            if action in ACTIONS and path:
                files.append({"path": path, "action": action})
        deps = [d for v in tagmap.get("depends", []) for d in v.split(",") if d]
        steps.append({
            "id": sid,
            "title": sc["title"] or f"Scenario {n + 1}",
            "depends_on": deps,
            "files": files,
            "acceptance_criteria": [
                {k2: "; ".join(c[k2]) for k2 in ("given", "when", "then")} for c in ac
            ],
            "notes": "Scenario outline: the examples table is folded into the criteria text."
            if sc["outline"] else "",
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
    p.add_argument("status"); p.set_defaults(fn=cmd_status)
    p = sub.add_parser("parse-feature"); p.add_argument("feature")
    p.add_argument("-o", "--output"); p.set_defaults(fn=cmd_parse_feature)
    args = ap.parse_args(argv)
    try:
        args.fn(args)
    except (PlanError, OSError, yaml.YAMLError) as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
