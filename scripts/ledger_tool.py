"""CLI management helper for Context Anchor L1 ledgers and L2 topic memories."""

from __future__ import annotations

import argparse
import json
import re
import shutil
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
    copy_memories_dir,
    format_memory_card,
    get_data_dir,
    get_plugin_root,
    is_l2_index_in_sync,
    is_paused,
    load_config,
    load_state,
    measure_ledger_metrics,
    resolve_ledger_paths,
    resolve_memories_dir,
    sanitize_memory_slug,
    scan_l2_memories,
    sync_l2_index,
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
    max_chars = int(args.max_chars or cfg.get("max_chars") or 4000)
    max_bytes = int(args.max_bytes or cfg["max_bytes"])
    max_l2 = int(cfg.get("l2_index_max_entries") or 40)

    text = target.read_text(encoding="utf-8-sig")
    metrics = measure_ledger_metrics(text)
    lowered = text.lower()
    missing_sections = [kw for kw in REQUIRED_SECTION_KEYWORDS if kw not in lowered]
    l2_cards = scan_l2_memories(target)
    l2_in_sync = is_l2_index_in_sync(target, max_entries=max_l2)

    over_lines = metrics["lines"] > max_lines
    over_chars = metrics["chars"] > max_chars
    over_bytes = metrics["bytes"] > max_bytes
    warn = over_lines or over_chars or over_bytes or bool(missing_sections) or (bool(l2_cards) and not l2_in_sync)

    report = {
        "status": "warn" if warn else "ok",
        "path": str(target),
        "lines": metrics["lines"],
        "max_lines": max_lines,
        "chars": metrics["chars"],
        "max_chars": max_chars,
        "bytes": metrics["bytes"],
        "max_bytes": max_bytes,
        "paused": is_paused(text),
        "missing_sections": missing_sections,
        "l2_count": len(l2_cards),
        "l2_index_in_sync": l2_in_sync,
    }
    print(json.dumps(report, ensure_ascii=False))
    return 1 if warn else 0


def cmd_pin(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).expanduser()
    project_ledger = workspace / ".agents" / "context-anchor" / "memory_ledger.md"
    copied_memories = 0

    if args.session_ledger:
        src = Path(args.session_ledger).expanduser()
        if not src.is_file():
            print(json.dumps({"status": "error", "reason": f"Session ledger not found: {src}"}, ensure_ascii=False))
            return 2
        content = src.read_text(encoding="utf-8-sig")
        atomic_write_text(project_ledger, content)
        copied_memories = copy_memories_dir(src, project_ledger)
    else:
        atomic_write_text(project_ledger, DEFAULT_LEDGER_TEMPLATE)

    print(
        json.dumps(
            {
                "status": "pinned",
                "path": str(project_ledger),
                "memories_copied": copied_memories,
            },
            ensure_ascii=False,
        )
    )
    return 0


def cmd_unpin(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).expanduser()
    project_ledger = workspace / ".agents" / "context-anchor" / "memory_ledger.md"
    if not project_ledger.is_file():
        print(json.dumps({"status": "not_pinned", "path": str(project_ledger)}, ensure_ascii=False))
        return 0

    copied_memories = 0
    if args.session_ledger:
        dst = Path(args.session_ledger).expanduser()
        content = project_ledger.read_text(encoding="utf-8-sig")
        atomic_write_text(dst, content)
        copied_memories = copy_memories_dir(project_ledger, dst)

    project_ledger.unlink()
    proj_mem_dir = resolve_memories_dir(project_ledger)
    if proj_mem_dir.is_dir():
        shutil.rmtree(proj_mem_dir, ignore_errors=True)

    print(
        json.dumps(
            {
                "status": "unpinned",
                "removed": str(project_ledger),
                "memories_copied_back": copied_memories,
            },
            ensure_ascii=False,
        )
    )
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


def cmd_sync_index(args: argparse.Namespace) -> int:
    target = Path(args.target).expanduser()
    if not target.is_file():
        print(json.dumps({"status": "missing", "path": str(target)}, ensure_ascii=False))
        return 2
    cfg = load_config(get_plugin_root(), get_data_dir())
    max_entries = int(args.max_entries or cfg.get("l2_index_max_entries") or 40)
    res = sync_l2_index(target, max_entries=max_entries)
    res["status"] = "synced"
    print(json.dumps(res, ensure_ascii=False))
    return 0


