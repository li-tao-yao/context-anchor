"""Antigravity PreInvocation hook for Context Anchor."""

from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from common import (  # noqa: E402
    copy_ledger_on_fork,
    detect_fork,
    find_fork_parent,
    get_data_dir,
    get_plugin_root,
    is_paused,
    load_config,
    load_registry,
    load_state,
    measure_ledger,
    normalize_workspace_key,
    parse_transcript,
    resolve_ledger_paths,
    save_state,
    sha256_text,
    update_registry,
)


def _build_ledger_injection(
    ledger_text: str,
    ledger_path: Path,
    mode: str,
    triggers: list[str],
    line_count: int,
    byte_count: int,
    config: dict[str, Any],
    inherited_from: str | None = None,
) -> str:
    max_lines = int(config["max_lines"])
    max_bytes = int(config["max_bytes"])
    lines = [
        f"[context-anchor | mode={mode} | triggers={','.join(triggers)} | path={ledger_path}]",
    ]
    if "inherited" in triggers and inherited_from:
        lines.append(
            f"Note: Inherited working memory ledger from parent conversation ({inherited_from}). "
            "Continue updating this copy in place as the conversation evolves."
        )
    if line_count > max_lines or byte_count > max_bytes:
        lines.append(
            f"WARNING: Ledger exceeds limit ({line_count}/{max_lines} lines, {byte_count}/{max_bytes} bytes). "
            "Compact and merge redundant bullets in place before appending new items."
        )
    lines.append("")
    lines.append(ledger_text.strip())
    lines.append("")
    lines.append(
        "Discipline: When confirmed goals, hard constraints, latest consensus, rejected options, "
        "or next steps change, update this ledger IN PLACE (overwrite obsolete values) before finalizing."
    )
    return "\n".join(lines)


def _build_auto_detect_injection(
    target_path: Path,
    effective_turns: int,
    config: dict[str, Any],
) -> str:
    return (
        f"[context-anchor | auto-detect | turn={effective_turns}]\n"
        f"This conversation has reached {effective_turns} user turns without an active memory ledger.\n"
        "Silently evaluate the conversation so far:\n"
        "- If durable hard constraints, confirmed specs/decisions, rejected approaches, or multi-step tasks exist "
        "that should not be forgotten across long turns or compaction, create and populate the 5-section working "
        f"memory ledger at `{target_path}` (keep <= {config['max_lines']} lines / {config['max_bytes']} bytes; "
        "overwrite in place; never store secrets).\n"
        "- If this is casual Q&A or short one-off help with nothing durable to preserve, do NOTHING and do NOT "
        "mention this check to the user."
    )


