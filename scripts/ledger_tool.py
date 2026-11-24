"""CLI management helper for Context Anchor ledgers."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from common import (  # noqa: E402
    DEFAULT_LEDGER_TEMPLATE,
    REQUIRED_SECTION_KEYWORDS,
    atomic_write_text,
    get_data_dir,
    get_plugin_root,
    is_paused,
    load_config,
    load_state,
    measure_ledger,
    resolve_ledger_paths,
)

PAUSED_MARKER = "<!-- context-anchor: paused -->"


def cmd_init(args: argparse.Namespace) -> int:
    target = Path(args.target).expanduser()
    if target.exists() and not args.force:
        print(json.dumps({"status": "exists", "path": str(target)}, ensure_ascii=False))
        return 0
    atomic_write_text(target, DEFAULT_LEDGER_TEMPLATE)
    print(json.dumps({"status": "created", "path": str(target)}, ensure_ascii=False))
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    target = Path(args.target).expanduser()
    if not target.is_file():
        print(json.dumps({"status": "missing", "path": str(target)}, ensure_ascii=False))
        return 2

    cfg = load_config(get_plugin_root(), get_data_dir())
    max_lines = int(args.max_lines or cfg["max_lines"])
    max_bytes = int(args.max_bytes or cfg["max_bytes"])

    text = target.read_text(encoding="utf-8-sig")
    line_count, byte_count = measure_ledger(text)
    lowered = text.lower()
    missing_sections = [kw for kw in REQUIRED_SECTION_KEYWORDS if kw not in lowered]

    over_lines = line_count > max_lines
    over_bytes = byte_count > max_bytes
    warn = over_lines or over_bytes or bool(missing_sections)

    report = {
        "status": "warn" if warn else "ok",
        "path": str(target),
        "lines": line_count,
        "max_lines": max_lines,
        "bytes": byte_count,
        "max_bytes": max_bytes,
        "paused": is_paused(text),
        "missing_sections": missing_sections,
    }
    print(json.dumps(report, ensure_ascii=False))
    return 1 if warn else 0


def cmd_pin(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).expanduser()
    project_ledger = workspace / ".agents" / "context-anchor" / "memory_ledger.md"

    if args.session_ledger:
        src = Path(args.session_ledger).expanduser()
        if not src.is_file():
            print(json.dumps({"status": "error", "reason": f"Session ledger not found: {src}"}, ensure_ascii=False))
            return 2
        content = src.read_text(encoding="utf-8-sig")
    else:
        content = DEFAULT_LEDGER_TEMPLATE

    atomic_write_text(project_ledger, content)
    print(json.dumps({"status": "pinned", "path": str(project_ledger)}, ensure_ascii=False))
    return 0


def cmd_unpin(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).expanduser()
    project_ledger = workspace / ".agents" / "context-anchor" / "memory_ledger.md"
    if not project_ledger.is_file():
        print(json.dumps({"status": "not_pinned", "path": str(project_ledger)}, ensure_ascii=False))
        return 0

    if args.session_ledger:
        dst = Path(args.session_ledger).expanduser()
        content = project_ledger.read_text(encoding="utf-8-sig")
        atomic_write_text(dst, content)

    project_ledger.unlink()
    print(json.dumps({"status": "unpinned", "removed": str(project_ledger)}, ensure_ascii=False))
    return 0


def cmd_pause(args: argparse.Namespace) -> int:
    target = Path(args.target).expanduser()
    if not target.is_file():
        print(json.dumps({"status": "missing", "path": str(target)}, ensure_ascii=False))
        return 2
    text = target.read_text(encoding="utf-8-sig")
    if is_paused(text):
        print(json.dumps({"status": "already_paused", "path": str(target)}, ensure_ascii=False))
        return 0
    updated = PAUSED_MARKER + "\n" + text
    atomic_write_text(target, updated)
    print(json.dumps({"status": "paused", "path": str(target)}, ensure_ascii=False))
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    target = Path(args.target).expanduser()
    if not target.is_file():
        print(json.dumps({"status": "missing", "path": str(target)}, ensure_ascii=False))
        return 2
    text = target.read_text(encoding="utf-8-sig")
    lines = [line for line in text.splitlines(keepends=True) if PAUSED_MARKER.lower() not in line.lower()]
    atomic_write_text(target, "".join(lines))
    print(json.dumps({"status": "resumed", "path": str(target)}, ensure_ascii=False))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    workspace_paths = [args.workspace] if args.workspace else []
    paths = resolve_ledger_paths(args.artifact_dir, workspace_paths)
    project_ledger = paths["project"]
    session_ledger = paths["session"]

    active_path: Path | None = None
    mode = "inactive"
    if project_ledger is not None and project_ledger.is_file():
        active_path = project_ledger
        mode = "project"
    elif session_ledger is not None and session_ledger.is_file():
        active_path = session_ledger
        mode = "session"

    result: dict[str, Any] = {
        "mode": mode,
        "active_path": str(active_path) if active_path else None,
        "project_path": str(project_ledger) if project_ledger else None,
        "session_path": str(session_ledger) if session_ledger else None,
    }

    if active_path is not None:
        text = active_path.read_text(encoding="utf-8-sig")
        lines, byte_count = measure_ledger(text)
        result["lines"] = lines
        result["bytes"] = byte_count
        result["paused"] = is_paused(text)

    if args.conversation_id:
        state = load_state(get_data_dir(), args.conversation_id)
        if state:
            result["state"] = {
                "last_injected_turn": state.get("last_injected_turn"),
                "last_auto_detect_turn": state.get("last_auto_detect_turn"),
                "inherited_from": state.get("inherited_from"),
                "inherited_base_turns": state.get("inherited_base_turns"),
            }

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Context Anchor ledger management tool.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Create a new ledger from template.")
    p_init.add_argument("--target", required=True, help="Target ledger markdown path.")
    p_init.add_argument("--force", action="store_true", help="Overwrite if already exists.")
    p_init.set_defaults(func=cmd_init)

    p_check = sub.add_parser("check", help="Validate ledger size and required sections.")
    p_check.add_argument("--target", required=True, help="Target ledger markdown path.")
    p_check.add_argument("--max-lines", type=int, default=0, help="Override max line count.")
    p_check.add_argument("--max-bytes", type=int, default=0, help="Override max byte count.")
    p_check.set_defaults(func=cmd_check)

    p_pin = sub.add_parser("pin", help="Pin ledger into workspace .agents/context-anchor/memory_ledger.md.")
    p_pin.add_argument("--workspace", required=True, help="Workspace directory.")
    p_pin.add_argument("--session-ledger", default="", help="Optional existing session ledger to copy.")
    p_pin.set_defaults(func=cmd_pin)

    p_unpin = sub.add_parser("unpin", help="Unpin workspace ledger.")
    p_unpin.add_argument("--workspace", required=True, help="Workspace directory.")
    p_unpin.add_argument("--session-ledger", default="", help="Optional session ledger path to copy back to.")
    p_unpin.set_defaults(func=cmd_unpin)

    p_pause = sub.add_parser("pause", help="Pause Context Anchor injections for a ledger.")
    p_pause.add_argument("--target", required=True, help="Target ledger markdown path.")
    p_pause.set_defaults(func=cmd_pause)

    p_resume = sub.add_parser("resume", help="Resume Context Anchor injections for a ledger.")
    p_resume.add_argument("--target", required=True, help="Target ledger markdown path.")
    p_resume.set_defaults(func=cmd_resume)

    p_status = sub.add_parser("status", help="Inspect current ledger and state.")
    p_status.add_argument("--artifact-dir", default="", help="Conversation artifact directory.")
    p_status.add_argument("--workspace", default="", help="Workspace directory.")
    p_status.add_argument("--conversation-id", default="", help="Conversation ID.")
    p_status.set_defaults(func=cmd_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
