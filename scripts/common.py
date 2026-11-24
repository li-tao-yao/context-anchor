"""Shared utilities for the Context Anchor Antigravity plugin."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DEFAULT_CONFIG: dict[str, Any] = {
    "auto_detect_after_turns": 6,
    "auto_detect_recheck_turns": 6,
    "reanchor_every_turns": 6,
    "max_lines": 120,
    "max_bytes": 6144,
    "inherit_on_fork": True,
    "fork_max_age_hours": 48,
    "registry_max_entries": 200,
}

DEFAULT_LEDGER_TEMPLATE = """<!-- context-anchor: overwrite-in-place | limit: <=120 lines, <=6KB -->
# Memory Ledger

## 1. Goal & Scope
- (State the core objective and boundaries of this conversation/task.)

## 2. Hard Constraints
- (Non-negotiable requirements, dimensions, tolerances, rules, style, or budget limits.)

## 3. Current Consensus
- (Latest confirmed decisions, specs, or plan. Overwrite obsolete values in place; never keep superseded versions alongside current ones.)

## 4. Rejected & Pitfalls
- (Approaches, parameters, or options explicitly rejected and why, so they are not proposed again.)

## 5. Active Files & Next
- (Key workspace file paths if any, and the immediate next step.)
"""

REQUIRED_SECTION_KEYWORDS = (
    "goal & scope",
    "hard constraints",
    "current consensus",
    "rejected",
    "next",
)

PAUSED_RE = re.compile(r"<!--\s*context-anchor:\s*paused\s*-->", re.IGNORECASE)
INHERITED_LINE_RE = re.compile(r"^<!--\s*context-anchor:\s*inherited_from=.*?-->\r?\n?", re.MULTILINE)


def get_plugin_root() -> Path:
    return Path(__file__).resolve().parent.parent


def get_data_dir() -> Path:
    env_override = os.environ.get("CONTEXT_ANCHOR_DATA_DIR") or os.environ.get("PLUGIN_DATA")
    if env_override:
        return Path(env_override).expanduser()
    return Path.home() / ".gemini" / "antigravity" / "context-anchor-state"


def _read_json_file(path: Path) -> dict[str, Any] | None:
    try:
        if not path.is_file():
            return None
        raw = path.read_text(encoding="utf-8-sig")
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def load_config(plugin_root: Path | None = None, data_dir: Path | None = None) -> dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    root = plugin_root or get_plugin_root()
    default_file = _read_json_file(root / "config.default.json")
    if default_file:
        cfg.update(default_file)
    if data_dir is not None:
        user_file = _read_json_file(data_dir / "config.json")
        if user_file:
            cfg.update(user_file)
    return cfg


def normalize_workspace_key(workspace_paths: Any) -> str:
    if isinstance(workspace_paths, list):
        for item in workspace_paths:
            if isinstance(item, str) and item.strip():
                return os.path.normcase(os.path.normpath(item.strip())).replace("\\", "/")
    return "__no_workspace__"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def measure_ledger(text: str) -> tuple[int, int]:
    if not text:
        return 0, 0
    line_count = len(text.splitlines())
    byte_count = len(text.encode("utf-8"))
    return line_count, byte_count


def is_paused(text: str) -> bool:
    return bool(PAUSED_RE.search(text))


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp_path, path)
    except Exception:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass
        raise


def atomic_write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def parse_transcript(transcript_path: str | Path | None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "exists": False,
        "total_lines": 0,
        "first_step_index": None,
        "max_step_index": -1,
        "user_turns": 0,
        "checkpoint_count": 0,
        "max_checkpoint_step": -1,
    }
    if not transcript_path:
        return result
    path = Path(str(transcript_path)).expanduser()
    if not path.is_file():
        return result

    result["exists"] = True
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if not isinstance(obj, dict):
                    continue

                result["total_lines"] += 1
                step_idx = obj.get("step_index")
                if isinstance(step_idx, int):
                    if result["first_step_index"] is None:
                        result["first_step_index"] = step_idx
                    if step_idx > result["max_step_index"]:
                        result["max_step_index"] = step_idx

                step_type = obj.get("type")
                if step_type == "USER_INPUT":
                    result["user_turns"] += 1
                elif step_type == "CHECKPOINT":
                    result["checkpoint_count"] += 1
                    if isinstance(step_idx, int) and step_idx > result["max_checkpoint_step"]:
                        result["max_checkpoint_step"] = step_idx
    except Exception:
        pass
    return result


def detect_fork(transcript_info: dict[str, Any], initial_num_steps: int = 0) -> bool:
    first_idx = transcript_info.get("first_step_index")
    total_lines = int(transcript_info.get("total_lines") or 0)
    init_steps = int(initial_num_steps or 0)

    if isinstance(first_idx, int) and first_idx >= 3:
        return True
    if init_steps > 0 and total_lines > 0 and (init_steps - total_lines) >= 5 and (first_idx or 0) > 0:
        return True
    return False


def _safe_conv_id(conversation_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", conversation_id)
    return cleaned or hashlib.sha256(conversation_id.encode("utf-8")).hexdigest()[:16]


def load_state(data_dir: Path, conversation_id: str) -> dict[str, Any]:
    path = data_dir / "conversations" / f"{_safe_conv_id(conversation_id)}.json"
    data = _read_json_file(path)
    return data if data is not None else {}


def save_state(data_dir: Path, conversation_id: str, state: dict[str, Any]) -> None:
    path = data_dir / "conversations" / f"{_safe_conv_id(conversation_id)}.json"
    atomic_write_json(path, state)


def load_registry(data_dir: Path) -> dict[str, Any]:
    path = data_dir / "registry.json"
    data = _read_json_file(path)
    if data and isinstance(data.get("conversations"), list):
        return data
    return {"conversations": []}


def _parse_iso_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def update_registry(
    data_dir: Path,
    entry: dict[str, Any],
    max_entries: int = 200,
) -> dict[str, Any]:
    registry = load_registry(data_dir)
    conv_id = entry.get("conversationId")
    items = [
        item
        for item in registry.get("conversations", [])
        if isinstance(item, dict) and item.get("conversationId") != conv_id
    ]
    stamped = dict(entry)
    stamped.setdefault("updatedAt", datetime.now(timezone.utc).isoformat())
    items.append(stamped)
    items.sort(key=lambda item: str(item.get("updatedAt") or ""), reverse=True)
    if max_entries > 0:
        items = items[:max_entries]
    updated = {"conversations": items}
    atomic_write_json(data_dir / "registry.json", updated)
    return updated


def _resolve_candidate_ledger(entry: dict[str, Any]) -> Path | None:
    ledger_path = entry.get("ledgerPath")
    if isinstance(ledger_path, str) and ledger_path:
        p = Path(ledger_path)
        if p.is_file():
            return p
    artifact_dir = entry.get("artifactDir")
    if isinstance(artifact_dir, str) and artifact_dir:
        candidate = Path(artifact_dir) / "memory_ledger.md"
        if candidate.is_file():
            return candidate
    return None


def find_fork_parent(
    registry: dict[str, Any],
    conversation_id: str,
    workspace_key: str,
    current_step_ceiling: int,
    require_ledger: bool = True,
    max_age_hours: int | float = 48,
) -> dict[str, Any] | None:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=float(max_age_hours)) if max_age_hours and max_age_hours > 0 else None

    candidates: list[tuple[dict[str, Any], Path | None]] = []
    for item in registry.get("conversations", []):
        if not isinstance(item, dict):
            continue
        if item.get("conversationId") == conversation_id:
            continue
        if item.get("workspaceKey") != workspace_key:
            continue
        dt = _parse_iso_dt(item.get("updatedAt"))
        if cutoff is not None and (dt is None or dt < cutoff):
            continue
        resolved_ledger = _resolve_candidate_ledger(item)
        if require_ledger and resolved_ledger is None:
            continue
        candidates.append((item, resolved_ledger))

    if not candidates:
        return None

    def _sort_key(pair: tuple[dict[str, Any], Path | None]) -> tuple[int, str, int]:
        item, _ = pair
        step = int(item.get("lastStepIndex") if isinstance(item.get("lastStepIndex"), int) else -1)
        step_ok = 1 if (current_step_ceiling <= 0 or step <= current_step_ceiling) else 0
        return (step_ok, str(item.get("updatedAt") or ""), step)

    candidates.sort(key=_sort_key, reverse=True)
    best_item, best_ledger = candidates[0]
    enriched = dict(best_item)
    if best_ledger is not None:
        enriched["ledgerPath"] = str(best_ledger)
        enriched["hasLedger"] = True
    return enriched


def copy_ledger_on_fork(src_ledger: Path, dst_ledger: Path, parent_id: str) -> str:
    raw = src_ledger.read_text(encoding="utf-8-sig")
    body = INHERITED_LINE_RE.sub("", raw).lstrip("\r\n")
    marker = f"<!-- context-anchor: inherited_from={parent_id} -->\n"
    new_text = marker + body
    atomic_write_text(dst_ledger, new_text)
    return new_text


def resolve_ledger_paths(artifact_dir: str | Path | None, workspace_paths: Any) -> dict[str, Path | None]:
    project_ledger: Path | None = None
    if isinstance(workspace_paths, list):
        for item in workspace_paths:
            if isinstance(item, str) and item.strip():
                project_ledger = Path(item.strip()) / ".agents" / "context-anchor" / "memory_ledger.md"
                break

    session_ledger: Path | None = None
    if artifact_dir:
        art_str = str(artifact_dir).strip()
        if art_str:
            session_ledger = Path(art_str).expanduser() / "memory_ledger.md"

    return {"project": project_ledger, "session": session_ledger}
