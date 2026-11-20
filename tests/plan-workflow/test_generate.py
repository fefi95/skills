import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugins" / "plan-workflow"
SKILL = PLUGIN / "skills" / "plan-workflow"
GEN = SKILL / "scripts" / "generate.py"
EXAMPLE = ROOT / "plugins/plan-board/skills/plan-board/examples/caching.plan.yaml"
HERE = Path(__file__).resolve().parent


def run(*args, check=True):
    done = subprocess.run([sys.executable, "-B", str(GEN), *map(str, args)], capture_output=True, text=True)
    if check:
        assert done.returncode == 0, done.stderr
    return done


def test_generates_a_workflow_with_meta_first(tmp_path):
    out = tmp_path / "w.js"
    run("generate", EXAMPLE, "--out", out)
    src = out.read_text()
    assert src.startswith('export const meta = { name: "caching", description: ')
    assert "__PLAN_JSON__" not in src and "__SLUG__" not in src and "__DESCRIPTION__" not in src
    assert "import(" not in src and "Date.now" not in src and "Math.random" not in src and "new Date" not in src


def test_default_output_is_under_dot_claude_workflows(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    done = subprocess.run([sys.executable, "-B", str(GEN), "generate", str(EXAMPLE)], cwd=tmp_path, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert (tmp_path / ".claude/workflows/caching.js").exists()


def test_name_option_sets_the_slug(tmp_path):
    out = tmp_path / "w.js"
    run("generate", EXAMPLE, "--out", out, "--name", "My Cache Plan")
    assert 'name: "my-cache-plan"' in out.read_text()


def test_hash_ignores_status_but_not_content(tmp_path):
    base = yaml.safe_load(EXAMPLE.read_text())

    def h(data):
        p = tmp_path / "p.yaml"
        p.write_text(yaml.safe_dump(data))
        return run("hash", p).stdout.strip()

    first = h(base)
    base["steps"][0]["status"] = "done"
    assert h(base) == first
    base["steps"][0]["title"] = "Something else"
    assert h(base) != first


def test_invalid_plans_stop_generation(tmp_path):
    cyc = tmp_path / "cycle.yaml"
    cyc.write_text("steps:\n  - {id: a, depends_on: [b]}\n  - {id: b, depends_on: [a]}\n")
    done = run("generate", cyc, "--out", tmp_path / "o.js", check=False)
    assert done.returncode != 0 and "cycle" in done.stderr
    assert not (tmp_path / "o.js").exists()
    empty = tmp_path / "empty.yaml"
    empty.write_text("steps: []\n")
    assert run("generate", empty, "--out", tmp_path / "o.js", check=False).returncode != 0


def test_step_ids_git_rejects_stop_generation(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    plan = tmp_path / "p.yaml"
    plan.write_text('steps:\n  - {id: "1.", title: One}\n')
    done = run("generate", plan, "--out", tmp_path / "o.js", check=False)
    assert done.returncode != 0 and "git rejects" in done.stderr
    assert not (tmp_path / "o.js").exists()


def test_missing_plan_board_gives_a_clear_message(tmp_path, monkeypatch):
    # Copy only the plan-workflow plugin so the sibling plan-board is not there.
    copy = tmp_path / "plugins" / "plan-workflow"
    shutil.copytree(PLUGIN, copy, ignore=shutil.ignore_patterns("__pycache__"))
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    done = subprocess.run(
        [sys.executable, "-B", str(copy / "skills/plan-workflow/scripts/generate.py"), "generate", str(EXAMPLE), "--out", str(tmp_path / "o.js")],
        capture_output=True, text=True, env=env)
    assert done.returncode != 0
    assert "plan-board plugin was not found" in done.stderr and "/plugin install plan-board" in done.stderr


def test_javascript_tests_pass():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    done = subprocess.run([node, "--test", str(HERE / "workflow.test.mjs")], capture_output=True, text=True)
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-2000:]


# ---- packaging

def test_manifests_list_both_plugins():
    market = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text())
    entry = next(p for p in market["plugins"] if p["name"] == "plan-workflow")
    manifest = json.loads((ROOT / entry["source"] / ".claude-plugin/plugin.json").read_text())
    assert manifest["name"] == "plan-workflow"
    assert (ROOT / entry["source"] / "skills/plan-workflow/SKILL.md").exists()


def test_skill_frontmatter():
    text = (SKILL / "SKILL.md").read_text()
    head = re.match(r"---\n(.*?)\n---\n", text, re.S).group(1)
    assert re.search(r"^name: plan-workflow$", head, re.M)
    assert re.search(r"^description: .{40,}", head, re.M)


def test_no_tests_or_junk_inside_the_plugin():
    banned = {"tests", "evals", "node_modules", "__pycache__"}
    bad = [p for p in PLUGIN.rglob("*") if banned & set(p.relative_to(PLUGIN).parts)]
    assert not bad


def test_readme_mentions_the_plugin():
    assert "plan-workflow" in (ROOT / "README.md").read_text()
