.PHONY: install dev test lint clean playwright

PYTHON ?= python3

install:
	$(PYTHON) -m pip install -e .
	$(PYTHON) -m playwright install chromium

dev:
	$(PYTHON) -m pip install -e ".[dev]"
	$(PYTHON) -m playwright install chromium

playwright:
	$(PYTHON) -m playwright install chromium

test:
	$(PYTHON) -m pytest tests/

lint:
	$(PYTHON) -m ruff check linkedin_cli/

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete
	rm -rf build dist *.egg-info .pytest_cache .ruff_cache
