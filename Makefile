UV ?= uv
UV_VERSION ?= 0.11.7
PYTHON_VERSION ?= 3.11.15

.PHONY: bootstrap uv-version lock lock-check format format-check lint compile test coverage check clean-test-artifacts

uv-version:
	@actual="$$($(UV) --version | awk '{print $$2}')"; \
		test "$$actual" = "$(UV_VERSION)" || { \
			echo "Expected uv $(UV_VERSION), found $$actual" >&2; \
			exit 1; \
		}

bootstrap: uv-version
	$(UV) python install $(PYTHON_VERSION)
	$(UV) sync --locked --dev

lock: uv-version
	$(UV) lock

lock-check: uv-version
	$(UV) lock --check

format: uv-version
	$(UV) run --locked ruff check --fix --exit-zero .
	$(UV) run --locked ruff format .

format-check: uv-version
	$(UV) run --locked ruff format --check .

lint: uv-version
	$(UV) run --locked ruff check .

compile: uv-version
	$(UV) run --locked python -m compileall -q \
		backend_handlers.py help.py main.py utils_handlers.py white_lists.py tests

test: uv-version
	$(UV) run --locked pytest

coverage: uv-version
	$(UV) run --locked pytest \
		--cov=backend_handlers \
		--cov=help \
		--cov=main \
		--cov=utils_handlers \
		--cov=white_lists \
		--cov-report=term-missing \
		--cov-report=xml \
		--cov-fail-under=95

check: lock-check format-check lint compile coverage

clean-test-artifacts: uv-version
	$(UV) run --locked coverage erase
