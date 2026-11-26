# Context Anchor Working Memory Protocol (v0.3 — L1 + L2)

This workspace/environment uses **Context Anchor** to preserve confirmed decisions, hard constraints, and structured domain context across long conversations, multi-version iterations, compactions, and `/btw` branches.

## Two-Tier Architecture (L1 + L2)
1. **L1 — Always-Anchored Working Ledger (`memory_ledger.md`)**:
   - **Session mode (default)**: `<artifactDirectoryPath>/memory_ledger.md`
   - **Project-pinned mode (optional)**: `<workspace>/.agents/context-anchor/memory_ledger.md` (takes precedence when present; ideal for ultra-long multi-session projects such as patents, textbooks, or large codebases).
   - Keep L1 concise (`<= 120 lines`, `<= 4000 chars`, `<= 12288 bytes`) with 5 core sections:
     1. `## 1. Goal & Scope` — core objective and boundaries.
     2. `## 2. Hard Constraints` — **non-negotiable rules, tolerances, red lines, and style limits. Never demote high-risk hard constraints solely into L2.**
     3. `## 3. Current Consensus` — latest active version baseline and confirmed decisions. **Overwrite obsolete values in place; never keep v1/v2 beside the active version.**
     4. `## 4. Rejected & Pitfalls` — concise dead ends and reasons so they are not re-proposed.
     5. `## 5. Active Files & Next` — key paths and immediate next step.
   - When L2 cards exist, an auto-synced `## 6. L2 Memory Index (Read on Demand)` table sits at the bottom of `memory_ledger.md`.
2. **L2 — On-Demand Topic Memory Cards (`memories/*.md` alongside `memory_ledger.md`)**:
   - Use L2 when bulky structured knowledge would bloat L1 (e.g., figure label tables, terminology/symbol glossaries, per-chapter revision status, exclusion lists, API/schema specs, review/OA pitfalls).
   - Each card uses lightweight YAML frontmatter (`title`, `trigger`, `keywords`, `updated`, `summary`) and is **overwritten in place** with the latest truth (keep superseded drafts in Git or Recycle Bin, never as duplicate memory cards).
   - Manage L2 cards via `ledger_tool.py memory-save` / `memory-delete` / `sync-index` so the L1 index stays strictly in sync without manual drift.

## Default Always-On Activation & Keyword Recall
- **Every user conversation (Turn 1+)**: Context Anchor auto-initializes `<artifactDirectoryPath>/memory_ledger.md` (`auto_start: true`) unless a project-pinned ledger exists. Replace template placeholders silently before your final reply (skip edits in read-only `/btw` side questions unless the user asks).
- **L2 Recall Discipline**: Whenever `[context-anchor | ... triggers=...l2_hint]` reports `Matched L2 memory cards for this turn`, or the current task matches a `Read Before (Trigger)` row in the L1 index, call `read_file` on the relevant `memories/*.md` card **before** editing or answering.
- To temporarily pause injection, use `/context-anchor pause` (or add `<!-- context-anchor: paused -->`).

## `/btw` Branch Isolation & Inheritance
- Each `/btw` branch inherits an **isolated private copy** of its true parent's substantive `memory_ledger.md` **and** `memories/*.md` directory (never mutating the parent or borrowing from sibling `/btw` branches).
- When a project-pinned ledger is active during an exploratory `/btw` question, treat both the shared project ledger and its `memories/` cards as read-only unless the user explicitly asks to adopt the branch's changes.
- Fallback: If a `<fork_notice>` names a parent artifact directory and the current conversation does not yet have `memory_ledger.md`, copy `<parent_artifact_dir>/memory_ledger.md` (and `memories/` if present) to `<artifactDirectoryPath>/` before answering.

## Privacy & Security
Never store passwords, API keys, tokens, cookies, private keys, or sensitive credentials inside `memory_ledger.md` or `memories/*.md`. Record only pointers (such as environment variable names or vault entries).
