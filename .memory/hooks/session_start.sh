#!/bin/bash
# EF Memory — SessionStart Hook
# Runs startup health check and outputs context for Claude.
# Stdout is injected as additionalContext into the session.

set -euo pipefail

# Resolve .memory/ directory relative to this script
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MEMORY_DIR="$(dirname "$SCRIPT_DIR")"
PROJECT_ROOT="$(dirname "$MEMORY_DIR")"

# Clean up stale compact harvest markers (>2h old)
find "$MEMORY_DIR/working/" -name ".compact_harvested" -mmin +120 -delete 2>/dev/null || true

# Run startup check (lightweight, <100ms)
cd "$PROJECT_ROOT"
python3 .memory/scripts/pipeline_cli.py --startup 2>/dev/null || true