def process_hook(
    payload: dict[str, Any],
    plugin_root: Path | None = None,
    data_dir: Path | None = None,
) -> dict[str, Any]:
    conversation_id = payload.get("conversationId")
    if not isinstance(conversation_id, str) or not conversation_id.strip():
        return {}
    conversation_id = conversation_id.strip()

    root = plugin_root or get_plugin_root()
    state_dir = data_dir or get_data_dir()
    config = load_config(root, state_dir)

    workspace_paths = payload.get("workspacePaths")
    if not isinstance(workspace_paths, list):
        workspace_paths = []
    workspace_key = normalize_workspace_key(workspace_paths)

    artifact_dir = str(payload.get("artifactDirectoryPath") or "").strip()
    transcript_path = str(payload.get("transcriptPath") or "").strip()
    initial_num_steps = int(payload.get("initialNumSteps") or 0)
    last_user_input = str(payload.get("lastUserInput") or "").strip()

    state = load_state(state_dir, conversation_id)
    registry = load_registry(state_dir)
    transcript_info = parse_transcript(transcript_path)
    is_forked = detect_fork(transcript_info, initial_num_steps)

    paths = resolve_ledger_paths(artifact_dir, workspace_paths)
    project_ledger = paths["project"]
    session_ledger = paths["session"]

    active_ledger: Path | None = None
    mode = ""
    just_inherited = False

    if project_ledger is not None and project_ledger.is_file():
        active_ledger = project_ledger
        mode = "project"
    elif session_ledger is not None and session_ledger.is_file():
        active_ledger = session_ledger
        mode = "session"
    elif (
        bool(config.get("inherit_on_fork", True))
        and is_forked
        and not state.get("fork_checked")
        and session_ledger is not None
    ):
        state["fork_checked"] = True
        step_ceiling = max(int(transcript_info.get("max_step_index") or -1), initial_num_steps)
        max_age = float(config.get("fork_max_age_hours") or 48)
        parent = find_fork_parent(
            registry=registry,
            conversation_id=conversation_id,
            workspace_key=workspace_key,
            current_step_ceiling=step_ceiling,
            require_ledger=True,
            max_age_hours=max_age,
        )
        if parent and parent.get("ledgerPath"):
            copy_ledger_on_fork(Path(parent["ledgerPath"]), session_ledger, str(parent["conversationId"]))
            state["inherited_from"] = parent["conversationId"]
            state["inherited_base_turns"] = int(parent.get("userTurns") or 0)
            active_ledger = session_ledger
            mode = "session"
            just_inherited = True
        else:
            parent_any = find_fork_parent(
                registry=registry,
                conversation_id=conversation_id,
                workspace_key=workspace_key,
                current_step_ceiling=step_ceiling,
                require_ledger=False,
                max_age_hours=max_age,
            )
            if parent_any:
                state["inherited_from"] = parent_any["conversationId"]
                state["inherited_base_turns"] = int(parent_any.get("userTurns") or 0)

    local_turns = int(transcript_info.get("user_turns") or 0)
    if local_turns <= 0:
        seen = state.setdefault("seen_prompt_hashes", [])
        if not isinstance(seen, list):
            seen = []
            state["seen_prompt_hashes"] = seen
        if last_user_input:
            prompt_hash = sha256_text(last_user_input)
            if prompt_hash not in seen:
                seen.append(prompt_hash)
        local_turns = len(seen) if seen else 1

    base_turns = int(state.get("inherited_base_turns") or 0)
    effective_turns = max(1, base_turns + local_turns)

    last_step_index = max(int(transcript_info.get("max_step_index") or -1), initial_num_steps)
    update_registry(
        state_dir,
        {
            "conversationId": conversation_id,
            "workspaceKey": workspace_key,
            "artifactDir": artifact_dir,
            "ledgerPath": str(active_ledger) if active_ledger is not None else "",
            "hasLedger": active_ledger is not None,
            "lastStepIndex": last_step_index,
            "userTurns": effective_turns,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        },
        max_entries=int(config.get("registry_max_entries") or 200),
    )

    if active_ledger is not None:
        ledger_text = active_ledger.read_text(encoding="utf-8-sig")
        if is_paused(ledger_text):
            save_state(state_dir, conversation_id, state)
            return {}

        content_hash = sha256_text(ledger_text)
        line_count, byte_count = measure_ledger(ledger_text)

        triggers: list[str] = []
        if just_inherited:
            triggers.append("inherited")
        if state.get("last_injected_hash") != content_hash:
            triggers.append("changed")

        cp_count = int(transcript_info.get("checkpoint_count") or 0)
        max_cp_step = int(transcript_info.get("max_checkpoint_step") if transcript_info.get("max_checkpoint_step") is not None else -1)
        prev_cp_count = int(state.get("last_checkpoint_count") or 0)
        prev_cp_step = int(state.get("last_checkpoint_step") if state.get("last_checkpoint_step") is not None else -1)
        if cp_count > prev_cp_count or max_cp_step > prev_cp_step:
            triggers.append("compacted")

        last_inj_turn = int(state.get("last_injected_turn") or 0)
        reanchor_every = int(config.get("reanchor_every_turns") or 6)
        if last_inj_turn > 0 and (effective_turns - last_inj_turn) >= reanchor_every:
            triggers.append("reanchor")

        if triggers:
            state["last_injected_hash"] = content_hash
            state["last_injected_turn"] = effective_turns
            state["last_checkpoint_count"] = cp_count
            state["last_checkpoint_step"] = max_cp_step
            state["ledger_path"] = str(active_ledger)
            save_state(state_dir, conversation_id, state)

            msg = _build_ledger_injection(
                ledger_text=ledger_text,
                ledger_path=active_ledger,
                mode=mode,
                triggers=triggers,
                line_count=line_count,
                byte_count=byte_count,
                config=config,
                inherited_from=state.get("inherited_from"),
            )
            return {
                "injectSteps": [
                    {
                        "systemMessage": {
                            "systemMessage": msg,
                            "metadata": {
                                "source": "context-anchor",
                                "mode": mode,
                                "triggers": triggers,
                            },
                        }
                    }
                ]
            }

        save_state(state_dir, conversation_id, state)
        return {}

    after_turns = int(config.get("auto_detect_after_turns") or 6)
    if effective_turns <= after_turns:
        save_state(state_dir, conversation_id, state)
        return {}

    recheck_turns = int(config.get("auto_detect_recheck_turns") or 6)
    last_check = int(state.get("last_auto_detect_turn") or 0)
    if last_check == 0 or (effective_turns - last_check) >= recheck_turns:
        target_path = session_ledger or project_ledger
        if target_path is None:
            save_state(state_dir, conversation_id, state)
            return {}
        state["last_auto_detect_turn"] = effective_turns
        save_state(state_dir, conversation_id, state)
        msg = _build_auto_detect_injection(target_path, effective_turns, config)
        return {
            "injectSteps": [
                {
                    "systemMessage": {
                        "systemMessage": msg,
                        "metadata": {
                            "source": "context-anchor",
                            "mode": "auto-detect",
                            "turn": effective_turns,
                        },
                    }
                }
            ]
        }

    save_state(state_dir, conversation_id, state)
    return {}


def main() -> int:
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace").strip()
        if not raw:
            sys.stdout.write("{}\n")
            return 0
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            sys.stdout.write("{}\n")
            return 0
        result = process_hook(payload)
        sys.stdout.write(json.dumps(result, ensure_ascii=True) + "\n")
        return 0
    except Exception:
        if os.environ.get("CONTEXT_ANCHOR_DEBUG") == "1":
            try:
                debug_dir = get_data_dir()
                debug_dir.mkdir(parents=True, exist_ok=True)
                with (debug_dir / "debug.log").open("a", encoding="utf-8") as fh:
                    fh.write(f"=== {datetime.now(timezone.utc).isoformat()} ===\n")
                    fh.write(traceback.format_exc() + "\n")
            except Exception:
                pass
        sys.stdout.write("{}\n")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
