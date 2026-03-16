# Lint: ruff check (config in pyproject.toml). Install: pip install -e ".[dev]"
.PHONY: lint
lint:
	ruff check .

# Format check only (CI); use 'make format' to fix.
.PHONY: format-check
format-check:
	ruff format --check .

# Apply ruff format to all sources.
.PHONY: format
format:
	ruff format .

# Test: pytest (testpaths in pyproject.toml).
.PHONY: test
test:
	python -m pytest

# Lint + test (full check without format).
.PHONY: check
check: lint test
