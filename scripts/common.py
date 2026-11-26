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
    "auto_start": True,
    "skip_subagents": True,
    "auto_detect_after_turns": 6,
    "auto_detect_recheck_turns": 6,
    "reanchor_every_turns": 6,
    "max_lines": 120,
    "max_chars": 4000,
    "max_bytes": 12288,
    "l2_index_max_entries": 40,
    "l2_keyword_hints": True,
    "l2_max_hint_matches": 3,
    "inherit_on_fork": True,
    "fork_max_age_hours": 48,
    "registry_max_entries": 200,
}

DEFAULT_LEDGER_TEMPLATE = """<!-- context-anchor: overwrite-in-place | limit: <=120 lines, <=4000 chars, <=12KB -->
# Memory Ledger

## 1. Goal & Scope
- (State the core objective and boundaries of this conversation/task.)

## 2. Hard Constraints
- (Non-negotiable requirements, dimensions, tolerances, rules, style, or budget limits. Keep high-risk rules here in L1; never demote them solely to L2.)

## 3. Current Consensus
- (Latest confirmed decisions, active version baseline, or plan. Overwrite obsolete values in place; offload bulky detail tables/glossaries to memories/*.md.)

## 4. Rejected & Pitfalls
- (Approaches, parameters, or options explicitly rejected and why, so they are not proposed again.)

## 5. Active Files & Next
- (Key workspace file paths if any, and the immediate next step.)
"""

TEMPLATE_PLACEHOLDER_MARKERS = (
    "(State the core objective and boundaries of this conversation/task.)",
    "(Non-negotiable requirements, dimensions, tolerances, rules, style, or budget limits.",
    "(Latest confirmed decisions, active version baseline, or plan.",
    "(Approaches, parameters, or options explicitly rejected and why, so they are not proposed again.)",
    "(Key workspace file paths if any, and the immediate next step.)",
)

LEGACY_TEMPLATE_PLACEHOLDER_MARKERS = (
    "(State the core objective and boundaries of this conversation/task.)",
    "(Non-negotiable requirements, dimensions, tolerances, rules, style, or budget limits.)",
    "(Latest confirmed decisions, specs, or plan. Overwrite obsolete values in place; never keep superseded versions alongside current ones.)",
    "(Approaches, parameters, or options explicitly rejected and why, so they are not proposed again.)",
    "(Key workspace file paths if any, and the immediate next step.)",
)

REQUIRED_SECTION_KEYWORDS = (
    "goal & scope",
    "hard constraints",
    "current consensus",
    "rejected",
    "next",
)

PAUSED_RE = re.compile(r"<!--\s*context-anchor:\s*paused\s*-->", re.IGNORECASE)
INHERITED_LINE_RE = re.compile(r"^<!--\s*context-anchor:\s*inherited_from=.*?-->\r?\n?", re.MULTILINE)
L2_INDEX_START = "<!-- context-anchor: l2-index:start -->"
L2_INDEX_END = "<!-- context-anchor: l2-index:end -->"
L2_INDEX_BLOCK_RE = re.compile(
    r"\r?\n*<!--\s*context-anchor:\s*l2-index:start\s*-->.*?<!--\s*context-anchor:\s*l2-index:end\s*-->\r?\n*",
    re.DOTALL | re.IGNORECASE,
)
SAFE_SLUG_RE = re.compile(r"[^A-Za-z0-9\u4e00-\u9fff_.-]+")


def get_plugin_root() -> Path:
    return Path(__file__).resolve().parent.parent


def get_data_dir() -> Path:
    env_override = os.environ.get("CONTEXT_ANCHOR_DATA_DIR") or os.environ.get("PLUGIN_DATA")
    if env_override:
        return Path(env_override).expanduser()
    plugin_data_dir = Path.home() / ".gemini" / "antigravity" / "plugin_data" / "context-anchor"
    legacy_dir = Path.home() / ".gemini" / "antigravity" / "context-anchor-state"
    if plugin_data_dir.exists() or not legacy_dir.exists():
        return plugin_data_dir
    return legacy_dir


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
    """Return (line_count, utf8_byte_count) for backward compatibility."""
    if not text:
        return 0, 0
    line_count = len(text.splitlines())
    byte_count = len(text.encode("utf-8"))
    return line_count, byte_count


def measure_ledger_metrics(text: str) -> dict[str, int]:
    """Return line, character, and UTF-8 byte counts for Chinese-fair budgeting."""
    if not text:
        return {"lines": 0, "chars": 0, "bytes": 0}
    return {
        "lines": len(text.splitlines()),
        "chars": len(text),
        "bytes": len(text.encode("utf-8")),
    }


def is_paused(text: str) -> bool:
    return bool(PAUSED_RE.search(text))


