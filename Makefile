# relore -- see AGENTS.md. Linting is ruff, full stop; there is no type-check gate.
#
# The venv is built on the LOWEST supported interpreter on purpose: the project floor is
# 3.10, and developing on it is what catches accidental 3.11+ syntax before CI does.
# Override with `make PYTHON=python3.13 ...`.
PYTHON ?= $(shell for p in python3.10 python3.11 python3.12 python3.13 python3; do \
	command -v $$p >/dev/null 2>&1 && $$p -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' \
	>/dev/null 2>&1 && { echo $$p; break; }; done)

VENV  := .venv
STAMP := $(VENV)/.installed

.PHONY: help install format lint test check clean

help:
	@echo "make install  create $(VENV) and install relore[dev] (editable)"
	@echo "make format   ruff format + ruff check --fix"
	@echo "make lint     ruff, read-only"
	@echo "make test     pytest"
	@echo "make check    lint + test -- what CI runs"
	@echo ""
	@echo "interpreter:  $(PYTHON)"

$(STAMP): pyproject.toml
	@test -n "$(PYTHON)" || { echo "no python >=3.10 on PATH; set PYTHON=..."; exit 1; }
	test -d $(VENV) || $(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install --quiet --upgrade pip
	$(VENV)/bin/pip install --quiet -e '.[dev]'
	touch $(STAMP)

install: $(STAMP)

format: $(STAMP)
	$(VENV)/bin/ruff format relore tests
	$(VENV)/bin/ruff check --fix relore tests

lint: $(STAMP)
	$(VENV)/bin/ruff format --check relore tests
	$(VENV)/bin/ruff check relore tests

test: $(STAMP)
	$(VENV)/bin/pytest -q

check: lint test

clean:
	rm -rf $(VENV) .pytest_cache .ruff_cache *.egg-info
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
