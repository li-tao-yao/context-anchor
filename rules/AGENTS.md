# Context Anchor Working Memory Protocol

This workspace/environment uses **Context Anchor** to prevent loss of confirmed decisions, constraints, and multi-turn state across long conversations, compactions, and `/btw` branches.

## Storage Modes
1. **Default — Session Ledger**: `<artifactDirectoryPath>/memory_ledger.md`
   - Used for pure-text conversations, casual workspaces, and single-thread tasks without cluttering project directories.
2. **Optional — Project-Pinned Ledger**: `<workspace>/.agents/context-anchor/memory_ledger.md`
   - Takes precedence when present. Used only when the user asks to persist/pin working memory across separate conversations in the same project.

## Default Always-On Activation
- **Every user conversation (Turn 1+)**: Context Anchor is enabled by default from Turn 1 (`auto_start: true`) — no `/context-anchor` command is needed. `PreInvocation` automatically initializes `<artifactDirectoryPath>/memory_ledger.md` (unless a project-pinned ledger already exists) and injects `[context-anchor | ...]`.
- When `memory_ledger.md` is newly initialized (still contains template placeholders), silently replace the placeholders with the established facts of the current conversation before your final reply (skip editing in read-only `/btw` side questions unless the user asks to edit files).
- To temporarily pause automatic injection for a conversation, use `/context-anchor pause` (or add `<!-- context-anchor: paused -->`).

## Overwrite-in-Place Discipline
Keep `memory_ledger.md` concise (`<= 120 lines` and `<= 6144 bytes`) with these 5 sections:
1. `## 1. Goal & Scope` — core goal and boundaries.
2. `## 2. Hard Constraints` — non-negotiable rules, tolerances, interfaces, style/budget limits.
3. `## 3. Current Consensus` — latest confirmed decisions/specs. **Always overwrite obsolete values in place; never leave superseded v1/v2 values beside the active one.**
4. `## 4. Rejected & Pitfalls` — explicitly rejected options and why, to prevent re-proposing dead ends.
5. `## 5. Active Files & Next` — key file paths (if any) and immediate next step.

Update the ledger **before** your final reply whenever the user confirms a change, adds a constraint, or rejects a proposal.

## `/btw` Branch Isolation & Inheritance
- Each `/btw` branch inherits an **isolated private copy** of its true parent conversation's substantive ledger (never mutating the parent's ledger, and never borrowing ledgers from sibling `/btw` branches).
- If a `/btw` branch is discarded, the main conversation and future branches from main are completely unaffected. If a `/btw` branch is promoted to main, its isolated copy continues evolving and becomes the parent for subsequent forks.
- When a project-pinned ledger is active during an exploratory `/btw` question, treat the shared project ledger as read-only unless the user explicitly asks to adopt the branch's changes.
- Fallback: If a `<fork_notice>` names a parent artifact directory and the current conversation does not yet have `memory_ledger.md` (and the hook has not already inherited it), check if `<parent_artifact_dir>/memory_ledger.md` exists with substantive content and copy it to `<artifactDirectoryPath>/memory_ledger.md` before answering.

## Privacy & Security
Never store passwords, API keys, tokens, cookies, private keys, or sensitive credentials inside `memory_ledger.md`. Record only pointers (such as environment variable names or vault entries).
