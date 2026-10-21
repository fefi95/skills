import pytest
import yaml
from conftest import pb

BLOCK = """\
# my plan
plan:
  title: T
  custom_field: keep me
  architecture:
    after: |
      flowchart LR
        A --> B
steps:
  - id: one   # first
    title: One
    status: not_started
    extra: stays
  - id: two
    title: Two
    notes: |
      multi
      line
  - {id: three, title: Three}
  - id: four
    title: Four
"""


def test_changes_only_the_target_status_line():
    new = pb.set_step_status(BLOCK, "one", "done")
    assert new == BLOCK.replace("status: not_started", "status: done")


def test_comments_unknown_keys_and_block_scalars_survive():
    new = pb.set_step_status(BLOCK, "two", "in_progress")
    assert "# my plan" in new and "custom_field: keep me" in new and "# first" in new
    assert "flowchart LR\n        A --> B" in new
    data = yaml.safe_load(new)
    assert data["steps"][1]["status"] == "in_progress"
    assert data["steps"][1]["notes"] == "multi\nline\n"  # block scalar untouched
    assert data["steps"][0].get("status") == "not_started"


def test_adds_status_to_flow_and_last_block_step():
    new = pb.set_step_status(BLOCK, "three", "done")
    assert yaml.safe_load(new)["steps"][2] == {"id": "three", "title": "Three", "status": "done"}
    new = pb.set_step_status(BLOCK, "four", "done")
    assert yaml.safe_load(new)["steps"][3]["status"] == "done"
    assert new.endswith("    status: done\n")


def test_last_step_without_trailing_newline():
    new = pb.set_step_status("steps:\n  - id: a\n    title: A", "a", "done")
    assert yaml.safe_load(new)["steps"][0]["status"] == "done"


def test_unknown_step_raises():
    with pytest.raises(pb.PlanError, match="no step"):
        pb.set_step_status(BLOCK, "nope", "done")


def test_cli_leaves_file_untouched_on_bad_input(tmp_path, capsys):
    f = tmp_path / "p.yaml"
    f.write_text(BLOCK)
    with pytest.raises(SystemExit):
        pb.main(["status", str(f), "nope", "done"])
    with pytest.raises(SystemExit):
        pb.main(["status", str(f), "one", "finished"])
    assert f.read_text() == BLOCK
    assert not list(tmp_path.glob("*.tmp"))


def test_cli_roundtrip_then_validates(tmp_path):
    f = tmp_path / "p.yaml"
    f.write_text(BLOCK)
    pb.main(["status", str(f), "one", "done"])
    plan = pb.load_plan(f)
    assert plan["steps"][0]["status"] == "done"
    assert f.read_text().startswith("# my plan")


# ---- second review round ----

import os
import stat

CRLF = BLOCK.replace("\n", "\r\n")


def run(args):
    with pytest.raises(SystemExit) as e:
        pb.main(args)
    return e.value.code


def test_crlf_file_keeps_its_line_endings(tmp_path):
    f = tmp_path / "p.yaml"
    f.write_bytes(CRLF.encode())
    pb.main(["status", str(f), "one", "done"])
    pb.main(["status", str(f), "four", "done"])
    data = f.read_bytes().decode()
    assert "\n" not in data.replace("\r\n", "")  # no bare LF anywhere
    assert data.count("\r\n") == CRLF.count("\r\n") + 1  # only one new line (four's status)


def test_symlink_and_permissions_survive(tmp_path):
    real = tmp_path / "real.yaml"
    real.write_text(BLOCK)
    os.chmod(real, 0o600)
    link = tmp_path / "link.yaml"
    link.symlink_to(real)
    pb.main(["status", str(link), "one", "done"])
    assert link.is_symlink()
    assert "status: done" in real.read_text()
    assert stat.S_IMODE(real.stat().st_mode) == 0o600
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("body", [
    "steps:\n  - id: a\n    status:\n",
    "steps:\n  - id: a\n    status: ~\n",
    "steps:\n  - id: a\n    status: not_started # was told to wait\n",
    "steps:\n  - {id: a, }\n",
    "steps:\n  - {id: a, status: not_started}\n",
])
def test_odd_layouts_still_edit_correctly(body):
    new = pb.set_step_status(body, "a", "done")
    assert yaml.safe_load(new)["steps"][0]["status"] == "done"


