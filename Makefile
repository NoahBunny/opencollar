# Makefile — QA harness wrapper for FocusLock / Lion's Share / Bunny Tasker.
#
# Targets land Stream C item 4 of the 2026-04-27 audit: a one-command
# `make qa` that boots a staging relay, runs all four QA layers (pytest +
# qa_runner + qa_wizard_browser + qa_index_browser), and tears down with
# state-clean. Uses staging/config.json + staging/lion_privkey.pem.
#
# Prereqs: see docs/STAGING.md.

# Use the project venv if it exists; otherwise system python3.
ifneq (,$(wildcard .venv/bin/python3))
PY ?= .venv/bin/python3
else
PY ?= python3
endif
STAGING_DIR := staging
RELAY_PORT ?= 8435
RELAY_URL ?= http://127.0.0.1:$(RELAY_PORT)
RELAY_PIDFILE := $(STAGING_DIR)/.relay.pid
STATE_DIR ?= /tmp/focuslock-staging

.PHONY: help qa qa-staging-up qa-staging-down qa-clean qa-pytest qa-runner qa-wizard qa-index qa-perf qa-matrix lint

help:
	@echo 'Targets:'
	@echo '  qa              Run full QA: staging up, pytest, qa_runner, wizard, index, staging down.'
	@echo '  qa-staging-up   Boot staging relay on $(RELAY_URL); writes pidfile to $(RELAY_PIDFILE).'
	@echo '  qa-staging-down Kill staging relay + state-clean $(STATE_DIR).'
	@echo '  qa-clean        State-clean only ($(STATE_DIR)); does not kill relay.'
	@echo '  qa-pytest       pytest tests/ (does NOT need staging relay).'
	@echo '  qa-runner       Drive /admin/order against staging relay (49 cases).'
	@echo '  qa-wizard       Playwright walkthrough of web/signup.html (8 cases).'
	@echo '  qa-index        Playwright walkthrough of web/index.html (19 cases).'
	@echo '  qa-perf         Performance smoke test (tests/test_perf_smoke.py).'
	@echo '  qa-matrix       Regression matrix walker (staging/qa_matrix.py).'
	@echo '  lint            ruff check + ruff format --check.'
	@echo
	@echo 'Vars: PY=$(PY) RELAY_PORT=$(RELAY_PORT) STATE_DIR=$(STATE_DIR)'

qa: qa-staging-up qa-pytest qa-runner qa-wizard qa-index qa-staging-down
	@echo '== make qa: all layers green =='

qa-staging-up:
	@if [ -f $(RELAY_PIDFILE) ] && kill -0 "$$(cat $(RELAY_PIDFILE))" 2>/dev/null; then \
		echo "Staging relay already running (pid $$(cat $(RELAY_PIDFILE)))."; \
	else \
		$(MAKE) qa-clean; \
		echo "Booting staging relay on $(RELAY_URL)..."; \
		FOCUSLOCK_CONFIG=$(STAGING_DIR)/config.json \
		FOCUSLOCK_STATE_DIR=$(STATE_DIR) \
			$(PY) focuslock-mail.py >$(STAGING_DIR)/.relay.log 2>&1 & \
		echo $$! > $(RELAY_PIDFILE); \
		for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do \
			if curl -fs $(RELAY_URL)/version >/dev/null 2>&1; then \
				echo "Staging relay up (pid $$(cat $(RELAY_PIDFILE)))."; \
				exit 0; \
			fi; \
			sleep 1; \
		done; \
		echo "ERROR: staging relay did not respond on $(RELAY_URL) within 15s."; \
		echo "       Check $(STAGING_DIR)/.relay.log for errors."; \
		kill "$$(cat $(RELAY_PIDFILE))" 2>/dev/null || true; \
		rm -f $(RELAY_PIDFILE); \
		exit 1; \
	fi

qa-staging-down:
	@if [ -f $(RELAY_PIDFILE) ]; then \
		pid="$$(cat $(RELAY_PIDFILE))"; \
		if kill -0 "$$pid" 2>/dev/null; then \
			kill "$$pid" 2>/dev/null || true; \
			for i in 1 2 3 4 5; do \
				if ! kill -0 "$$pid" 2>/dev/null; then break; fi; \
				sleep 1; \
			done; \
			kill -9 "$$pid" 2>/dev/null || true; \
		fi; \
		rm -f $(RELAY_PIDFILE); \
		echo "Staging relay stopped."; \
	fi
	@$(MAKE) qa-clean

qa-clean:
	@if [ -d $(STATE_DIR) ]; then \
		rm -rf $(STATE_DIR); \
		echo "Cleaned $(STATE_DIR)."; \
	fi
	@rm -f $(STAGING_DIR)/.relay.log

qa-pytest:
	$(PY) -m pytest tests/ -q

qa-runner:
	$(PY) $(STAGING_DIR)/qa_runner.py --relay $(RELAY_URL) --config $(STAGING_DIR)/config.json

qa-wizard:
	$(PY) $(STAGING_DIR)/qa_wizard_browser.py

qa-index:
	$(PY) $(STAGING_DIR)/qa_index_browser.py

qa-perf:
	PERF_TESTS=1 $(PY) -m pytest tests/test_perf_smoke.py -v

qa-matrix:
	$(PY) $(STAGING_DIR)/qa_matrix.py

lint:
	ruff check .
	ruff format --check .
