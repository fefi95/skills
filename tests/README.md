# Tests

These live at the repo root, outside `plugins/`, so they are **not** shipped when someone installs a plugin.
`tests/plan-board/test_packaging.py` checks that nothing from `tests/` or `evals/` ends up inside the plugin.

## plan-board

```
pip install -r tests/plan-board/requirements.txt
python3 -m pytest tests/plan-board          # unit + golden tests, about a second

cd tests/plan-board/e2e && npm install && npm test   # browser tests (Playwright)
```

Or `make test` from the repo root (runs both).

- **Unit tests** (`test_*.py`): plan checks, critical path, Gherkin parsing, status editing, rendering, `.gitignore` handling, packaging.
- **Golden files** (`golden/`): the Mermaid text for the example plan. If you change the diagram output on purpose, delete the golden files, run pytest twice, and review the diff.
- **Browser tests** (`e2e/`): open real reports in Chromium and click through them. Set `PW_CHROMIUM=/path/to/chromium` to use a specific browser, otherwise Playwright's own is used (`npx playwright install chromium`).
- If `mermaid.min.js` is upgraded on purpose, update `MERMAID_SHA256` in `test_packaging.py`.
