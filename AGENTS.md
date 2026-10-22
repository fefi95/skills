# AGENTS.md

Guidance for AI agents working in this repo.

## Layout
- `plugins/<name>/skills/<name>/`: the shipped skill (`SKILL.md`, `scripts/`, `assets/`, `examples/`).
- `.claude-plugin/marketplace.json`: lists the plugins. Add new plugins here.
- `plugins/<name>/.claude-plugin/plugin.json`: each plugin needs one. Tests check it.
- `tests/`: tests live here, outside `plugins/`, so they are not shipped to users.
- `README.md`: skill list and install steps. Keep it up to date when you add or rename a skill.

## Rules
- Never put tests, evals, `node_modules` or `__pycache__` inside `plugins/`. `tests/plan-board/test_packaging.py` checks this.
- Don't edit `plugins/plan-board/skills/plan-board/assets/mermaid.min.js`. If it is upgraded on purpose, update `MERMAID_SHA256` in `test_packaging.py`, and the version and SHA-256 in `VENDORED.md`.
- If you change the diagram output on purpose, refresh the golden files (see `tests/README.md`).

## Before you commit
- First time only: `pip install -r tests/plan-board/requirements.txt`. The browser tests also need Node and Chromium (see `tests/README.md`).
- Run `make test-unit` (about a second). Run `make test` if you touched the HTML report, since it also runs the browser tests.
- Keep explanations in docs short and in plain English.
