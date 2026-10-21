from conftest import pb


def parse(text):
    return pb.normalize(pb.parse_feature(text))


FEATURE = """\
Feature: Login
  Lets people in.

  Background:
    Given the db is seeded

  @id:form @file:create:ui/Form.tsx
  Scenario: Show form
    When the user opens /login
    Then a form is shown

  @id:api @depends:form @file:edit:api/index.ts
  Scenario: Sign in
    Given a user exists
    When they send the right password
    Then they are redirected
    And a cookie is set
    When they send a wrong password
    Then they see an error
"""


def test_scenarios_become_steps_with_tags():
    plan = parse(FEATURE)
    assert plan["plan"]["title"] == "Login"
    assert plan["plan"]["summary"] == "Lets people in."
    form, api = plan["steps"]
    assert (form["id"], api["id"]) == ("form", "api")
    assert api["depends_on"] == ["form"]
    assert form["files"] == [{"path": "ui/Form.tsx", "action": "create"}]
    assert api["files"][0]["action"] == "edit"


def test_and_continues_previous_keyword_and_criteria_split_on_new_when():
    api = parse(FEATURE)["steps"][1]
    first, second = api["acceptance_criteria"]
    assert first["then"] == "they are redirected; a cookie is set"
    assert first["given"] == "the db is seeded; a user exists"
    assert second["when"] == "they send a wrong password"
    assert second["given"] == "the db is seeded"  # background applies to every criterion


def test_no_deps_or_files_are_flagged_not_invented():
    plan = parse("Feature: F\n  Scenario: One\n    When x\n    Then y\n")
    flags = " ".join(plan["plan"]["inferred"])
    assert "dependencies" in flags and "files" in flags
    assert plan["steps"][0]["depends_on"] == [] and plan["steps"][0]["files"] == []


def test_duplicate_titles_get_unique_ids():
    plan = parse("Feature: F\n Scenario: Same\n  When a\n  Then b\n Scenario: Same\n  When c\n  Then d\n")
    assert [s["id"] for s in plan["steps"]] == ["same", "same-2"]


def test_tags_above_feature_do_not_leak_into_first_scenario():
    plan = parse("@id:feat @depends:zzz\nFeature: F\n  @id:first\n  Scenario: One\n    When a\n    Then b\n")
    step = plan["steps"][0]
    assert step["id"] == "first" and step["depends_on"] == []
    pb.analyze(plan)  # still valid


def test_docstring_content_is_not_parsed_as_gherkin():
    text = 'Feature: F\n  Scenario: One\n    When I send\n      """\n      Given fake step\n      @id:evil\n      # kept\n      """\n    Then ok\n  Scenario: Two\n    When x\n    Then y\n'
    one, two = parse(text)["steps"]
    assert one["id"] == "one" and two["id"] == "two"
    assert len(one["acceptance_criteria"]) == 1
    assert "Given fake step" in one["acceptance_criteria"][0]["when"]


def test_examples_table_is_folded_into_notes():
    text = "Feature: F\n  Scenario Outline: Lock\n    Given failed <n> times\n    Then locked\n    Examples:\n      | n | who |\n      | 3 | ann |\n      | 5 | bob |\n"
    step = parse(text)["steps"][0]
    assert step["notes"] == "Examples: n=3, who=ann / n=5, who=bob"


def test_tags_above_examples_do_not_leak_to_next_scenario():
    text = ("Feature: F\n  Scenario Outline: A\n    Given <n>\n    Then ok\n    @id:oops\n    Examples:\n      | n |\n      | 1 |\n"
            "  Scenario: B\n    When x\n    Then y\n")
    assert parse(text)["steps"][1]["id"] == "b"


def test_tag_comment_is_not_a_tag():
    text = "Feature: F\n  @id:a # note here\n  Scenario: One\n    When x\n    Then y\n"
    assert parse(text)["steps"][0]["id"] == "a"


def test_example_feature_parses(examples):
    plan = parse((examples / "login.feature").read_text())
    assert [s["id"] for s in plan["steps"]] == ["login-form", "login-api", "lock-out"]
    pb.analyze(plan)
