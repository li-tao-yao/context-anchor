---
name: context-anchor
description: Manage the Context Anchor L1 working memory ledger and L2 topic memory cards (start, compact, memory-save, sync-index, pin, unpin, export, pause, resume, or status) in long Antigravity conversations.
---

# Context Anchor Skill

Use this skill when the user invokes `/context-anchor` or asks in natural language to start, update, compact, manage L2 topic memory cards (`memories/*.md`), sync the L2 index, pin/unpin a project ledger, export, pause, resume, or inspect status.

Locate the helper CLI at `../../scripts/ledger_tool.py` relative to this `SKILL.md` file (or `~/.gemini/config/plugins/context-anchor/scripts/ledger_tool.py`).

## Supported Workflows

### 1. Start / Enable (`start` or "开启记忆账本")
1. Determine target path:
   - If `<workspace>/.agents/context-anchor/memory_ledger.md` exists, use it.
   - Otherwise use `<artifactDirectoryPath>/memory_ledger.md`.
2. Initialize if missing:
   ```bash
   python "<plugin_root>/scripts/ledger_tool.py" init --target "<target_path>"
   ```
3. Immediately populate the 5 L1 sections with established facts from the conversation.
4. Validate:
   ```bash
   python "<plugin_root>/scripts/ledger_tool.py" check --target "<target_path>"
   ```

### 2. Save / Update / List / Delete L2 Topic Memory Cards (`memories/*.md`)
Offload bulky domain tables, glossaries, API/schema specs, module progress, or exclusion lists into `<ledger_dir>/memories/<slug>.md` while keeping non-negotiable hard constraints in L1:

- **Create or overwrite an L2 card (auto-syncs L1 index)**:
  ```bash
  python "<plugin_root>/scripts/ledger_tool.py" memory-save \
    --target "<active_ledger_path>" \
    --name "api_contract" \
    --title "核心接口与字段规范" \
    --trigger "修改后端接口、请求字段或错误码定义前必读" \
    --keywords "接口,字段,错误码,API" \
    --summary "统一管理核心 API 字段命名与错误码约定" \
    --body-file "<optional_body_md_file>"
  ```
  (Or pass `--body "<markdown content>"` for short bodies.)
- **List L2 cards & index sync status**:
  ```bash
  python "<plugin_root>/scripts/ledger_tool.py" memory-list --target "<active_ledger_path>"
  ```
- **Delete an obsolete L2 card (auto-syncs L1 index)**:
  ```bash
  python "<plugin_root>/scripts/ledger_tool.py" memory-delete --target "<active_ledger_path>" --name "obsolete_card"
  ```
- **Re-sync the L1 index table after manually editing `memories/*.md`**:
  ```bash
  python "<plugin_root>/scripts/ledger_tool.py" sync-index --target "<active_ledger_path>"
  ```

### 3. Compact / Audit (`compact` or "整理记忆账本")
1. Read the active `memory_ledger.md` (and run `memory-list` if `memories/` exists).
2. Merge redundant bullets, overwrite superseded version details in place, move bulky reference lists into `memories/*.md` cards, keep L1 within `<= 120 lines` / `<= 4000 chars` / `<= 12288 bytes`.
3. Run `sync-index` and verify with `check`:
   ```bash
   python "<plugin_root>/scripts/ledger_tool.py" sync-index --target "<active_ledger_path>"
   python "<plugin_root>/scripts/ledger_tool.py" check --target "<active_ledger_path>"
   ```

### 4. Pin / Unpin to Project (`pin` / `unpin`)
- **Pin** copies both `memory_ledger.md` and `memories/*.md` into `<workspace>/.agents/context-anchor/`:
  ```bash
  python "<plugin_root>/scripts/ledger_tool.py" pin --workspace "<workspace_root>" --session-ledger "<artifact_dir>/memory_ledger.md"
  ```
- **Unpin** optionally copies both `memory_ledger.md` and `memories/*.md` back to the session artifact dir before removing the workspace pin:
  ```bash
  python "<plugin_root>/scripts/ledger_tool.py" unpin --workspace "<workspace_root>" --session-ledger "<artifact_dir>/memory_ledger.md"
  ```

### 5. Export (`export`)
Copy the active ledger (and optionally `memories/`) to a user-chosen Markdown path in the workspace without changing the active hook tracking path.

### 6. Pause / Resume (`pause` / `resume`)
```bash
python "<plugin_root>/scripts/ledger_tool.py" pause --target "<active_ledger_path>"
python "<plugin_root>/scripts/ledger_tool.py" resume --target "<active_ledger_path>"
```

### 7. Status (`status`)
```bash
python "<plugin_root>/scripts/ledger_tool.py" status --artifact-dir "<artifact_dir>" --workspace "<workspace_root>" --conversation-id "<conversation_id>"
```
Report mode (`session` / `project` / `inactive`), active path, line/char/byte counts, L2 card count & index sync state, paused state, and parent fork inheritance.
