#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

NO_SETUP="${NO_SETUP:-0}"
SKIP_VENV="${SKIP_VENV:-0}"

echo "========================================"
echo "  JARVIS / Mark Assistant - Launcher"
echo "========================================"
echo ""

find_python() {
    local candidates=(
        "$SCRIPT_DIR/venv/bin/python3"
        "$SCRIPT_DIR/venv/bin/python"
        "$(command -v python3 2>/dev/null || true)"
        "$(command -v python 2>/dev/null || true)"
    )
    for c in "${candidates[@]}"; do
        if [[ -n "$c" && -x "$c" ]]; then
            echo "$c"
            return 0
        fi
    done
    return 1
}

run_setup() {
    echo "[1/3] Checking Python environment..."
    if [[ "$SKIP_VENV" == "0" && ! -x "$SCRIPT_DIR/venv/bin/python3" ]]; then
        echo "  Creating virtual environment..."
        "$PYTHON_EXE" -m venv venv
        export PYTHON_EXE="$SCRIPT_DIR/venv/bin/python3"
        echo "  Virtual environment created."
    fi
    echo "  Python OK: $PYTHON_EXE"

    echo ""
    echo "[2/3] Installing dependencies..."
    if [[ -f "$SCRIPT_DIR/requirements.txt" ]]; then
        "$PYTHON_EXE" -m pip install --upgrade pip 2>/dev/null || true
        "$PYTHON_EXE" -m pip install -r "$SCRIPT_DIR/requirements.txt" \
            || { echo "Warning: Some dependencies failed to install; continuing."; }
        echo "  Dependencies installed."
    else
        echo "  requirements.txt not found, skipping install."
    fi

    echo ""
    echo "[3/3] Checking environment variables..."
    if [[ ! -f "$SCRIPT_DIR/.env" && -f "$SCRIPT_DIR/.env.example" ]]; then
        cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
        echo "  Created .env from .env.example — please edit it before next run."
    fi
    echo "  Setup complete."
    echo ""
}

PYTHON_EXE="$(find_python || true)"
if [[ -z "$PYTHON_EXE" ]]; then
    echo "Error: Python interpreter not found. Install Python 3.10+ or add it to PATH." >&2
    exit 1
fi

if [[ "$NO_SETUP" == "0" ]]; then
    run_setup
fi

echo "Launching Mark Assistant..."
echo "Dashboard will be available at http://localhost:8080"
echo "Press Ctrl+C to stop."
echo ""

exec "$PYTHON_EXE" "$SCRIPT_DIR/Mark.py"
