.PHONY: test test-unit test-e2e
test: test-unit test-e2e

test-unit:
	python3 -m pytest tests/plan-board tests/plan-workflow -q

test-e2e:
	cd tests/plan-board/e2e && npm install --no-audit --no-fund && npm test
