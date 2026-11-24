---
name: context-anchor
description: Manage the Context Anchor working memory ledger (start, compact, pin to project, unpin, export, pause, resume, or inspect status) in long Antigravity conversations.
---

# Context Anchor Skill

Use this skill when the user invokes `/context-anchor` or asks in natural language to start, update, compact, pin, unpin, export, pause, resume, or inspect their working memory ledger.

Locate the helper CLI at `../../scripts/ledger_tool.py` relative to this `SKILL.md` file (or `~/.gemini/config/plugins/context-anchor/scripts/ledger_tool.py`).

## Supported Workflows

### 1. Start / Enable (`start` or "开启记忆账本" / "开启 context-anchor")
1. Determine target path:
   - If the workspace already has `.agents/context-anchor/memory_ledger.md`, use it.
   - Otherwise use `<artifactDirectoryPath>/memory_ledger.md`.
2. Initialize if missing:
   ```bash
   python "<plugin_root>/scripts/ledger_tool.py" init --target "<target_path>"
   ```
3. Immediately populate the 5 sections (`Goal & Scope`, `Hard Constraints`, `Current Consensus`, `Rejected & Pitfalls`, `Active Files & Next`) with facts already established in the conversation so far.
4. Run a size/schema check:
   ```bash
   python "<plugin_root>/scripts/ledger_tool.py" check --target "<target_path>"
   ```

### 2. Compact / Audit (`compact` or "整理记忆账本")
1. Read the active `memory_ledger.md`.
2. Merge redundant bullets, remove superseded values so only the latest consensus remains, keep rejected options concise, and trim to `<= 120 lines` / `<= 6144 bytes`.
3. Write the cleaned file and verify with `ledger_tool.py check --target "<path>"`.

### 3. Pin to Project (`pin` or "固定到当前项目")
1. Copy the active session ledger into `<workspace>/.agents/context-anchor/memory_ledger.md` so new conversations in the same project inherit it automatically:
   ```bash
   python "<plugin_root>/scripts/ledger_tool.py" pin --workspace "<workspace_root>" --session-ledger "<artifact_dir>/memory_ledger.md"
   ```
   (Omit `--session-ledger` if no session ledger exists yet.)

### 4. Unpin from Project (`unpin` or "取消项目固定")
1. Optionally preserve the latest state back into `<artifactDirectoryPath>/memory_ledger.md` and remove the workspace pin:
   ```bash
   python "<plugin_root>/scripts/ledger_tool.py" unpin --workspace "<workspace_root>" --session-ledger "<artifact_dir>/memory_ledger.md"
   ```

### 5. Export (`export`)
1. Copy the active ledger to a user-chosen Markdown path in the workspace (e.g. `DESIGN_SUMMARY.md` or `docs/decision-ledger.md`) without changing the active hook tracking path.

### 6. Pause / Resume (`off` / `pause` / `resume`)
- Pause automatic injections for the active ledger:
  ```bash
  python "<plugin_root>/scripts/ledger_tool.py" pause --target "<active_ledger_path>"
  ```
- Resume automatic injections:
  ```bash
  python "<plugin_root>/scripts/ledger_tool.py" resume --target "<active_ledger_path>"
  ```

### 7. Status (`status`)
```bash
python "<plugin_root>/scripts/ledger_tool.py" status --artifact-dir "<artifact_dir>" --workspace "<workspace_root>" --conversation-id "<conversation_id>"
```
Report the mode (`session` / `project` / `inactive`), active file path, line/byte counts, paused state, and any parent conversation inheritance.
