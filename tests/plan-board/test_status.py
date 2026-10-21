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