def test_comment_after_status_is_kept():
    new = pb.set_step_status("steps:\n  - id: a\n    status: not_started # note\n", "a", "done")
    assert new.endswith("status: done # note\n")


def test_aliases_are_refused_and_file_untouched(tmp_path):
    body = "base: &s not_started\nsteps:\n  - id: a\n    status: *s\n  - id: b\n    status: *s\n"
    with pytest.raises(pb.PlanError, match="safely"):
        pb.set_step_status(body, "a", "done")
    f = tmp_path / "p.yaml"
    f.write_text(body)
    assert str(run(["status", str(f), "a", "done"])).startswith("error:")
    assert f.read_text() == body


def test_multi_document_file_is_refused():
    with pytest.raises(pb.PlanError):
        pb.set_step_status("steps: []\n---\nsteps: []\n", "a", "done")


@pytest.mark.parametrize("bad", ["id: yes", "id: 010", "id: 1.5"])
def test_non_text_ids_are_rejected_with_a_clear_message(bad):
    with pytest.raises(pb.PlanError, match="quotes"):
        pb.normalize(yaml.safe_load(f"steps:\n  - {bad}\n"))


def test_quoted_numeric_ids_work_end_to_end(tmp_path):
    f = tmp_path / "p.yaml"
    f.write_text('steps:\n  - {id: "1"}\n  - {id: "2", depends_on: ["1"]}\n')
    pb.main(["status", str(f), "1", "done"])
    assert pb.load_plan(f)["steps"][0]["status"] == "done"


@pytest.mark.parametrize("bad", ["a b", "a,b", "-a", "a/b", "é"])
def test_ids_must_be_simple(bad):
    with pytest.raises(pb.PlanError, match="may only use"):
        pb.normalize({"steps": [{"id": bad}]})


CHAIN = "steps:\n  - {id: a, status: not_started}\n  - {id: b, depends_on: [a]}\n"


def test_cannot_start_a_step_before_its_dependencies_are_done(tmp_path, capsys):
    f = tmp_path / "p.yaml"
    f.write_text(CHAIN)
    assert "waits for: a" in str(run(["status", str(f), "b", "in_progress"]))
    assert f.read_text() == CHAIN
    pb.main(["status", str(f), "b", "in_progress", "--force"])
    assert pb.load_plan(f)["steps"][1]["status"] == "in_progress"


def test_can_start_after_dependencies_done_and_can_always_reset(tmp_path):
    f = tmp_path / "p.yaml"
    f.write_text(CHAIN)
    pb.main(["status", str(f), "a", "done"])
    pb.main(["status", str(f), "b", "in_progress"])
    pb.main(["status", str(f), "a", "not_started"])  # going back is never blocked


def test_status_refuses_an_already_invalid_plan(tmp_path):
    f = tmp_path / "p.yaml"
    body = "steps:\n  - {id: a, depends_on: [b]}\n  - {id: b, depends_on: [a]}\n"
    f.write_text(body)
    assert run(["status", str(f), "a", "done", "--force"]) != 0
    assert f.read_text() == body


def test_next_lists_ready_running_and_done(tmp_path, capsys):
    f = tmp_path / "p.yaml"
    f.write_text("steps:\n  - {id: a}\n  - {id: b}\n  - {id: c, depends_on: [a, b]}\n")
    pb.main(["next", str(f)])
    assert capsys.readouterr().out.strip() == "ready: a, b"
    pb.main(["status", str(f), "a", "in_progress"])
    pb.main(["status", str(f), "a", "done"])
    pb.main(["status", str(f), "b", "in_progress"])
    capsys.readouterr()
    pb.main(["next", str(f)])
    assert capsys.readouterr().out.splitlines() == ["in progress: b"]
    pb.main(["status", str(f), "b", "done"])
    pb.main(["status", str(f), "c", "done"])
    capsys.readouterr()
    pb.main(["next", str(f)])
    assert capsys.readouterr().out.strip() == "all steps done"


def test_non_utf8_file_gives_a_clean_error(tmp_path, capsys):
    f = tmp_path / "p.yaml"
    f.write_bytes(b"steps:\n  - {id: a, title: \xff\xfe}\n")
    assert str(run(["validate", str(f)])).startswith("error:")
