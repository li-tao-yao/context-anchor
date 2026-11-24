#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TARGET_DIR="${HOME}/.gemini/config/plugins/context-anchor"
UNINSTALL=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --uninstall)
      UNINSTALL=1
      shift
      ;;
    --target)
      TARGET_DIR="$2"
      shift 2
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

if [[ "$UNINSTALL" -eq 1 ]]; then
  if [[ -d "$TARGET_DIR" ]]; then
    rm -rf "$TARGET_DIR"
    echo "Removed plugin from: $TARGET_DIR"
  else
    echo "Plugin directory not found: $TARGET_DIR"
  fi
  echo "Note: Persistent hook state in ${HOME}/.gemini/antigravity/context-anchor-state was preserved."
  exit 0
fi

PYTHON_CMD="python"
if ! command -v python >/dev/null 2>&1; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_CMD="python3"
  else
    echo "Error: Python 3 is required but neither 'python' nor 'python3' was found in PATH." >&2
    exit 1
  fi
fi

mkdir -p "$TARGET_DIR"
rm -rf "$TARGET_DIR/assets" "$TARGET_DIR/rules" "$TARGET_DIR/skills" "$TARGET_DIR/scripts"

for f in plugin.json hooks.json config.default.json LICENSE README.md; do
  if [[ -f "$REPO_ROOT/$f" ]]; then
    cp "$REPO_ROOT/$f" "$TARGET_DIR/$f"
  fi
done

for d in assets rules skills; do
  if [[ -d "$REPO_ROOT/$d" ]]; then
    cp -R "$REPO_ROOT/$d" "$TARGET_DIR/$d"
  fi
done

mkdir -p "$TARGET_DIR/scripts"
for sf in common.py pre_invocation.py ledger_tool.py install.ps1 install.sh; do
  if [[ -f "$SCRIPT_DIR/$sf" ]]; then
    cp "$SCRIPT_DIR/$sf" "$TARGET_DIR/scripts/$sf"
  fi
done

chmod +x "$TARGET_DIR/scripts/"*.py "$TARGET_DIR/scripts/install.sh"

if [[ "$PYTHON_CMD" == "python3" ]]; then
  python3 - "$TARGET_DIR/hooks.json" <<'PY'
import json, sys
from pathlib import Path
p = Path(sys.argv[1])
data = json.loads(p.read_text(encoding="utf-8"))
for entry in data.get("context-anchor", {}).get("PreInvocation", []):
    if isinstance(entry, dict) and entry.get("command") == "python ./scripts/pre_invocation.py":
        entry["command"] = "python3 ./scripts/pre_invocation.py"
p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY
fi

echo "Installed Context Anchor plugin to: $TARGET_DIR"