def is_template_only(text: str) -> bool:
    if not text or not text.strip():
        return True
    cleaned = INHERITED_LINE_RE.sub("", text)
    cleaned = PAUSED_RE.sub("", cleaned).strip()
    if cleaned == DEFAULT_LEDGER_TEMPLATE.strip():
        return True
    for marker_set in (TEMPLATE_PLACEHOLDER_MARKERS, LEGACY_TEMPLATE_PLACEHOLDER_MARKERS):
        if all(marker in cleaned for marker in marker_set):
            non_empty = [ln.strip() for ln in cleaned.splitlines() if ln.strip() and not ln.strip().startswith("<!--")]
            tpl_non_empty = [
                ln.strip()
                for ln in DEFAULT_LEDGER_TEMPLATE.splitlines()
                if ln.strip() and not ln.strip().startswith("<!--")
            ]
            if len(non_empty) <= len(tpl_non_empty):
                return True
    return False


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


# ---------------------------------------------------------------------------
# L2 Topic Memories (`memories/*.md`) — Zero-Dependency Frontmatter & Indexing
# ---------------------------------------------------------------------------

def resolve_memories_dir(ledger_path: Path) -> Path:
    return ledger_path.parent / "memories"


def sanitize_memory_slug(name: str) -> str:
    raw = name.strip()
    if raw.lower().endswith(".md"):
        raw = raw[:-3]
    cleaned = SAFE_SLUG_RE.sub("_", raw).strip("._-")
    return cleaned or "memory"


def _unquote(value: str) -> str:
    v = value.strip()
    if len(v) >= 2 and ((v[0] == '"' and v[-1] == '"') or (v[0] == "'" and v[-1] == "'")):
        return v[1:-1].strip()
    return v


def _parse_keyword_list(raw: str) -> list[str]:
    v = raw.strip()
    if not v:
        return []
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        if not inner:
            return []
        parts = re.split(r"[,，;；]", inner)
    else:
        parts = re.split(r"[,，;；]", v)
    out: list[str] = []
    seen: set[str] = set()
    for part in parts:
        kw = _unquote(part)
        if kw and kw.lower() not in seen:
            seen.add(kw.lower())
            out.append(kw)
    return out


def parse_memory_card(path: Path) -> dict[str, Any]:
    """Parse an L2 memory markdown file and its optional YAML frontmatter."""
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    lines = text.splitlines()
    meta: dict[str, Any] = {}
    body_lines = lines

    if lines and lines[0].strip() == "---":
        end_idx = None
        for idx in range(1, len(lines)):
            if lines[idx].strip() == "---":
                end_idx = idx
                break
        if end_idx is not None:
            fm_lines = lines[1:end_idx]
            body_lines = lines[end_idx + 1 :]
            current_list_key: str | None = None
            for fl in fm_lines:
                stripped = fl.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if stripped.startswith("- ") and current_list_key == "keywords":
                    kw = _unquote(stripped[2:])
                    if kw:
                        meta.setdefault("keywords", []).append(kw)
                    continue
                if ":" in fl:
                    k, v = fl.split(":", 1)
                    key = k.strip().lower()
                    val = v.strip()
                    if key == "keywords":
                        parsed_kws = _parse_keyword_list(val)
                        meta["keywords"] = parsed_kws
                        current_list_key = "keywords" if not parsed_kws and not val else None
                    else:
                        meta[key] = _unquote(val)
                        current_list_key = None

    title = str(meta.get("title") or "").strip()
    if not title:
        for bl in body_lines:
            s = bl.strip()
            if s.startswith("#"):
                title = s.lstrip("#").strip()
                break
    if not title:
        title = path.stem

    summary = str(meta.get("summary") or "").strip()
    if not summary:
        for bl in body_lines:
            s = bl.strip()
            if s and not s.startswith("#") and not s.startswith("<!--"):
                summary = s.lstrip("-* ").strip()[:100]
                break

    updated = str(meta.get("updated") or "").strip()
    if not updated:
        try:
            updated = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%d")
        except Exception:
            updated = ""

    raw_kws = meta.get("keywords")
    keywords: list[str] = []
    if isinstance(raw_kws, list):
        seen_k: set[str] = set()
        for item in raw_kws:
            kw = _unquote(str(item))
            if kw and kw.lower() not in seen_k:
                seen_k.add(kw.lower())
                keywords.append(kw)

    metrics = measure_ledger_metrics(text)
    return {
        "filename": path.name,
        "rel_path": f"memories/{path.name}",
        "path": str(path),
        "title": title,
        "trigger": str(meta.get("trigger") or "").strip(),
        "keywords": keywords,
        "updated": updated,
        "summary": summary,
        "lines": metrics["lines"],
        "chars": metrics["chars"],
        "bytes": metrics["bytes"],
    }


