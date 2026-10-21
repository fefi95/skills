import hashlib
import json
import re

import pytest
from conftest import ROOT, SKILL, pb

MERMAID_SHA256 = "a43bc1afd446f9c4cc66ac5dd45d02e8d65e26fc5344ec0ef787f88d6ddb6f9e"


def test_manifests_are_valid_and_point_at_the_plugin():
    market = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text())
    plugin_dir = ROOT / market["plugins"][0]["source"]
    manifest = json.loads((plugin_dir / ".claude-plugin/plugin.json").read_text())
    assert manifest["name"] == market["plugins"][0]["name"] == "plan-board"
    assert (plugin_dir / "skills/plan-board/SKILL.md").exists()


def test_skill_frontmatter():
    text = (SKILL / "SKILL.md").read_text()
    head = re.match(r"---\n(.*?)\n---\n", text, re.S).group(1)
    assert re.search(r"^name: plan-board$", head, re.M)
    assert re.search(r"^description: .{40,}", head, re.M)


def test_tests_and_evals_are_not_inside_the_plugin():
    import subprocess
    files = subprocess.run(["git", "ls-files", "plugins/plan-board"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    banned = {"tests", "evals", "node_modules", "__pycache__"}
    assert files and not [f for f in files if banned & set(f.split("/"))]


def test_bundled_mermaid_is_unmodified():
    digest = hashlib.sha256((SKILL / "assets/mermaid.min.js").read_bytes()).hexdigest()
    assert digest == MERMAID_SHA256


@pytest.mark.parametrize("name", ["caching.plan.yaml"])
def test_examples_validate(examples, name):
    plan = pb.load_plan(examples / name)
    pb.analyze(plan)
