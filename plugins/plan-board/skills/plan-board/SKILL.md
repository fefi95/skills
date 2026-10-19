---
name: plan-board
description: Turn an agreed implementation plan into a visual HTML report (step cards with arrows, architecture diagram, file tree) and a YAML hand-off file for the agent that builds it. Use only when the user asks for it, after they have agreed to a plan, e.g. "plan board", "visualize this plan", "make a board from plan.md or login.feature". Never use a claude.ai artifact for this.
---

# Plan Board

One YAML file is the single source of truth. The HTML report and the implementing agent both read it.

Script: `scripts/plan_board.py` (Python 3 + PyYAML). Below, `<skill>` means the folder that holds this `SKILL.md`. Everything is offline and self-contained.

## When to use
Only when the user asks, after they have reviewed and agreed to a plan. Do not start on your own.

## Steps

1. **Pick the source.**
   - A plan you just wrote in this session: use it.
   - A `.md` file: read it and infer steps, dependencies and files (see "Inference").
   - A `.feature` file: run `python3 <skill>/scripts/plan_board.py parse-feature FILE -o plans/<slug>.yaml`, then fill in what the script cannot know (files, dependencies, notes, architecture).
2. **Write the YAML** to `plans/<slug>.yaml` (or the path the user names). Format below.
3. **Check it:** `python3 <skill>/scripts/plan_board.py validate plans/<slug>.yaml`. Fix errors. Warnings (missing criteria, no files, inferred items) go to the user.
4. **Show the user what you guessed** before building. List every item in `plan.inferred` in plain words and let them correct it. Once confirmed, remove those entries (and any step-level `inferred`).
5. **Build the report:** `python3 <skill>/scripts/plan_board.py render plans/<slug>.yaml --open`
   - Writes to `.claude/tmp/plan-board/<slug>.html` and adds `.claude/tmp/` to the repo's `.gitignore` if missing.
   - If the browser can't open (cloud/headless), tell the user the path. Never publish it as an artifact.
6. **Edit loop.** For "move step 3 earlier", "add a note about caching" and the like: edit the YAML, run `validate`, run `render` again. Never edit the HTML. The layout stays identical; only the content changes.
7. **Hand-off.** Once the user accepts, the YAML is the brief for the implementing agent (see below).

## YAML format
```yaml
plan:
  title: string
  summary: string                # one or two lines
  complexity: low|medium|high    # optional; computed if left out
  open_questions: [string]
  risks: [string]
  decisions:                     # key architecture decisions
    - {title: string, detail: string}
  architecture:                  # Mermaid flowchart source, optional
    before: |
      flowchart LR
        ...
    after: |
      flowchart LR
        ...
  inferred: [string]             # things you guessed; user must confirm
steps:
  - id: string                   # unique, stable, never reuse
    title: string
    depends_on: [step id]
    files:
      - {path: string, action: create|edit|delete}
    acceptance_criteria:
      - {given: string, when: string, then: string}
    notes: string                # architecture context for this step
    status: not_started|in_progress|done
    inferred: [field names]      # optional, marks guessed fields
```
See `examples/caching.plan.yaml` and `examples/login.feature`.

## Inference (for `.md` and prose plans)
- Step boundaries: headings, numbered items, or clear phase changes.
- Dependencies: use the text if explicit; otherwise infer from data flow (what needs what). Mark guessed ones with step `inferred: [depends_on]` and add a line to `plan.inferred`.
- Files: only list files the plan names or clearly implies. Don't invent paths.
- Acceptance criteria: write them as Given/When/Then. If the plan gives none and you would be making them up, leave them empty. The report flags the gap, which is the point.
- Keep architecture diagrams small (under about 12 nodes). Quote labels with special characters.

## Gherkin rules
Each `Scenario` becomes one step. `And`/`But` continue the previous keyword. `Background` Given lines are added to every scenario. A second When after a Then starts another criterion. Optional tags set what Gherkin can't express: `@id:name`, `@depends:id1,id2`, `@file:create:path/to/file`.

## Agent hand-off
When implementing from the YAML:
1. Do steps in dependency order (a step starts only when every `depends_on` step is `done`).
2. Before starting a step, run `plan_board.py status FILE STEP_ID in_progress`. After its acceptance criteria pass, run `... done`. Use this command, not hand edits, so the file stays valid.
3. Treat `acceptance_criteria` as the tests to satisfy, and `notes` as context.
4. If the plan turns out wrong, stop and tell the user. Don't silently change the YAML structure.
5. To see progress, run `render` again.

## What the report shows
Header (steps, files, complexity, progress ring, coverage, open questions and risks) · zoomable canvas of step cards joined by arrows, with the critical path (longest dependency chain) in thick orange · Canvas/List toggle · per-step card with files, Given/When/Then, status, and a dashed red "no criteria" flag · architecture decisions with a Before/After diagram · file tree colored by create/edit/delete.