def cmd_memory_save(args: argparse.Namespace) -> int:
    target = Path(args.target).expanduser()
    if not target.is_file():
        atomic_write_text(target, DEFAULT_LEDGER_TEMPLATE)

    slug = sanitize_memory_slug(args.name)
    mem_dir = resolve_memories_dir(target)
    card_path = mem_dir / f"{slug}.md"

    if args.body_file:
        bf = Path(args.body_file).expanduser()
        if not bf.is_file():
            print(json.dumps({"status": "error", "reason": f"Body file not found: {bf}"}, ensure_ascii=False))
            return 2
        body = bf.read_text(encoding="utf-8-sig")
    else:
        body = args.body or ""

    keywords = [k.strip() for k in re.split(r"[,，;；]", args.keywords or "") if k.strip()]
    card_text = format_memory_card(
        title=args.title or slug,
        body=body,
        trigger=args.trigger or "",
        keywords=keywords,
        summary=args.summary or "",
        updated=args.updated or "",
    )
    atomic_write_text(card_path, card_text)

    cfg = load_config(get_plugin_root(), get_data_dir())
    sync_res = sync_l2_index(target, max_entries=int(cfg.get("l2_index_max_entries") or 40))
    print(
        json.dumps(
            {
                "status": "saved",
                "memory_path": str(card_path),
                "rel_path": f"memories/{card_path.name}",
                "l2_count": sync_res["l2_count"],
                "index_synced": True,
            },
            ensure_ascii=False,
        )
    )
    return 0


