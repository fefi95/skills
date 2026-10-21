import json
import re
import subprocess
import sys
import hashlib
from pathlib import Path

import pytest
from conftest import SCRIPT, SKILL, ROOT, make_plan, pb

GOLDEN = Path(__file__).parent / "golden"


def render(plan):
    return pb.render_html(plan)


def embedded_data(page):
    m = re.search(r"const DATA = (.*?);\n", page, re.S)
    return json.loads(m.group(1))


def test_render_is_deterministic(examples):
    plan = pb.load_plan(examples / "caching.plan.yaml")
    assert render(plan) == render(plan)


def test_page_shell_is_identical_across_plans(examples):
    """Only the data changes between plans; the template around it stays the same."""
    a = render(pb.load_plan(examples / "caching.plan.yaml"))
    b = render(make_plan({"id": "x"}, title="Other"))
    strip = lambda p: re.sub(r"const DATA = .*?;\n", "", p, flags=re.S).replace("Other", "").replace("Add response caching to the API", "")
    assert strip(a).count("<section") == strip(b).count("<section")
    assert re.findall(r'id="([\w-]+)"', strip(a)) == re.findall(r'id="([\w-]+)"', strip(b))


HOSTILE = ['x <!-- <script> y', '</script><img src=x onerror=alert(1)>', "a b", '"quotes" & <tags> #1']


@pytest.mark.parametrize("text", HOSTILE)
def test_hostile_text_cannot_break_out_of_the_script_tag(text):
    page = render(make_plan({"id": "a", "title": text, "notes": text}, title=text, summary=text))
    data_line = re.search(r"const DATA = (.*?);\n", page, re.S).group(1)
    assert "<" not in data_line and ">" not in data_line and " " not in data_line
    assert embedded_data(page)["title"] == text  # round-trips exactly


def test_example_dependency_diagram_matches_golden(examples):
    plan = pb.load_plan(examples / "caching.plan.yaml")
    an, _ = pb.analyze(plan)
    got = {"deps.mmd": pb.dependency_mermaid(plan, an), "tree.mmd": pb.file_tree_mermaid(plan)}
    missing = [n for n in got if not (GOLDEN / f"caching.{n}").exists()]
    for n in missing:
        (GOLDEN / f"caching.{n}").write_text(got[n] + "\n")
    assert not missing, f"wrote new golden files {missing}; review them and rerun"
    for name, text in got.items():
        assert text + "\n" == (GOLDEN / f"caching.{name}").read_text(), \
            f"{name} changed; delete the golden file to regenerate"


def test_cli_render_writes_where_asked_and_gitignores(tmp_path, examples):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    r = subprocess.run([sys.executable, str(SCRIPT), "render", str(examples / "caching.plan.yaml")],
                       cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / ".claude/tmp/plan-board/add-response-caching-to-the-api.html").exists()
    assert (tmp_path / ".gitignore").read_text().splitlines().count(".claude/tmp/") == 1
    subprocess.run([sys.executable, str(SCRIPT), "render", str(examples / "caching.plan.yaml")], cwd=tmp_path, check=True, capture_output=True)
    assert (tmp_path / ".gitignore").read_text().splitlines().count(".claude/tmp/") == 1


def test_gitignore_appends_without_clobbering(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text("node_modules")  # no trailing newline
    pb.ensure_gitignore(tmp_path)
    assert (tmp_path / ".gitignore").read_text() == "node_modules\n.claude/tmp/\n"


def test_gitignore_skips_when_already_covered(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text(".claude/\n")
    pb.ensure_gitignore(tmp_path)
    assert (tmp_path / ".gitignore").read_text() == ".claude/\n"


def test_gitignore_outside_a_repo_does_nothing(tmp_path):
    assert pb.ensure_gitignore(tmp_path) is None
    assert not (tmp_path / ".gitignore").exists()


def test_cli_reports_errors_without_traceback(tmp_path):
    f = tmp_path / "bad.yaml"
    f.write_text("steps:\n  - {id: a, files: [foo.py]}\n")
    r = subprocess.run([sys.executable, str(SCRIPT), "render", str(f)], capture_output=True, text=True)
    assert r.returncode != 0 and "Traceback" not in r.stderr and r.stderr.startswith("error:")