def scan_l2_memories(ledger_path: Path) -> list[dict[str, Any]]:
    mem_dir = resolve_memories_dir(ledger_path)
    if not mem_dir.is_dir():
        return []
    cards: list[dict[str, Any]] = []
    for md_file in sorted(mem_dir.glob("*.md"), key=lambda p: p.name.lower()):
        if not md_file.is_file():
            continue
        try:
            cards.append(parse_memory_card(md_file))
        except Exception:
            continue
    return cards


def format_memory_card(
    title: str,
    body: str,
    trigger: str = "",
    keywords: list[str] | None = None,
    summary: str = "",
    updated: str = "",
) -> str:
    clean_kws = [k.strip() for k in (keywords or []) if k and k.strip()]
    date_str = updated.strip() or datetime.now().strftime("%Y-%m-%d")
    kw_line = ", ".join(clean_kws)
    fm_lines = [
        "---",
        f"title: {title.strip()}",
        f"trigger: {trigger.strip()}",
        f"keywords: [{kw_line}]",
        f"updated: {date_str}",
        f"summary: {summary.strip()}",
        "---",
        "",
    ]
    clean_body = body.strip()
    if not clean_body:
        clean_body = f"# {title.strip()}\n"
    return "\n".join(fm_lines) + clean_body + "\n"


def _escape_md_cell(value: str, max_len: int = 120) -> str:
    flat = re.sub(r"\s+", " ", value.strip()).replace("|", "\\|")
    if max_len > 0 and len(flat) > max_len:
        return flat[: max_len - 1].rstrip() + "…"
    return flat or "—"


def render_l2_index_block(cards: list[dict[str, Any]], max_entries: int = 40) -> str:
    if not cards:
        return ""
    limit = max(1, int(max_entries)) if max_entries and max_entries > 0 else len(cards)
    shown = cards[:limit]
    rows = [
        L2_INDEX_START,
        "## 6. L2 Memory Index (Read on Demand)",
        "| File | Read Before (Trigger) | Summary | Updated |",
        "| :--- | :--- | :--- | :--- |",
    ]
    for card in shown:
        file_col = f"`{card['rel_path']}`"
        trigger_col = _escape_md_cell(card.get("trigger") or card.get("title") or "", max_len=80)
        summary_col = _escape_md_cell(card.get("summary") or card.get("title") or "", max_len=100)
        updated_col = _escape_md_cell(card.get("updated") or "", max_len=20)
        rows.append(f"| {file_col} | {trigger_col} | {summary_col} | {updated_col} |")
    if len(cards) > len(shown):
        rows.append(
            f"> Showing {len(shown)}/{len(cards)} L2 memory cards (limit={limit}). "
            "Merge or archive stale cards under `memories/`."
        )
    rows.append(L2_INDEX_END)
    return "\n".join(rows)


def apply_l2_index_to_text(ledger_text: str, cards: list[dict[str, Any]], max_entries: int = 40) -> str:
    rendered = render_l2_index_block(cards, max_entries=max_entries)
    if L2_INDEX_BLOCK_RE.search(ledger_text):
        if not rendered:
            cleaned = L2_INDEX_BLOCK_RE.sub("\n", ledger_text).rstrip() + "\n"
            return cleaned
        updated = L2_INDEX_BLOCK_RE.sub(lambda _: "\n\n" + rendered + "\n", ledger_text, count=1)
        return updated.rstrip() + "\n"
    if not rendered:
        return ledger_text
    base = ledger_text.rstrip()
    return (base + "\n\n" + rendered + "\n") if base else (rendered + "\n")


def sync_l2_index(ledger_path: Path, max_entries: int = 40) -> dict[str, Any]:
    if not ledger_path.is_file():
        raise FileNotFoundError(f"Ledger not found: {ledger_path}")
    cards = scan_l2_memories(ledger_path)
    original = ledger_path.read_text(encoding="utf-8-sig")
    updated = apply_l2_index_to_text(original, cards, max_entries=max_entries)
    changed = updated != original
    if changed:
        atomic_write_text(ledger_path, updated)
    return {
        "path": str(ledger_path),
        "l2_count": len(cards),
        "shown": min(len(cards), max_entries) if max_entries > 0 else len(cards),
        "truncated": max_entries > 0 and len(cards) > max_entries,
        "changed": changed,
    }


def is_l2_index_in_sync(ledger_path: Path, max_entries: int = 40) -> bool:
    if not ledger_path.is_file():
        return False
    cards = scan_l2_memories(ledger_path)
    original = ledger_path.read_text(encoding="utf-8-sig")
    expected = apply_l2_index_to_text(original, cards, max_entries=max_entries)
    return expected == original


def _keyword_matches_text(keyword: str, user_text: str, lowered_user_text: str) -> bool:
    kw = keyword.strip()
    if len(kw) < 2:
        return False
    if kw.isascii():
        pattern = r"(?<![A-Za-z0-9])" + re.escape(kw.lower()) + r"(?![A-Za-z0-9])"
        return bool(re.search(pattern, lowered_user_text))
    return kw.lower() in lowered_user_text


