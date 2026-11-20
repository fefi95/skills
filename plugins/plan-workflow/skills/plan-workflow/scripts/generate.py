#!/usr/bin/env python3
"""Generate a Claude Code workflow (.js) that builds a plan-board plan.

  generate.py generate PLAN.yaml [--out FILE] [--name SLUG] [--plan-board PATH]
  generate.py hash PLAN.yaml [--plan-board PATH]

Needs Python 3 + PyYAML and the plan-board plugin (it does the plan checks).
"""
import argparse
import hashlib
import importlib.util
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE.parent / "assets" / "workflow.template.js"
MISSING = ("The plan-board plugin was not found, and plan-workflow needs it. Install it with "
           "`/plugin install plan-board@fefi95-skills`, or pass --plan-board "
           "PATH/TO/plan_board.py.")


def find_plan_board(explicit=None):
    """Return the path of plan_board.py, or exit with a clear message."""
    cands = [explicit, os.environ.get("PLAN_BOARD_SCRIPT")]
    cands.append(HERE.parents[3] / "plan-board/skills/plan-board/scripts/plan_board.py")
    for c in cands:
        if c and Path(c).is_file():
            return Path(c).resolve()
    cache = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "plugins"
    if cache.is_dir():
        hits = sorted(cache.glob("**/plan-board/**/scripts/plan_board.py"), key=lambda p: p.stat().st_mtime)
        if hits:
            return hits[-1].resolve()
    sys.exit(MISSING)


def load_plan_board(path):
    spec = importlib.util.spec_from_file_location("plan_board", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def plan_hash(plan):
    """Short hash of what the workflow bakes in. Ignores `status`, which the run edits."""
    keys = ("id", "title", "depends_on", "files", "acceptance_criteria", "notes")
    data = {"title": plan["plan"]["title"], "steps": [{k: s[k] for k in keys} for s in plan["steps"]]}
    blob = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def load_checked(pb, plan_path):
    try:
        plan = pb.load_plan(plan_path)
        pb.analyze(plan)  # raises on cycles, unknown ids, duplicates
    except (pb.PlanError, OSError) as e:
        sys.exit(f"error: {e}")
    except Exception as e:  # bad YAML
        sys.exit(f"error: could not read the plan: {e}")
    if not plan["steps"]:
        sys.exit("error: the plan has no steps")
    return plan


def check_branch_names(plan, slug):
    """Step ids become branch names (plan/<slug>/<id>). Reject ids git would refuse."""
    for s in plan["steps"]:
        name = f"plan/{slug}/{s['id']}"
        try:
            ok = subprocess.run(["git", "check-ref-format", "--branch", name],
                                capture_output=True).returncode == 0
        except OSError:
            return  # no git here; the run itself will fail loudly
        if not ok:
            sys.exit(f"error: step id '{s['id']}' makes the branch name '{name}', which git rejects. "
                     "Rename the step id (avoid a trailing '.', '..', or '.lock').")


def build(plan, plan_path, pb_script, slug, source_template):
    title = plan["plan"]["title"]
    data = {
        "title": title,
        "slug": slug,
        "planPath": str(Path(plan_path).resolve()),
        "planBoardScript": str(pb_script),
        "hashCommand": " ".join(["python3", shlex.quote(str(HERE / "generate.py")), "hash",
                                 shlex.quote(str(Path(plan_path).resolve())),
                                 "--plan-board", shlex.quote(str(pb_script))]),
        "planHash": plan_hash(plan),
        "steps": [
            {k: s[k] for k in ("id", "title", "depends_on", "files", "acceptance_criteria", "notes")}
            for s in plan["steps"]
        ],
    }
    desc = f"Build the plan '{title}' ({len(plan['steps'])} steps) with draft PRs per step."
    lit = lambda v: json.dumps(v, ensure_ascii=True)  # noqa: E731
    out = (source_template
           .replace('"__SLUG__"', lit(slug))
           .replace('"__DESCRIPTION__"', lit(desc))
           .replace("__PLAN_JSON__", json.dumps(data, indent=2, ensure_ascii=True)))
    return out


def cmd_generate(args):
    pb_script = find_plan_board(args.plan_board)
    pb = load_plan_board(pb_script)
    plan = load_checked(pb, args.plan)
    stem = Path(args.plan).name
    for suffix in (".yaml", ".yml", ".plan"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
    slug = pb.slugify(args.name or stem)
    check_branch_names(plan, slug)
    out = build(plan, args.plan, pb_script, slug, TEMPLATE.read_text(encoding="utf-8"))
    if args.out:
        target = Path(args.out)
    else:
        root = pb.git_root(Path.cwd()) or Path.cwd()
        target = root / ".claude" / "workflows" / f"{slug}.js"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(out, encoding="utf-8")
    print(f"Workflow written: {target.resolve()}")
    print(f"Run it with /{slug}  (add `base=<branch>` for a base other than the default, "
          "`dryRun` to only list the work)")
    print(f"{len(plan['steps'])} steps, plan hash {plan_hash(plan)}")


def cmd_hash(args):
    pb = load_plan_board(find_plan_board(args.plan_board))
    print(plan_hash(load_checked(pb, args.plan)))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("generate")
    p.add_argument("plan")
    p.add_argument("--out")
    p.add_argument("--name")
    p.add_argument("--plan-board")
    p.set_defaults(fn=cmd_generate)
    p = sub.add_parser("hash")
    p.add_argument("plan")
    p.add_argument("--plan-board")
    p.set_defaults(fn=cmd_hash)
    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
