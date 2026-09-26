# ============================================================================
# StampBob — Makefile
# "The 90-Second Rule": any judge runs `make setup && make local` and sees
# the full end-to-end product with zero cloud accounts or billing.
# ============================================================================

.PHONY: setup eval test local clean help

# Default target — print help so an accidental bare `make` is never destructive
.DEFAULT_GOAL := help

# ─── Paths ───────────────────────────────────────────────────────────────────
PYTHON      ?= python3
PIP         ?= $(PYTHON) -m pip
PYTEST      ?= $(PYTHON) -m pytest
HARNESS     := src.eval.benchmark_harness
LOCAL_DEMO  := scripts/local_demo.py

# ─── Colours (suppressed when NO_COLOR is set or stdout is not a tty) ────────
BOLD  := $(shell tput bold   2>/dev/null || true)
GREEN := $(shell tput setaf 2 2>/dev/null || true)
CYAN  := $(shell tput setaf 6 2>/dev/null || true)
RESET := $(shell tput sgr0   2>/dev/null || true)

# ─── Ephemeral artefacts produced during runs ────────────────────────────────
CACHE_DIRS   := $(shell find . -name "__pycache__" -not -path "./.git/*" 2>/dev/null)
LOG_FILES    := $(shell find . -name "*.log" -not -path "./.git/*" 2>/dev/null)
SANDBOX_DIRS := $(shell find /tmp -maxdepth 1 -name "stampbob_repro_*" 2>/dev/null)

# ============================================================================
# help  — default target, lists all available targets
# ============================================================================
help:
	@echo ""
	@echo "$(BOLD)$(CYAN)🔏  StampBob — available Make targets$(RESET)"
	@echo ""
	@echo "  $(BOLD)make setup$(RESET)   Install Python dependencies (pip install -r requirements.txt)"
	@echo "  $(BOLD)make eval$(RESET)    Run the offline eval benchmark against 50 golden PR fixtures"
	@echo "  $(BOLD)make test$(RESET)    Run the full unit test suite (engine · generator · eval · guardrails · telemetry · web)"
	@echo "  $(BOLD)make local$(RESET)   Zero-credential end-to-end demo — eval + PR simulation + repro + web UI"
	@echo "  $(BOLD)make clean$(RESET)   Remove caches, logs, and ephemeral sandbox directories"
	@echo ""

# ============================================================================
# setup  — install dependencies
# ============================================================================
setup:
	@echo "$(BOLD)$(GREEN)▶ Installing dependencies …$(RESET)"
	$(PIP) install --quiet --upgrade pip
	$(PIP) install -r requirements.txt
	@echo "$(GREEN)✓ Dependencies installed.$(RESET)"

# ============================================================================
# eval  — offline evaluation benchmark
#
# Runs python -m src.eval.benchmark_harness against all 50 golden PR fixtures
# and prints the benchmark matrix.  Exits with code 1 when precision < 92 %
# or hallucination rate > 5 %.
# ============================================================================
eval:
	@echo "$(BOLD)$(GREEN)▶ Running offline eval benchmark …$(RESET)"
	$(PYTHON) -m $(HARNESS)

# ============================================================================
# test  — full unit test suite
#
# Discovers and runs every test under tests/ covering:
#   engine · generator · eval · guardrails · telemetry · web
# ============================================================================
test:
	@echo "$(BOLD)$(GREEN)▶ Running unit test suite …$(RESET)"
	$(PYTEST) -v tests/

# ============================================================================
# local  — zero-credential interactive demo  (The 90-Second Rule)
#
# Orchestrated by scripts/local_demo.py:
#   1. Offline eval gate  — 50-fixture benchmark, static-analysis mode, no sandbox
#   2. Breaking PR        — injects a goroutine-leak diff through InvariantOracle
#   3. Repro synthesis    — synthesizes & sandbox-executes the reproduction test
#   4. Web UI             — starts Tactical Review Command Center at localhost:8000
#
# No IBM Cloud API key, no internet connection, no billing required.
# ============================================================================
local:
	@echo "$(BOLD)$(GREEN)▶ Starting StampBob end-to-end demo …$(RESET)"
	$(PYTHON) $(LOCAL_DEMO)

# ============================================================================
# clean  — remove caches, logs, and temporary sandbox directories
# ============================================================================
clean:
	@echo "$(BOLD)$(GREEN)▶ Cleaning up …$(RESET)"
	find . -type d -name "__pycache__"    -not -path "./.git/*" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache"  -not -path "./.git/*" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc"          -not -path "./.git/*" -delete             2>/dev/null || true
	find . -type f -name "*.log"          -not -path "./.git/*" -delete             2>/dev/null || true
	find . -type f -name "*.pyo"          -not -path "./.git/*" -delete             2>/dev/null || true
	find /tmp -maxdepth 1 -name "stampbob_repro_*" -exec rm -rf {} + 2>/dev/null || true
	@echo "$(GREEN)✓ Clean.$(RESET)"