def cmd_memory_list(args: argparse.Namespace) -> int:
    target = Path(args.target).expanduser()
    if not target.is_file():
        print(json.dumps({"status": "missing", "path": str(target)}, ensure_ascii=False))
        return 2
    cfg = load_config(get_plugin_root(), get_data_dir())
    max_entries = int(cfg.get("l2_index_max_entries") or 40)
    cards = scan_l2_memories(target)
    print(
        json.dumps(
            {
                "status": "ok",
                "ledger_path": str(target),
                "memories_dir": str(resolve_memories_dir(target)),
                "l2_count": len(cards),
                "l2_index_in_sync": is_l2_index_in_sync(target, max_entries=max_entries),
                "memories": cards,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def cmd_memory_delete(args: argparse.Namespace) -> int:
    target = Path(args.target).expanduser()
    if not target.is_file():
        print(json.dumps({"status": "missing", "path": str(target)}, ensure_ascii=False))
        return 2
    slug = sanitize_memory_slug(args.name)
    card_path = resolve_memories_dir(target) / f"{slug}.md"
    if not card_path.is_file():
        print(json.dumps({"status": "not_found", "memory_path": str(card_path)}, ensure_ascii=False))
        return 2
    card_path.unlink()
    cfg = load_config(get_plugin_root(), get_data_dir())
    sync_res = sync_l2_index(target, max_entries=int(cfg.get("l2_index_max_entries") or 40))
    print(
        json.dumps(
            {
                "status": "deleted",
                "removed": str(card_path),
                "l2_count": sync_res["l2_count"],
                "index_synced": True,
            },
            ensure_ascii=False,
        )
    )
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
        cfg = load_config(get_plugin_root(), get_data_dir())
        text = active_path.read_text(encoding="utf-8-sig")
        metrics = measure_ledger_metrics(text)
        cards = scan_l2_memories(active_path)
        result["lines"] = metrics["lines"]
        result["chars"] = metrics["chars"]
        result["bytes"] = metrics["bytes"]
        result["paused"] = is_paused(text)
        result["l2_dir"] = str(resolve_memories_dir(active_path))
        result["l2_count"] = len(cards)
        result["l2_files"] = [c["rel_path"] for c in cards]
        result["l2_index_in_sync"] = is_l2_index_in_sync(
            active_path,
            max_entries=int(cfg.get("l2_index_max_entries") or 40),
        )

    if args.conversation_id:
        state = load_state(get_data_dir(), args.conversation_id)
        if state:
            result["state"] = {
                "last_injected_turn": state.get("last_injected_turn"),
                "last_auto_detect_turn": state.get("last_auto_detect_turn"),
                "last_l2_hint_turn": state.get("last_l2_hint_turn"),
                "inherited_from": state.get("inherited_from"),
                "inherited_base_turns": state.get("inherited_base_turns"),
            }

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Context Anchor ledger and L2 memory management tool.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Create a new ledger from template.")
    p_init.add_argument("--target", required=True, help="Target ledger markdown path.")
    p_init.add_argument("--force", action="store_true", help="Overwrite if already exists.")
    p_init.set_defaults(func=cmd_init)

    p_check = sub.add_parser("check", help="Validate ledger size, required sections, and L2 index sync.")
    p_check.add_argument("--target", required=True, help="Target ledger markdown path.")
    p_check.add_argument("--max-lines", type=int, default=0, help="Override max line count.")
    p_check.add_argument("--max-chars", type=int, default=0, help="Override max character count.")
    p_check.add_argument("--max-bytes", type=int, default=0, help="Override max byte count.")
    p_check.set_defaults(func=cmd_check)

    p_pin = sub.add_parser("pin", help="Pin ledger (and memories/) into workspace .agents/context-anchor/.")
    p_pin.add_argument("--workspace", required=True, help="Workspace directory.")
    p_pin.add_argument("--session-ledger", default="", help="Optional existing session ledger to copy.")
    p_pin.set_defaults(func=cmd_pin)

    p_unpin = sub.add_parser("unpin", help="Unpin workspace ledger (and memories/).")
    p_unpin.add_argument("--workspace", required=True, help="Workspace directory.")
    p_unpin.add_argument("--session-ledger", default="", help="Optional session ledger path to copy back to.")
    p_unpin.set_defaults(func=cmd_unpin)

    p_pause = sub.add_parser("pause", help="Pause Context Anchor injections for a ledger.")
    p_pause.add_argument("--target", required=True, help="Target ledger markdown path.")
    p_pause.set_defaults(func=cmd_pause)

    p_resume = sub.add_parser("resume", help="Resume Context Anchor injections for a ledger.")
    p_resume.add_argument("--target", required=True, help="Target ledger markdown path.")
    p_resume.set_defaults(func=cmd_resume)

    p_sync = sub.add_parser("sync-index", help="Regenerate the L2 memory index table inside memory_ledger.md.")
    p_sync.add_argument("--target", required=True, help="Target ledger markdown path.")
    p_sync.add_argument("--max-entries", type=int, default=0, help="Override max L2 index entries.")
    p_sync.set_defaults(func=cmd_sync_index)

    p_msave = sub.add_parser("memory-save", help="Create or overwrite an L2 memory card and sync L1 index.")
    p_msave.add_argument("--target", required=True, help="Target L1 ledger markdown path.")
    p_msave.add_argument("--name", required=True, help="Memory card slug/filename (e.g. api_contract).")
    p_msave.add_argument("--title", default="", help="Human-readable card title.")
    p_msave.add_argument("--trigger", default="", help="When the agent must read this card.")
    p_msave.add_argument("--keywords", default="", help="Comma-separated trigger keywords for PreInvocation hints.")
    p_msave.add_argument("--summary", default="", help="One-line summary for L1 index table.")
    p_msave.add_argument("--updated", default="", help="Optional YYYY-MM-DD date override.")
    p_msave.add_argument("--body", default="", help="Markdown body content.")
    p_msave.add_argument("--body-file", default="", help="Optional path to file containing Markdown body.")
    p_msave.set_defaults(func=cmd_memory_save)

    p_mlist = sub.add_parser("memory-list", help="List all L2 memory cards alongside a ledger.")
    p_mlist.add_argument("--target", required=True, help="Target L1 ledger markdown path.")
    p_mlist.set_defaults(func=cmd_memory_list)

    p_mdel = sub.add_parser("memory-delete", help="Delete an obsolete L2 memory card and re-sync L1 index.")
    p_mdel.add_argument("--target", required=True, help="Target L1 ledger markdown path.")
    p_mdel.add_argument("--name", required=True, help="Memory card slug/filename to delete.")
    p_mdel.set_defaults(func=cmd_memory_delete)

    p_status = sub.add_parser("status", help="Inspect current ledger, L2 memories, and state.")
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
