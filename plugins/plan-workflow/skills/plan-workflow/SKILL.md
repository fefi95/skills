---
name: plan-workflow
description: Turn a plan-board YAML file into a Claude Code dynamic workflow (.claude/workflows/<slug>.js) that builds the whole plan, one draft PR per step. Use only when the user asks to generate or run a workflow from a plan, e.g. "make a workflow from plans/caching.yaml". Needs the plan-board plugin.
---

# Plan Workflow

Takes a plan-board YAML and writes a workflow script that implements it. The user runs the script as `/<slug>`.

`<skill>` below means the folder that holds this `SKILL.md` (the base directory shown when the skill loads). Needs Python 3 + PyYAML (`pip install pyyaml`) and the **plan-board** plugin. If plan-board is missing, the generator stops and says how to install it. Workflows must be on (`/config`, "Dynamic workflows").

## Generate
1. Run `python3 <skill>/scripts/generate.py generate PLAN.yaml`.
   - It runs plan-board's checks first. An invalid plan or a dependency cycle stops it with the error. Fix the YAML and run again.
   - It writes `.claude/workflows/<slug>.js` at the git root. `--out FILE` and `--name SLUG` change that.
   - If it can't find plan-board, find plan-board's `scripts/plan_board.py` and pass `--plan-board PATH`.
2. Tell the user the file path and how to run it:
   - `/<slug>`: build everything.
   - `/<slug> dryRun`: list the waves and files, change nothing.
   - `/<slug> base=<branch>`: start from a branch other than the default.
3. Never run it yourself unless the user asks. Claude Code's own "Yes, run it" prompt is the approval.

## What the workflow does
- **Hash check.** First agent compares a hash of the plan (ignoring `status`) with the one baked in. If the YAML changed, the run stops with "plan changed, regenerate".
- **Waves.** Steps whose `depends_on` are all done run together, up to 3 at once, each in its own git worktree. Steps already `done` in the YAML are skipped and assumed merged into the base.
- **Per step.** One agent writes tests and code together. A second runs the repo's own checks (from `CLAUDE.md`, `AGENTS.md`, Makefile, package scripts) and fixes failures. A fresh third agent judges each Given/When/Then from the diff and check results. No checks found: the step goes on and its PR says "Unverified".
- **Retry.** A failed step gets one retry. Then it is skipped, and so is everything behind it. The rest carries on.
- **PRs.** One commit and one draft PR per step. Chains use `gh stack` when it works. A step with several parents gets a plain draft PR on the parent that finished last, with the others merged in. If `gh stack` or draft PRs don't work, plain PRs are used.
- **CI.** After the waves, an agent watches each PR and fixes failures, up to 3 tries per PR. Still red: a comment, and it stays a draft.
- **Status.** Only the workflow writes `status`, in the starting checkout, with plan-board's `status` command. Never committed.
- **Report.** Done, failed, blocked and unverified steps, PR links, CI state, and whether real stacks were used.

## Limits to tell the user
- A run can't ask questions. Review the plan before you start it.
- The workflow has absolute paths baked in, so don't commit it unless the team shares those paths (plan, plan-board script). Regenerate after moving the repo or updating the plugins.
- Re-running continues where it stopped: finished steps are skipped. Their PRs must be merged, or the next steps start from the base branch without them.
