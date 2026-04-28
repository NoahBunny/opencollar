# Makefile — unified QA harness entry points.
#
# Audit 2026-04-27 Stream C round 2: replaces the prior "manual relay
# restart between runs" friction with one-command targets that bring
# the staging relay up, drive the four QA layers, and tear down.
#
# Targets (run `make help` for descriptions):
#   make qa           — full sweep: clean → up → pytest + qa_runner +
#                       browser walkthroughs → down
#   make qa-fast      — pytest + ruff only (no staging relay)
#   make qa-staging-up    — start the staging relay in background
#   make qa-staging-down  — stop the staging relay
#   make qa-clean     — wipe the staging state dir
#   make help         — list targets
#
# Requirements:
#   - .venv/bin/python (set up via the project README)
#   - staging/config.json (copy from staging/config.json.template, fill in)
#   - staging/lion_privkey.pem + lion_pubkey.pem (throwaway test keypair)
#   - For browser walkthroughs: Playwright + headless Chromium
#
# Environment overrides:
#   PYTHON              path to python (default: .venv/bin/python)
#   STAGING_PORT        relay port (default: 8435 — what start-staging.sh binds)
#   STAGING_STATE_DIR   staging state dir (default: /tmp/focuslock-staging)
#   STAGING_PIDFILE     where to track the relay PID (default: /tmp/focuslock-staging.pid)
#   STAGING_LOGFILE     where to redirect relay output (default: /tmp/focuslock-staging.log)

PYTHON ?= .venv/bin/python
STAGING_PORT ?= 8435
STAGING_STATE_DIR ?= /tmp/focuslock-staging
STAGING_PIDFILE ?= /tmp/focuslock-staging.pid
STAGING_LOGFILE ?= /tmp/focuslock-staging.log
STAGING_URL ?= http://127.0.0.1:$(STAGING_PORT)

.PHONY: help qa qa-fast qa-staging-up qa-staging-down qa-clean qa-pytest qa-runner qa-browser

help:
	@echo "QA harness targets:"
	@echo "  make qa              full sweep (recommended)"
	@echo "  make qa-fast         pytest + ruff only (~90s, no staging relay)"
	@echo "  make qa-pytest       pytest tests/ (~2min)"
	@echo "  make qa-runner       staging/qa_runner.py against running relay"
	@echo "  make qa-browser      staging/qa_{wizard,index}_browser.py"
	@echo "  make qa-staging-up   start the staging relay in background"
	@echo "  make qa-staging-down stop the staging relay"
	@echo "  make qa-clean        wipe $(STAGING_STATE_DIR)"
	@echo ""
	@echo "Default staging URL: $(STAGING_URL)"

# ── Fast path: lint + tests only (no relay) ──

qa-fast:
	@echo "── ruff check ──"
	@$(PYTHON) -m ruff check .
	@echo "── ruff format --check ──"
	@$(PYTHON) -m ruff format --check .
	@echo "── pytest tests/ ──"
	@$(PYTHON) -m pytest tests/

qa-pytest:
	@$(PYTHON) -m pytest tests/

# ── Staging relay lifecycle ──

qa-clean:
	@echo "Wiping $(STAGING_STATE_DIR)…"
	@rm -rf "$(STAGING_STATE_DIR)"
	@rm -f "$(STAGING_PIDFILE)" "$(STAGING_LOGFILE)"

qa-staging-up: qa-clean
	@if [ -f "$(STAGING_PIDFILE)" ] && kill -0 "$$(cat $(STAGING_PIDFILE))" 2>/dev/null; then \
		echo "Staging relay already running (pid $$(cat $(STAGING_PIDFILE)))"; \
		exit 0; \
	fi
	@if [ ! -f staging/config.json ]; then \
		echo "ERROR: staging/config.json missing. Copy from staging/config.json.template + fill in."; \
		exit 1; \
	fi
	@echo "Starting staging relay on $(STAGING_URL)…"
	@FOCUSLOCK_STAGING_STATE_DIR="$(STAGING_STATE_DIR)" \
		nohup bash staging/start-staging.sh > "$(STAGING_LOGFILE)" 2>&1 < /dev/null & echo $$! > "$(STAGING_PIDFILE)"
	@for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do \
		if curl -sf "$(STAGING_URL)/version" > /dev/null 2>&1; then \
			echo "  relay ready after $$i seconds (pid $$(cat $(STAGING_PIDFILE)))"; \
			exit 0; \
		fi; \
		sleep 1; \
	done; \
	echo "ERROR: relay never came up — check $(STAGING_LOGFILE)"; \
	cat "$(STAGING_LOGFILE)" 2>&1 | tail -20; \
	exit 1

qa-staging-down:
	@if [ -f "$(STAGING_PIDFILE)" ]; then \
		pid=$$(cat "$(STAGING_PIDFILE)"); \
		if kill -0 "$$pid" 2>/dev/null; then \
			echo "Stopping staging relay (pid $$pid)…"; \
			kill "$$pid" 2>/dev/null || true; \
			sleep 1; \
			kill -9 "$$pid" 2>/dev/null || true; \
		fi; \
		rm -f "$(STAGING_PIDFILE)"; \
	fi
	@pkill -f 'python.*focuslock-mail.py' 2>/dev/null || true
	@echo "Staging relay stopped."

# ── QA driver scripts (require a running staging relay) ──

qa-runner:
	@echo "── staging/qa_runner.py ──"
	@$(PYTHON) staging/qa_runner.py --relay "$(STAGING_URL)"

qa-browser:
	@echo "── staging/qa_wizard_browser.py ──"
	@$(PYTHON) staging/qa_wizard_browser.py --relay "$(STAGING_URL)"
	@echo "── staging/qa_index_browser.py ──"
	@$(PYTHON) staging/qa_index_browser.py --relay "$(STAGING_URL)"

# ── Full sweep ──
# Bring up relay, run pytest (no relay required), run order driver,
# run browser walkthroughs, tear down. Tear-down runs even on failure
# via the trap-equivalent in shell-target chaining.

qa: qa-staging-up
	@echo "── Full QA sweep ──"
	@( $(PYTHON) -m pytest tests/ \
		&& $(PYTHON) staging/qa_runner.py --relay "$(STAGING_URL)" \
		&& $(PYTHON) staging/qa_wizard_browser.py --relay "$(STAGING_URL)" \
		&& $(PYTHON) staging/qa_index_browser.py --relay "$(STAGING_URL)" \
	); rc=$$?; \
	$(MAKE) --no-print-directory qa-staging-down; \
	exit $$rc
