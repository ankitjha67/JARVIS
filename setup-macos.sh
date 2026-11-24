#!/bin/zsh
# One-time setup on macOS (Apple Silicon, macOS 13 or newer). Run from anywhere:  ./setup-macos.sh
set -euo pipefail

root="${0:A:h}"
cd "$root"

if [[ "$(uname -m)" != "arm64" ]]; then
  echo "Jarvis on macOS needs an Apple Silicon Mac (M1 or newer). Intel Macs are not supported yet." >&2
  exit 1
fi

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing: $1 — install it with:  $2" >&2
    exit 1
  fi
}
need uv   "curl -LsSf https://astral.sh/uv/install.sh | sh   (or: brew install uv)"
need node "brew install node"
need npm  "brew install node"

uv sync
npm --prefix ui install
npm --prefix ui run build

echo
uv run jarvis --doctor || true
echo
echo "Setup complete. Start with:  uv run jarvis"
echo "First run: macOS will ask to allow Accessibility, Screen Recording and Microphone for your terminal app."
echo "Details and what to test: docs/MACOS.md"
