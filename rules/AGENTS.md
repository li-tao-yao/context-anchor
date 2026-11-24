# Context Anchor Working Memory Protocol

This workspace/environment uses **Context Anchor** to prevent loss of confirmed decisions, constraints, and multi-turn state across long conversations, compactions, and `/btw` branches.

## Storage Modes
1. **Default — Session Ledger**: `<artifactDirectoryPath>/memory_ledger.md`
   - Used for pure-text conversations, casual workspaces, and single-thread tasks without cluttering project directories.
2. **Optional — Project-Pinned Ledger**: `<workspace>/.agents/context-anchor/memory_ledger.md`
   - Takes precedence when present. Used only when the user asks to persist/pin working memory across separate conversations in the same project.

## Activation & Quiet Window
- **Turns 1–6**: Stay completely quiet about memory ledgers unless the user explicitly asks to enable it (e.g., `/context-anchor`, "开启记忆账本", "开启 context-anchor", "记住当前设计状态").
- **Turn 7+ (Auto-detect)**: When `PreInvocation` injects a `[context-anchor | auto-detect]` notice, silently evaluate whether the conversation contains durable constraints, confirmed specs/decisions, rejected options, or multi-step work. If yes, create `memory_ledger.md` at the suggested path. If it is casual Q&A, do nothing and do not mention the check.

## Overwrite-in-Place Discipline
Keep `memory_ledger.md` concise (`<= 120 lines` and `<= 6144 bytes`) with these 5 sections:
1. `## 1. Goal & Scope` — core goal and boundaries.
2. `## 2. Hard Constraints` — non-negotiable rules, tolerances, interfaces, style/budget limits.
3. `## 3. Current Consensus` — latest confirmed decisions/specs. **Always overwrite obsolete values in place; never leave superseded v1/v2 values beside the active one.**
4. `## 4. Rejected & Pitfalls` — explicitly rejected options and why, to prevent re-proposing dead ends.
5. `## 5. Active Files & Next` — key file paths (if any) and immediate next step.

Update the ledger **before** your final reply whenever the user confirms a change, adds a constraint, or rejects a proposal.

## Fork & `/btw` Fallback Inheritance
If a `<fork_notice>` names a parent artifact directory and the current conversation does not yet have `memory_ledger.md` (and the hook has not already inherited it), check if `<parent_artifact_dir>/memory_ledger.md` exists and copy it to the current `<artifactDirectoryPath>/memory_ledger.md` before answering.

## Privacy & Security
Never store passwords, API keys, tokens, cookies, private keys, or sensitive credentials inside `memory_ledger.md`. Record only pointers (such as environment variable names or vault entries).
