import pytest
from conftest import make_plan, pb


def ids(*pairs):
    return [{"id": i, "depends_on": d} for i, d in pairs]


def test_cycle_is_rejected():
    with pytest.raises(pb.PlanError, match="cycle"):
        pb.analyze(make_plan(*ids(("a", ["b"]), ("b", ["a"]))))


def test_unknown_and_self_dependency_rejected():
    with pytest.raises(pb.PlanError, match="unknown step"):
        pb.analyze(make_plan(*ids(("a", ["zzz"]))))
    with pytest.raises(pb.PlanError, match="itself"):
        pb.analyze(make_plan(*ids(("a", ["a"]))))


def test_duplicate_ids_rejected():
    with pytest.raises(pb.PlanError, match="duplicate"):
        pb.analyze(make_plan(*ids(("a", []), ("a", []))))


def test_order_respects_dependencies_and_keeps_file_order_for_ties():
    an, _ = pb.analyze(make_plan(*ids(("c", ["a", "b"]), ("a", []), ("b", []))))
    assert an["order"] == ["a", "b", "c"]


def test_critical_path_is_longest_chain():
    plan = make_plan(*ids(("a", []), ("b", ["a"]), ("c", ["b"]), ("x", ["a"]), ("end", ["c", "x"])))
    an, _ = pb.analyze(plan)
    assert an["critical"] == ["a", "b", "c", "end"]
    assert ("b", "c") in an["crit_edges"] and ("a", "x") not in an["crit_edges"]


def test_critical_path_of_empty_plan():
    an, _ = pb.analyze(make_plan())
    assert an["critical"] == [] and an["order"] == []


def test_coverage_gap_and_warnings():
    plan = make_plan(
        {"id": "a", "files": [{"path": "x.py", "action": "edit"}],
         "acceptance_criteria": [{"given": "g", "when": "w", "then": "t"}]},
        {"id": "b"},
        inferred=["guess"],
    )
    an, warnings = pb.analyze(plan)
    assert an["uncovered"] == ["b"]
    assert any("'b' has no acceptance criteria" in w for w in warnings)
    assert any("'b' lists no files" in w for w in warnings)
    assert any("guess" in w for w in warnings)


def test_complexity_is_computed_unless_given():
    assert pb.analyze(make_plan({"id": "a"}))[0]["complexity"] == "low"
    assert pb.analyze(make_plan({"id": "a"}, complexity="high"))[0]["complexity"] == "high"
    many = [{"id": f"s{i}", "depends_on": [f"s{i-1}"] if i else []} for i in range(25)]
    assert pb.analyze(make_plan(*many))[0]["complexity"] == "high"


@pytest.mark.parametrize("raw", [
    {"steps": [{"id": "a", "files": ["foo.py"]}]},
    {"steps": [{"id": "a", "acceptance_criteria": ["x"]}]},
    {"plan": "x", "steps": []},
    {"plan": {"architecture": "x"}, "steps": []},
    {"steps": [{"id": "a", "status": "finished"}]},
    {"steps": [{"id": "a", "files": [{"path": "x", "action": "move"}]}]},
    {"steps": [{"title": "no id"}]},
    {"steps": "nope"},
    {"steps": [{"id": "a", "files": [{"path": ""}]}]},
    {"plan": {"complexity": "huge"}, "steps": []},
])
def test_bad_shapes_raise_plan_error_not_a_traceback(raw):
    with pytest.raises(pb.PlanError):
        pb.normalize(raw)


def test_bare_list_of_steps_is_accepted():
    assert pb.normalize([{"id": "a"}])["steps"][0]["id"] == "a"


@pytest.mark.parametrize("files", [
    ["src", "src/a.py"],
    ["src/a.py", "src"],
    ["."],
])
def test_file_tree_conflicts_are_caught_by_analyze(files):
    plan = make_plan({"id": "a", "files": [{"path": f, "action": "edit"} for f in files]})
    with pytest.raises(pb.PlanError):
        pb.analyze(plan)


def test_same_file_in_two_steps_is_fine():
    f = [{"path": "src/a.py", "action": "edit"}]
    pb.analyze(make_plan({"id": "a", "files": f}, {"id": "b", "files": f}))


def test_path_parts_normalizes():
    assert pb.path_parts("./src//a.py") == ("src", "a.py")
    assert pb.path_parts("src\\a.py") == ("src", "a.py")


def test_mermaid_escape():
    assert pb.mm('a "b" <c> & #d') == "a #quot;b#quot; #lt;c#gt; #amp; #35;d"