def match_l2_memories(
    cards: list[dict[str, Any]],
    user_input: str,
    max_matches: int = 3,
) -> list[dict[str, Any]]:
    """Lightweight deterministic keyword matching (Chinese-native, zero external deps)."""
    if not cards or not user_input or not user_input.strip():
        return []
    lowered = user_input.lower()
    scored: list[tuple[int, int, str, dict[str, Any]]] = []
    for card in cards:
        kws = card.get("keywords") or []
        hits = [kw for kw in kws if _keyword_matches_text(str(kw), user_input, lowered)]
        if hits:
            max_kw_len = max(len(str(h)) for h in hits)
            enriched = dict(card)
            enriched["matched_keywords"] = hits
            scored.append((len(hits), max_kw_len, card["filename"].lower(), enriched))
    if not scored:
        return []
    scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
    limit = max(1, int(max_matches)) if max_matches and max_matches > 0 else len(scored)
    return [item[3] for item in scored[:limit]]


def copy_memories_dir(src_ledger: Path, dst_ledger: Path) -> int:
    """Copy all L2 `memories/*.md` cards alongside a ledger copy; returns copied count."""
    src_dir = resolve_memories_dir(src_ledger)
    if not src_dir.is_dir():
        return 0
    dst_dir = resolve_memories_dir(dst_ledger)
    copied = 0
    for md_file in sorted(src_dir.glob("*.md"), key=lambda p: p.name.lower()):
        if not md_file.is_file():
            continue
        try:
            content = md_file.read_text(encoding="utf-8-sig")
            atomic_write_text(dst_dir / md_file.name, content)
            copied += 1
        except Exception:
            continue
    return copied


# ---------------------------------------------------------------------------
# Transcript, Registry, Fork Detection & Ledger Resolution
# ---------------------------------------------------------------------------

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

    if isinstance(first_idx, int) and first_idx >= 2:
        return True
    if total_lines == 0 and init_steps >= 3:
        return True
    if init_steps > 0 and total_lines > 0 and (init_steps - total_lines) >= 3 and (first_idx or 0) > 0:
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
    require_ledger: bool = False,
    max_age_hours: int | float = 48,
    current_first_step: int | None = None,
) -> dict[str, Any] | None:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=float(max_age_hours)) if max_age_hours and max_age_hours > 0 else None

    candidates: list[tuple[dict[str, Any], Path | None, bool]] = []
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
        substantive = False
        if resolved_ledger is not None:
            try:
                text = resolved_ledger.read_text(encoding="utf-8-sig")
                substantive = bool(text) and not is_template_only(text) and not is_paused(text)
            except Exception:
                substantive = False
        if require_ledger and not substantive:
            continue
        candidates.append((item, resolved_ledger, substantive))

    if not candidates:
        return None

    fork_point = current_first_step if isinstance(current_first_step, int) and current_first_step > 0 else current_step_ceiling

    def _sort_key(pair: tuple[dict[str, Any], Path | None, bool]) -> tuple[int, int, int, int, int, str]:
        item, _, _ = pair
        step = int(item.get("lastStepIndex") if isinstance(item.get("lastStepIndex"), int) else -1)
        item_first = item.get("firstStepIndex")
        origin_ok = 1 if (not isinstance(item_first, int) or fork_point <= 0 or item_first < fork_point) else 0
        reachable = 1 if (fork_point <= 0 or step >= fork_point - 10) else 0
        not_forked = 0 if item.get("isForked") else 1
        exact_pre_fork = 1 if (fork_point > 0 and (fork_point - 10) <= step <= fork_point) else 0
        neg_distance = -abs(step - fork_point) if (fork_point > 0 and step >= 0) else 0
        return (origin_ok, reachable, not_forked, exact_pre_fork, neg_distance, str(item.get("updatedAt") or ""))

    candidates.sort(key=_sort_key, reverse=True)
    best_item, best_ledger, best_substantive = candidates[0]
    enriched = dict(best_item)
    if best_ledger is not None and best_substantive:
        enriched["ledgerPath"] = str(best_ledger)
        enriched["hasLedger"] = True
    else:
        enriched["ledgerPath"] = ""
        enriched["hasLedger"] = False
    return enriched


def copy_ledger_on_fork(src_ledger: Path, dst_ledger: Path, parent_id: str) -> str:
    raw = src_ledger.read_text(encoding="utf-8-sig")
    body = INHERITED_LINE_RE.sub("", raw).lstrip("\r\n")
    marker = f"<!-- context-anchor: inherited_from={parent_id} -->\n"
    new_text = marker + body
    atomic_write_text(dst_ledger, new_text)
    copy_memories_dir(src_ledger, dst_ledger)
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
