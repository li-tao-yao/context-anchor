"""Comprehensive unit and integration tests for Context Anchor."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from common import DEFAULT_LEDGER_TEMPLATE, is_template_only  # noqa: E402
from ledger_tool import main as ledger_main  # noqa: E402
from pre_invocation import process_hook  # noqa: E402


def write_transcript(path: Path, steps: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(step, ensure_ascii=False) for step in steps]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def build_linear_steps(user_turns: int, start_step: int = 0, checkpoints_at: list[int] | None = None) -> list[dict]:
    checkpoints_set = set(checkpoints_at or [])
    steps: list[dict] = []
    idx = start_step
    for turn in range(1, user_turns + 1):
        steps.append(
            {
                "step_index": idx,
                "source": "USER_EXPLICIT",
                "type": "USER_INPUT",
                "status": "DONE",
                "content": f"User turn {turn}",
            }
        )
        idx += 1
        if turn in checkpoints_set:
            steps.append(
                {
                    "step_index": idx,
                    "source": "SYSTEM",
                    "type": "CHECKPOINT",
                    "status": "DONE",
                    "content": "Compacted checkpoint",
                }
            )
            idx += 1
        if turn < user_turns:
            steps.append(
                {
                    "step_index": idx,
                    "source": "MODEL",
                    "type": "PLANNER_RESPONSE",
                    "status": "DONE",
                    "content": f"Assistant turn {turn}",
                }
            )
            idx += 1
    return steps


class ContextAnchorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.data_dir = self.root / "state"
        self.workspace = self.root / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.old_env = os.environ.get("CONTEXT_ANCHOR_DATA_DIR")
        os.environ["CONTEXT_ANCHOR_DATA_DIR"] = str(self.data_dir)

    def tearDown(self) -> None:
        if self.old_env is None:
            os.environ.pop("CONTEXT_ANCHOR_DATA_DIR", None)
        else:
            os.environ["CONTEXT_ANCHOR_DATA_DIR"] = self.old_env
        self._tmp.cleanup()

    def _conv_dirs(self, conv_id: str) -> tuple[Path, Path]:
        art = self.root / "brain" / conv_id
        art.mkdir(parents=True, exist_ok=True)
        tr = art / ".system_generated" / "logs" / "transcript.jsonl"
        return art, tr

    def _payload(
        self,
        conv_id: str,
        art: Path,
        tr: Path,
        workspace: Path | None = None,
        initial_num_steps: int = 0,
        invocation_num: int = 0,
        last_user_input: str = "hello",
    ) -> dict:
        return {
            "conversationId": conv_id,
            "workspacePaths": [str(workspace)] if workspace is not None else [],
            "transcriptPath": str(tr),
            "artifactDirectoryPath": str(art),
            "initialNumSteps": initial_num_steps,
            "invocationNum": invocation_num,
            "lastUserInput": last_user_input,
        }

    def _set_user_config(self, cfg: dict) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "config.json").write_text(json.dumps(cfg), encoding="utf-8")

    def _assert_valid_injection(self, res: dict) -> str:
        self.assertIn("injectSteps", res)
        sys_msg = res["injectSteps"][0]["systemMessage"]
        self.assertNotIn("metadata", sys_msg, "systemMessage.metadata must be omitted to avoid host panic")
        return sys_msg["systemMessage"]

    def test_01_auto_start_turn_1_and_subagent_skip(self) -> None:
        art, tr = self._conv_dirs("conv-autostart")
        steps1 = build_linear_steps(1)
        write_transcript(tr, steps1)
        res1_a = process_hook(
            self._payload("conv-autostart", art, tr, self.workspace, initial_num_steps=len(steps1)),
            plugin_root=REPO_ROOT,
            data_dir=self.data_dir,
        )
        msg = self._assert_valid_injection(res1_a)
        self.assertIn("triggers=initialized,changed", msg)
        self.assertTrue((art / "memory_ledger.md").is_file())
        self.assertTrue(is_template_only((art / "memory_ledger.md").read_text(encoding="utf-8")))

        # Second invocation on same turn -> silent
        res1_b = process_hook(
            self._payload("conv-autostart", art, tr, self.workspace, initial_num_steps=len(steps1) + 1, invocation_num=1),
            plugin_root=REPO_ROOT,
            data_dir=self.data_dir,
        )
        self.assertEqual(res1_b, {})

        # Subagent (0 user turns, empty lastUserInput) -> skipped completely
        sub_art, sub_tr = self._conv_dirs("subagent-conv")
        write_transcript(sub_tr, [])
        res_sub = process_hook(
            self._payload("subagent-conv", sub_art, sub_tr, self.workspace, initial_num_steps=0, last_user_input=""),
            plugin_root=REPO_ROOT,
            data_dir=self.data_dir,
        )
        self.assertEqual(res_sub, {})
        self.assertFalse((sub_art / "memory_ledger.md").exists())
        reg = json.loads((self.data_dir / "registry.json").read_text(encoding="utf-8"))
        self.assertFalse(any(c["conversationId"] == "subagent-conv" for c in reg["conversations"]))

    def test_02_opt_out_auto_start_quiet_6_turns_and_auto_detect_turn_7(self) -> None:
        self._set_user_config({"auto_start": False})
        art, tr = self._conv_dirs("conv-autodetect")
        for turn in range(1, 7):
            steps = build_linear_steps(turn)
            write_transcript(tr, steps)
            res = process_hook(
                self._payload("conv-autodetect", art, tr, self.workspace, initial_num_steps=len(steps)),
                plugin_root=REPO_ROOT,
                data_dir=self.data_dir,
            )
            self.assertEqual(res, {}, f"Turn {turn} should be silent when auto_start=false")
        self.assertFalse((art / "memory_ledger.md").exists())

        # Turn 7 first invocation -> fires auto-detect
        steps_7 = build_linear_steps(7)
        write_transcript(tr, steps_7)
        res7_a = process_hook(
            self._payload("conv-autodetect", art, tr, self.workspace, initial_num_steps=len(steps_7), invocation_num=0),
            plugin_root=REPO_ROOT,
            data_dir=self.data_dir,
        )
        msg7 = self._assert_valid_injection(res7_a)
        self.assertIn("auto-detect | turn=7", msg7)
        self.assertIn(str(art / "memory_ledger.md"), msg7)

        # Turn 7 second invocation -> silent
        res7_b = process_hook(
            self._payload("conv-autodetect", art, tr, self.workspace, initial_num_steps=len(steps_7) + 1, invocation_num=1),
            plugin_root=REPO_ROOT,
            data_dir=self.data_dir,
        )
        self.assertEqual(res7_b, {})

        # Turns 8..12 -> silent
        for turn in range(8, 13):
            steps = build_linear_steps(turn)
            write_transcript(tr, steps)
            res = process_hook(
                self._payload("conv-autodetect", art, tr, self.workspace, initial_num_steps=len(steps)),
                plugin_root=REPO_ROOT,
                data_dir=self.data_dir,
            )
            self.assertEqual(res, {}, f"Turn {turn} should be in auto-detect cooldown")

        # Turn 13 (7 + 6) -> re-check fires
        steps_13 = build_linear_steps(13)
        write_transcript(tr, steps_13)
        res13 = process_hook(
            self._payload("conv-autodetect", art, tr, self.workspace, initial_num_steps=len(steps_13)),
            plugin_root=REPO_ROOT,
            data_dir=self.data_dir,
        )
        self._assert_valid_injection(res13)

    def test_03_manual_ledger_delta_and_periodic_6_turn_reanchor(self) -> None:
        art, tr = self._conv_dirs("conv-manual")
        ledger = art / "memory_ledger.md"
        ledger.write_text(DEFAULT_LEDGER_TEMPLATE + "\n- Fixture width: 100mm\n", encoding="utf-8")

        steps_2 = build_linear_steps(2)
        write_transcript(tr, steps_2)
        res2 = process_hook(
            self._payload("conv-manual", art, tr, self.workspace, initial_num_steps=len(steps_2)),
            plugin_root=REPO_ROOT,
            data_dir=self.data_dir,
        )
        msg2 = self._assert_valid_injection(res2)
        self.assertIn("triggers=changed", msg2)

        for turn in range(3, 8):
            steps = build_linear_steps(turn)
            write_transcript(tr, steps)
            res = process_hook(
                self._payload("conv-manual", art, tr, self.workspace, initial_num_steps=len(steps)),
                plugin_root=REPO_ROOT,
                data_dir=self.data_dir,
            )
            self.assertEqual(res, {}, f"Turn {turn} unchanged should not stack duplicate injections")

        steps_8 = build_linear_steps(8)
        write_transcript(tr, steps_8)
        res8 = process_hook(
            self._payload("conv-manual", art, tr, self.workspace, initial_num_steps=len(steps_8)),
            plugin_root=REPO_ROOT,
            data_dir=self.data_dir,
        )
        msg8 = self._assert_valid_injection(res8)
        self.assertIn("triggers=reanchor", msg8)

    def test_04_ledger_modification_triggers_immediate_delta_injection(self) -> None:
        art, tr = self._conv_dirs("conv-delta")
        ledger = art / "memory_ledger.md"
        ledger.write_text(DEFAULT_LEDGER_TEMPLATE + "\n- Version: v1\n", encoding="utf-8")
        write_transcript(tr, build_linear_steps(2))
        res2 = process_hook(self._payload("conv-delta", art, tr, self.workspace), plugin_root=REPO_ROOT, data_dir=self.data_dir)
        self._assert_valid_injection(res2)

        write_transcript(tr, build_linear_steps(3))
        res3_a = process_hook(self._payload("conv-delta", art, tr, self.workspace), plugin_root=REPO_ROOT, data_dir=self.data_dir)
        self.assertEqual(res3_a, {})

        ledger.write_text(DEFAULT_LEDGER_TEMPLATE + "\n- Version: v2\n", encoding="utf-8")
        res3_b = process_hook(
            self._payload("conv-delta", art, tr, self.workspace, invocation_num=1),
            plugin_root=REPO_ROOT,
            data_dir=self.data_dir,
        )
        msg3_b = self._assert_valid_injection(res3_b)
        self.assertIn("- Version: v2", msg3_b)
        self.assertIn("triggers=changed", msg3_b)

    def test_05_compaction_checkpoint_triggers_immediate_injection(self) -> None:
        art, tr = self._conv_dirs("conv-compact")
        ledger = art / "memory_ledger.md"
        ledger.write_text(DEFAULT_LEDGER_TEMPLATE + "\n- Tolerance: +/-0.02mm\n", encoding="utf-8")

        write_transcript(tr, build_linear_steps(3))
        res3 = process_hook(self._payload("conv-compact", art, tr, self.workspace), plugin_root=REPO_ROOT, data_dir=self.data_dir)
        self._assert_valid_injection(res3)

        write_transcript(tr, build_linear_steps(4, checkpoints_at=[3]))
        res4 = process_hook(self._payload("conv-compact", art, tr, self.workspace), plugin_root=REPO_ROOT, data_dir=self.data_dir)
        msg4 = self._assert_valid_injection(res4)
        self.assertIn("triggers=compacted", msg4)

        write_transcript(tr, build_linear_steps(5, checkpoints_at=[3]))
        res5 = process_hook(self._payload("conv-compact", art, tr, self.workspace), plugin_root=REPO_ROOT, data_dir=self.data_dir)
        self.assertEqual(res5, {})

    def test_06_promoted_btw_fork_inheritance_and_isolation(self) -> None:
        parent_art, parent_tr = self._conv_dirs("parent-conv")
        parent_ledger = parent_art / "memory_ledger.md"
        parent_ledger.write_text(DEFAULT_LEDGER_TEMPLATE + "\n- Parent decision: Use 45# steel\n", encoding="utf-8")
        parent_steps = build_linear_steps(10)
        write_transcript(parent_tr, parent_steps)
        process_hook(
            self._payload("parent-conv", parent_art, parent_tr, self.workspace, initial_num_steps=len(parent_steps)),
            plugin_root=REPO_ROOT,
            data_dir=self.data_dir,
        )

        child_art, child_tr = self._conv_dirs("child-conv")
        child_steps = build_linear_steps(1, start_step=81)
        write_transcript(child_tr, child_steps)
        res_child = process_hook(
            self._payload("child-conv", child_art, child_tr, self.workspace, initial_num_steps=84),
            plugin_root=REPO_ROOT,
            data_dir=self.data_dir,
        )
        child_ledger = child_art / "memory_ledger.md"
        self.assertTrue(child_ledger.is_file(), "Child conversation should inherit memory_ledger.md")
        msg_child = self._assert_valid_injection(res_child)
        self.assertIn("triggers=inherited,changed", msg_child)
        self.assertIn("Use 45# steel", msg_child)

        child_ledger.write_text(child_ledger.read_text(encoding="utf-8") + "\n- Child update: Nitride coating\n", encoding="utf-8")
        self.assertNotIn("Nitride coating", parent_ledger.read_text(encoding="utf-8"))

    def test_07_pre_ledger_btw_promotion_preserves_turn_count_when_opt_out(self) -> None:
        self._set_user_config({"auto_start": False})
        parent_art, parent_tr = self._conv_dirs("parent-pre")
        parent_steps = build_linear_steps(5)
        write_transcript(parent_tr, parent_steps)
        res_p = process_hook(
            self._payload("parent-pre", parent_art, parent_tr, self.workspace, initial_num_steps=len(parent_steps)),
            plugin_root=REPO_ROOT,
            data_dir=self.data_dir,
        )
        self.assertEqual(res_p, {})

        child_art, child_tr = self._conv_dirs("child-pre")
        write_transcript(child_tr, build_linear_steps(1, start_step=12))
        res_c1 = process_hook(
            self._payload("child-pre", child_art, child_tr, self.workspace, initial_num_steps=14),
            plugin_root=REPO_ROOT,
            data_dir=self.data_dir,
        )
        self.assertEqual(res_c1, {})

        write_transcript(child_tr, build_linear_steps(2, start_step=12))
        res_c2 = process_hook(
            self._payload("child-pre", child_art, child_tr, self.workspace, initial_num_steps=16),
            plugin_root=REPO_ROOT,
            data_dir=self.data_dir,
        )
        msg_c2 = self._assert_valid_injection(res_c2)
        self.assertIn("auto-detect | turn=7", msg_c2)

    def test_08_unrelated_new_conversation_does_not_inherit_session_ledger(self) -> None:
        self._set_user_config({"auto_start": False})
        art1, tr1 = self._conv_dirs("conv-one")
        (art1 / "memory_ledger.md").write_text(DEFAULT_LEDGER_TEMPLATE + "\n- Secret-only-for-conv-one\n", encoding="utf-8")
        write_transcript(tr1, build_linear_steps(3))
        process_hook(self._payload("conv-one", art1, tr1, self.workspace, initial_num_steps=5), plugin_root=REPO_ROOT, data_dir=self.data_dir)

        art2, tr2 = self._conv_dirs("conv-two")
        write_transcript(tr2, build_linear_steps(1, start_step=0))
        res2 = process_hook(self._payload("conv-two", art2, tr2, self.workspace, initial_num_steps=1), plugin_root=REPO_ROOT, data_dir=self.data_dir)
        self.assertEqual(res2, {})
        self.assertFalse((art2 / "memory_ledger.md").exists())

    def test_09_project_pinned_ledger_loads_in_new_conversation(self) -> None:
        pinned = self.workspace / ".agents" / "context-anchor" / "memory_ledger.md"
        pinned.parent.mkdir(parents=True, exist_ok=True)
        pinned.write_text(DEFAULT_LEDGER_TEMPLATE + "\n- Project Standard: ISO-2768-m\n", encoding="utf-8")

        art, tr = self._conv_dirs("conv-proj")
        write_transcript(tr, build_linear_steps(1, start_step=0))
        res = process_hook(self._payload("conv-proj", art, tr, self.workspace, initial_num_steps=1), plugin_root=REPO_ROOT, data_dir=self.data_dir)
        msg = self._assert_valid_injection(res)
        self.assertIn("mode=project", msg)
        self.assertIn("ISO-2768-m", msg)

    def test_10_paused_ledger_returns_empty(self) -> None:
        art, tr = self._conv_dirs("conv-paused")
        ledger = art / "memory_ledger.md"
        ledger.write_text("<!-- context-anchor: paused -->\n" + DEFAULT_LEDGER_TEMPLATE, encoding="utf-8")
        write_transcript(tr, build_linear_steps(8))
        res = process_hook(self._payload("conv-paused", art, tr, self.workspace), plugin_root=REPO_ROOT, data_dir=self.data_dir)
        self.assertEqual(res, {})

    def test_11_oversize_ledger_triggers_warning(self) -> None:
        art, tr = self._conv_dirs("conv-big")
        ledger = art / "memory_ledger.md"
        many_lines = "\n".join(f"- Line {i}" for i in range(1, 140))
        ledger.write_text(DEFAULT_LEDGER_TEMPLATE + "\n" + many_lines + "\n", encoding="utf-8")
        write_transcript(tr, build_linear_steps(1))
        res = process_hook(self._payload("conv-big", art, tr, self.workspace), plugin_root=REPO_ROOT, data_dir=self.data_dir)
        msg = self._assert_valid_injection(res)
        self.assertIn("WARNING: Ledger exceeds limit", msg)

    def test_12_pure_text_conversation_and_btw_inheritance(self) -> None:
        p_art, p_tr = self._conv_dirs("text-parent")
        (p_art / "memory_ledger.md").write_text(DEFAULT_LEDGER_TEMPLATE + "\n- Tone: Academic concise\n", encoding="utf-8")
        write_transcript(p_tr, build_linear_steps(7))
        res_p = process_hook(
            self._payload("text-parent", p_art, p_tr, workspace=None, initial_num_steps=13),
            plugin_root=REPO_ROOT,
            data_dir=self.data_dir,
        )
        self._assert_valid_injection(res_p)

        c_art, c_tr = self._conv_dirs("text-child")
        write_transcript(c_tr, build_linear_steps(1, start_step=25))
        res_c = process_hook(
            self._payload("text-child", c_art, c_tr, workspace=None, initial_num_steps=27),
            plugin_root=REPO_ROOT,
            data_dir=self.data_dir,
        )
        self.assertTrue((c_art / "memory_ledger.md").is_file())
        msg_c = self._assert_valid_injection(res_c)
        self.assertIn("Academic concise", msg_c)

    def test_13_fail_open_on_malformed_json_and_missing_fields(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "pre_invocation.py")],
            input=b"{broken-json",
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.decode("utf-8").strip(), "{}")

        res_empty = process_hook({}, plugin_root=REPO_ROOT, data_dir=self.data_dir)
        self.assertEqual(res_empty, {})

    def test_14_ledger_tool_subcommands(self) -> None:
        art, _ = self._conv_dirs("tool-conv")
        session_ledger = art / "memory_ledger.md"

        self.assertEqual(ledger_main(["init", "--target", str(session_ledger)]), 0)
        self.assertTrue(session_ledger.is_file())
        self.assertEqual(ledger_main(["check", "--target", str(session_ledger)]), 0)

        self.assertEqual(
            ledger_main(["pin", "--workspace", str(self.workspace), "--session-ledger", str(session_ledger)]),
            0,
        )
        pinned = self.workspace / ".agents" / "context-anchor" / "memory_ledger.md"
        self.assertTrue(pinned.is_file())

        self.assertEqual(ledger_main(["pause", "--target", str(pinned)]), 0)
        self.assertIn("paused", pinned.read_text(encoding="utf-8"))
        self.assertEqual(ledger_main(["resume", "--target", str(pinned)]), 0)
        self.assertNotIn("paused", pinned.read_text(encoding="utf-8"))

        self.assertEqual(ledger_main(["status", "--artifact-dir", str(art), "--workspace", str(self.workspace)]), 0)

        self.assertEqual(
            ledger_main(["unpin", "--workspace", str(self.workspace), "--session-ledger", str(session_ledger)]),
            0,
        )
        self.assertFalse(pinned.exists())
        self.assertTrue(session_ledger.is_file())

    def test_15_windows_cmd_utf8_subprocess_smoke(self) -> None:
        art, tr = self._conv_dirs("conv-utf8")
        ledger = art / "memory_ledger.md"
        ledger.write_text(
            DEFAULT_LEDGER_TEMPLATE + "\n- 工装夹具尺寸 120±0.02mm，定位销孔 H7 配合\n",
            encoding="utf-8",
        )
        write_transcript(tr, build_linear_steps(2))
        payload = self._payload("conv-utf8", art, tr, self.workspace, initial_num_steps=3)

        if os.name == "nt":
            cmd = ["cmd", "/c", "python", "./scripts/pre_invocation.py"]
        else:
            cmd = [sys.executable, "./scripts/pre_invocation.py"]

        env = dict(os.environ)
        env["CONTEXT_ANCHOR_DATA_DIR"] = str(self.data_dir)
        proc = subprocess.run(
            cmd,
            cwd=str(REPO_ROOT),
            input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            capture_output=True,
            env=env,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr.decode("utf-8", errors="replace"))
        parsed = json.loads(proc.stdout.decode("utf-8"))
        text = self._assert_valid_injection(parsed)
        self.assertIn("工装夹具尺寸 120±0.02mm", text)

    def test_16_btw_from_ledgerless_main_does_not_steal_sibling_btw_ledger(self) -> None:
        # Main conversation M at step 518 (9 user turns), has no ledger yet
        m_art, m_tr = self._conv_dirs("main-1d7")
        m_steps = build_linear_steps(9, start_step=502)  # ends at step 518
        write_transcript(m_tr, m_steps)
        self._set_user_config({"auto_start": False})
        process_hook(self._payload("main-1d7", m_art, m_tr, self.workspace, initial_num_steps=519), plugin_root=REPO_ROOT, data_dir=self.data_dir)

        # Sibling /btw B1 forked earlier at 506..577 WITH its own plugin-repair ledger
        self._set_user_config({"auto_start": True})
        b1_art, b1_tr = self._conv_dirs("btw-5a2")
        (b1_art / "memory_ledger.md").write_text(DEFAULT_LEDGER_TEMPLATE + "\n- Sibling-Only-Fix-Plugin\n", encoding="utf-8")
        b1_steps = build_linear_steps(2, start_step=506)
        b1_steps[-1]["step_index"] = 577
        write_transcript(b1_tr, b1_steps)
        process_hook(self._payload("btw-5a2", b1_art, b1_tr, self.workspace, initial_num_steps=578), plugin_root=REPO_ROOT, data_dir=self.data_dir)

        # New /btw B2 opens from main M at step 520
        b2_art, b2_tr = self._conv_dirs("btw-f93")
        write_transcript(b2_tr, build_linear_steps(1, start_step=520))
        res_b2 = process_hook(self._payload("btw-f93", b2_art, b2_tr, self.workspace, initial_num_steps=521), plugin_root=REPO_ROOT, data_dir=self.data_dir)
        msg_b2 = self._assert_valid_injection(res_b2)
        self.assertNotIn("Sibling-Only-Fix-Plugin", msg_b2)
        self.assertIn("triggers=initialized,changed", msg_b2)

        b2_state = json.loads((self.data_dir / "conversations" / "btw-f93.json").read_text(encoding="utf-8"))
        self.assertEqual(b2_state.get("inherited_from"), "main-1d7")
        self.assertEqual(b2_state.get("inherited_base_turns"), 9)

    def test_17_discarded_btw_never_pollutes_main_or_future_btw(self) -> None:
        m_art, m_tr = self._conv_dirs("main-clean")
        m_ledger = m_art / "memory_ledger.md"
        m_ledger.write_text(DEFAULT_LEDGER_TEMPLATE + "\n- Spec-Main: Material 304\n", encoding="utf-8")
        write_transcript(m_tr, build_linear_steps(4, start_step=0))  # ends at step 6
        process_hook(self._payload("main-clean", m_art, m_tr, self.workspace, initial_num_steps=7), plugin_root=REPO_ROOT, data_dir=self.data_dir)

        # Throwaway /btw B1 edits its own copy with Dead-End-Experiment and stops
        b1_art, b1_tr = self._conv_dirs("btw-discard")
        write_transcript(b1_tr, build_linear_steps(1, start_step=8))
        process_hook(self._payload("btw-discard", b1_art, b1_tr, self.workspace, initial_num_steps=9), plugin_root=REPO_ROOT, data_dir=self.data_dir)
        b1_ledger = b1_art / "memory_ledger.md"
        b1_ledger.write_text(b1_ledger.read_text(encoding="utf-8") + "\n- Dead-End-Experiment\n", encoding="utf-8")
        write_transcript(b1_tr, build_linear_steps(2, start_step=8))
        process_hook(self._payload("btw-discard", b1_art, b1_tr, self.workspace, initial_num_steps=11), plugin_root=REPO_ROOT, data_dir=self.data_dir)

        # Main M advances to step 12 and opens a second /btw B2 at step 14
        write_transcript(m_tr, build_linear_steps(7, start_step=0))  # ends at step 12
        process_hook(self._payload("main-clean", m_art, m_tr, self.workspace, initial_num_steps=13), plugin_root=REPO_ROOT, data_dir=self.data_dir)
        self.assertNotIn("Dead-End-Experiment", m_ledger.read_text(encoding="utf-8"))

        b2_art, b2_tr = self._conv_dirs("btw-next")
        write_transcript(b2_tr, build_linear_steps(1, start_step=14))
        res_b2 = process_hook(self._payload("btw-next", b2_art, b2_tr, self.workspace, initial_num_steps=15), plugin_root=REPO_ROOT, data_dir=self.data_dir)
        msg_b2 = self._assert_valid_injection(res_b2)
        self.assertIn("Spec-Main: Material 304", msg_b2)
        self.assertNotIn("Dead-End-Experiment", msg_b2)

    def test_18_promoted_btw_becomes_parent_for_later_forks(self) -> None:
        m_art, m_tr = self._conv_dirs("main-old")
        (m_art / "memory_ledger.md").write_text(DEFAULT_LEDGER_TEMPLATE + "\n- Old-Main\n", encoding="utf-8")
        write_transcript(m_tr, build_linear_steps(4, start_step=512))  # ends at 518
        process_hook(self._payload("main-old", m_art, m_tr, self.workspace, initial_num_steps=519), plugin_root=REPO_ROOT, data_dir=self.data_dir)

        # B1 forks at 520, gets promoted, and advances far past M to step 650
        b1_art, b1_tr = self._conv_dirs("btw-promoted")
        write_transcript(b1_tr, build_linear_steps(1, start_step=520))
        process_hook(self._payload("btw-promoted", b1_art, b1_tr, self.workspace, initial_num_steps=521), plugin_root=REPO_ROOT, data_dir=self.data_dir)
        b1_ledger = b1_art / "memory_ledger.md"
        b1_ledger.write_text(DEFAULT_LEDGER_TEMPLATE + "\n- Promoted-Spec: Titanium\n", encoding="utf-8")
        b1_steps = build_linear_steps(5, start_step=520)
        b1_steps[-1]["step_index"] = 650
        write_transcript(b1_tr, b1_steps)
        process_hook(self._payload("btw-promoted", b1_art, b1_tr, self.workspace, initial_num_steps=651), plugin_root=REPO_ROOT, data_dir=self.data_dir)

        # Later /btw B3 opens from promoted B1 at step 652
        b3_art, b3_tr = self._conv_dirs("btw-grandchild")
        write_transcript(b3_tr, build_linear_steps(1, start_step=652))
        res_b3 = process_hook(self._payload("btw-grandchild", b3_art, b3_tr, self.workspace, initial_num_steps=653), plugin_root=REPO_ROOT, data_dir=self.data_dir)
        msg_b3 = self._assert_valid_injection(res_b3)
        self.assertIn("Promoted-Spec: Titanium", msg_b3)
        self.assertNotIn("Old-Main", msg_b3)

    def test_19_multi_main_conversations_prefers_step_adjacent_parent(self) -> None:
        m1_art, m1_tr = self._conv_dirs("main-m1")
        (m1_art / "memory_ledger.md").write_text(DEFAULT_LEDGER_TEMPLATE + "\n- M1-Spec\n", encoding="utf-8")
        m1_steps = build_linear_steps(3, start_step=0)
        m1_steps[-1]["step_index"] = 518
        write_transcript(m1_tr, m1_steps)
        process_hook(self._payload("main-m1", m1_art, m1_tr, self.workspace, initial_num_steps=519), plugin_root=REPO_ROOT, data_dir=self.data_dir)

        time.sleep(0.02)
        m2_art, m2_tr = self._conv_dirs("main-m2")
        (m2_art / "memory_ledger.md").write_text(DEFAULT_LEDGER_TEMPLATE + "\n- M2-Spec\n", encoding="utf-8")
        m2_steps = build_linear_steps(3, start_step=0)
        m2_steps[-1]["step_index"] = 800
        write_transcript(m2_tr, m2_steps)
        process_hook(self._payload("main-m2", m2_art, m2_tr, self.workspace, initial_num_steps=801), plugin_root=REPO_ROOT, data_dir=self.data_dir)

        # /btw opens at step 520 -> should match M1 (distance 2), NOT newer M2 (distance 280)
        b_art, b_tr = self._conv_dirs("btw-adj")
        write_transcript(b_tr, build_linear_steps(1, start_step=520))
        res_b = process_hook(self._payload("btw-adj", b_art, b_tr, self.workspace, initial_num_steps=521), plugin_root=REPO_ROOT, data_dir=self.data_dir)
        msg_b = self._assert_valid_injection(res_b)
        self.assertIn("M1-Spec", msg_b)
        self.assertNotIn("M2-Spec", msg_b)

    def test_20_blank_template_not_inherited_and_not_marked_has_ledger(self) -> None:
        m_art, m_tr = self._conv_dirs("main-blank")
        write_transcript(m_tr, build_linear_steps(2, start_step=0))
        process_hook(self._payload("main-blank", m_art, m_tr, self.workspace, initial_num_steps=3), plugin_root=REPO_ROOT, data_dir=self.data_dir)
        reg = json.loads((self.data_dir / "registry.json").read_text(encoding="utf-8"))
        m_entry = next(c for c in reg["conversations"] if c["conversationId"] == "main-blank")
        self.assertFalse(m_entry["hasLedger"])
        self.assertEqual(m_entry["ledgerPath"], "")

        b_art, b_tr = self._conv_dirs("btw-from-blank")
        write_transcript(b_tr, build_linear_steps(1, start_step=10))
        res_b = process_hook(self._payload("btw-from-blank", b_art, b_tr, self.workspace, initial_num_steps=11), plugin_root=REPO_ROOT, data_dir=self.data_dir)
        msg_b = self._assert_valid_injection(res_b)
        self.assertNotIn("triggers=inherited", msg_b)
        self.assertIn("triggers=initialized,changed", msg_b)

    def test_21_btw_project_pinned_mode_injects_readonly_warning(self) -> None:
        pinned = self.workspace / ".agents" / "context-anchor" / "memory_ledger.md"
        pinned.parent.mkdir(parents=True, exist_ok=True)
        pinned.write_text(DEFAULT_LEDGER_TEMPLATE + "\n- Shared-Project-Rule\n", encoding="utf-8")

        b_art, b_tr = self._conv_dirs("btw-pinned")
        write_transcript(b_tr, build_linear_steps(1, start_step=40))
        res = process_hook(self._payload("btw-pinned", b_art, b_tr, self.workspace, initial_num_steps=42), plugin_root=REPO_ROOT, data_dir=self.data_dir)
        msg = self._assert_valid_injection(res)
        self.assertIn("mode=project", msg)
        self.assertIn("read-only context", msg)
        self.assertIn("Do NOT write exploratory /btw discussion", msg)

    def test_22_chinese_fair_char_and_byte_budget(self) -> None:
        art, tr = self._conv_dirs("conv-cn-budget")
        ledger = art / "memory_ledger.md"
        # ~2,500 Chinese chars = ~7,500 UTF-8 bytes: previously triggered 6KB warning, now OK under 4000 chars / 12KB
        cn_block = "\n".join(f"- 第{i}条设计约束：采用标准无砟轨道结构参数与统一术语规范，保持最新定稿口径。" for i in range(1, 70))
        ledger.write_text(DEFAULT_LEDGER_TEMPLATE + "\n" + cn_block + "\n", encoding="utf-8")
        write_transcript(tr, build_linear_steps(1))
        res_ok = process_hook(self._payload("conv-cn-budget", art, tr, self.workspace), plugin_root=REPO_ROOT, data_dir=self.data_dir)
        msg_ok = self._assert_valid_injection(res_ok)
        self.assertNotIn("WARNING: Ledger exceeds limit", msg_ok)

        # Exceed max_chars (>4000 chars) -> triggers warning with L2 offload advice
        huge_block = "\n".join(f"- 第{i}条超长明细：" + ("轨道参数" * 18) for i in range(1, 65))
        ledger.write_text(DEFAULT_LEDGER_TEMPLATE + "\n" + huge_block + "\n", encoding="utf-8")
        res_warn = process_hook(self._payload("conv-cn-budget", art, tr, self.workspace, invocation_num=1), plugin_root=REPO_ROOT, data_dir=self.data_dir)
        msg_warn = self._assert_valid_injection(res_warn)
        self.assertIn("WARNING: Ledger exceeds limit", msg_warn)
        self.assertIn("memories/*.md", msg_warn)

    def test_23_l2_memory_save_sync_list_delete_cli(self) -> None:
        art, _ = self._conv_dirs("conv-l2-cli")
        ledger = art / "memory_ledger.md"
        self.assertEqual(ledger_main(["init", "--target", str(ledger)]), 0)

        self.assertEqual(
            ledger_main(
                [
                    "memory-save",
                    "--target",
                    str(ledger),
                    "--name",
                    "figure_labels",
                    "--title",
                    "附图标记与部件名称表",
                    "--trigger",
                    "修改附图或实施例部件标号前必读",
                    "--keywords",
                    "附图,标号,部件,实施例",
                    "--summary",
                    "统一管理18个附图标记与最新命名",
                    "--updated",
                    "2026-11-26",
                    "--body",
                    "# 附图标记\n- 100：承载基座\n- 101：升降导向机构",
                ]
            ),
            0,
        )
        card = art / "memories" / "figure_labels.md"
        self.assertTrue(card.is_file())
        ledger_text = ledger.read_text(encoding="utf-8")
        self.assertIn("l2-index:start", ledger_text)
        self.assertIn("`memories/figure_labels.md`", ledger_text)
        self.assertIn("统一管理18个附图标记与最新命名", ledger_text)
        self.assertEqual(ledger_main(["check", "--target", str(ledger)]), 0)
        self.assertEqual(ledger_main(["memory-list", "--target", str(ledger)]), 0)

        # Delete removes file and cleanly strips the L2 index block when empty
        self.assertEqual(ledger_main(["memory-delete", "--target", str(ledger), "--name", "figure_labels"]), 0)
        self.assertFalse(card.exists())
        cleaned_text = ledger.read_text(encoding="utf-8")
        self.assertNotIn("l2-index:start", cleaned_text)
        self.assertEqual(ledger_main(["check", "--target", str(ledger)]), 0)

    def test_24_l2_keyword_hint_fires_on_unchanged_turn_and_dedups(self) -> None:
        art, tr = self._conv_dirs("conv-l2-hint")
        ledger = art / "memory_ledger.md"
        ledger.write_text(DEFAULT_LEDGER_TEMPLATE + "\n- Active project: Patent v12\n", encoding="utf-8")
        self.assertEqual(
            ledger_main(
                [
                    "memory-save",
                    "--target",
                    str(ledger),
                    "--name",
                    "figure_labels",
                    "--title",
                    "附图标记表",
                    "--trigger",
                    "修改附图或标号前必读",
                    "--keywords",
                    "附图,标号,API",
                    "--summary",
                    "全部附图编号清单",
                    "--body",
                    "- 100：底座",
                ]
            ),
            0,
        )

        # Turn 1: L1 changed (unrelated prompt) -> L1 injected without l2_hint
        write_transcript(tr, build_linear_steps(1))
        res1 = process_hook(
            self._payload("conv-l2-hint", art, tr, self.workspace, last_user_input="capital budget overview"),
            plugin_root=REPO_ROOT,
            data_dir=self.data_dir,
        )
        msg1 = self._assert_valid_injection(res1)
        self.assertNotIn("l2_hint", msg1, "ASCII keyword 'API' must not match substring inside 'capital'")

        # Turn 2: L1 unchanged, Chinese keyword matches -> compact l2_hint fires WITHOUT repeating full L1
        write_transcript(tr, build_linear_steps(2))
        res2_a = process_hook(
            self._payload("conv-l2-hint", art, tr, self.workspace, invocation_num=0, last_user_input="帮我核对图3的零件标号"),
            plugin_root=REPO_ROOT,
            data_dir=self.data_dir,
        )
        msg2_a = self._assert_valid_injection(res2_a)
        self.assertIn("triggers=l2_hint", msg2_a)
        self.assertIn("`memories/figure_labels.md`", msg2_a)
        self.assertIn("Matched: 标号", msg2_a)
        self.assertNotIn("## 1. Goal & Scope", msg2_a, "Hint-only injection must not duplicate full L1 ledger")

        # Turn 2 second invocation (same turn & prompt) -> deduplicated to silent {}
        res2_b = process_hook(
            self._payload("conv-l2-hint", art, tr, self.workspace, invocation_num=1, last_user_input="帮我核对图3的零件标号"),
            plugin_root=REPO_ROOT,
            data_dir=self.data_dir,
        )
        self.assertEqual(res2_b, {})

        # Turn 3: ASCII word-boundary keyword 'API' matches -> fires again on new turn
        write_transcript(tr, build_linear_steps(3))
        res3 = process_hook(
            self._payload("conv-l2-hint", art, tr, self.workspace, last_user_input="Update the API contract"),
            plugin_root=REPO_ROOT,
            data_dir=self.data_dir,
        )
        msg3 = self._assert_valid_injection(res3)
        self.assertIn("triggers=l2_hint", msg3)
        self.assertIn("Matched: API", msg3)

    def test_25_l2_hint_merges_with_l1_and_warns_when_index_unsynced(self) -> None:
        art, tr = self._conv_dirs("conv-l2-unsynced")
        ledger = art / "memory_ledger.md"
        ledger.write_text(DEFAULT_LEDGER_TEMPLATE + "\n- Textbook revision v8\n", encoding="utf-8")
        # Hand-create an L2 file WITHOUT calling sync-index
        mem_dir = art / "memories"
        mem_dir.mkdir(parents=True, exist_ok=True)
        (mem_dir / "symbols.md").write_text(
            "---\ntitle: 符号术语表\ntrigger: 修改公式前必读\nkeywords: [公式, 符号]\nsummary: 全书统一数学符号\n---\n- E: 弹性模量\n",
            encoding="utf-8",
        )

        write_transcript(tr, build_linear_steps(1))
        res = process_hook(
            self._payload("conv-l2-unsynced", art, tr, self.workspace, last_user_input="统一第3章公式里的符号"),
            plugin_root=REPO_ROOT,
            data_dir=self.data_dir,
        )
        msg = self._assert_valid_injection(res)
        self.assertIn("triggers=changed,l2_hint", msg)
        self.assertIn("not yet listed in the L1 index", msg)
        self.assertIn("`memories/symbols.md`", msg)
        # check should warn because L2 index is out of sync
        self.assertEqual(ledger_main(["check", "--target", str(ledger)]), 1)
        # sync-index fixes it
        self.assertEqual(ledger_main(["sync-index", "--target", str(ledger)]), 0)
        self.assertEqual(ledger_main(["check", "--target", str(ledger)]), 0)

    def test_26_btw_fork_copies_and_isolates_l2_memories(self) -> None:
        p_art, p_tr = self._conv_dirs("parent-l2")
        p_ledger = p_art / "memory_ledger.md"
        p_ledger.write_text(DEFAULT_LEDGER_TEMPLATE + "\n- Parent Patent v5\n", encoding="utf-8")
        self.assertEqual(
            ledger_main(
                [
                    "memory-save",
                    "--target",
                    str(p_ledger),
                    "--name",
                    "claims",
                    "--title",
                    "当前权利要求",
                    "--trigger",
                    "修改权利要求前必读",
                    "--keywords",
                    "权利要求,独立权项",
                    "--summary",
                    "v5 最新独权与从权",
                    "--body",
                    "- 权利要求1：原版结构",
                ]
            ),
            0,
        )
        write_transcript(p_tr, build_linear_steps(5))
        process_hook(
            self._payload("parent-l2", p_art, p_tr, self.workspace, initial_num_steps=9),
            plugin_root=REPO_ROOT,
            data_dir=self.data_dir,
        )

        # Fork child /btw
        c_art, c_tr = self._conv_dirs("child-l2")
        write_transcript(c_tr, build_linear_steps(1, start_step=20))
        res_c = process_hook(
            self._payload("child-l2", c_art, c_tr, self.workspace, initial_num_steps=22, last_user_input="检查权利要求1"),
            plugin_root=REPO_ROOT,
            data_dir=self.data_dir,
        )
        child_card = c_art / "memories" / "claims.md"
        parent_card = p_art / "memories" / "claims.md"
        self.assertTrue(child_card.is_file(), "Forked /btw branch must inherit L2 memories/*.md")
        msg_c = self._assert_valid_injection(res_c)
        self.assertIn("triggers=inherited,changed,l2_hint", msg_c)
        self.assertIn(str(child_card), msg_c)

        # Mutate child's L2 card -> parent's L2 card remains untouched
        child_card.write_text(child_card.read_text(encoding="utf-8") + "\n- 分支实验方案\n", encoding="utf-8")
        self.assertNotIn("分支实验方案", parent_card.read_text(encoding="utf-8"))

    def test_27_pin_and_unpin_preserve_l2_memories(self) -> None:
        art, _ = self._conv_dirs("conv-pin-l2")
        session_ledger = art / "memory_ledger.md"
        self.assertEqual(ledger_main(["init", "--target", str(session_ledger)]), 0)
        self.assertEqual(
            ledger_main(
                [
                    "memory-save",
                    "--target",
                    str(session_ledger),
                    "--name",
                    "terms",
                    "--title",
                    "术语表",
                    "--keywords",
                    "术语",
                    "--summary",
                    "全书统一术语",
                    "--body",
                    "- 无砟轨道 = ballastless track",
                ]
            ),
            0,
        )

        self.assertEqual(
            ledger_main(["pin", "--workspace", str(self.workspace), "--session-ledger", str(session_ledger)]),
            0,
        )
        pinned_card = self.workspace / ".agents" / "context-anchor" / "memories" / "terms.md"
        self.assertTrue(pinned_card.is_file())

        # Update pinned L2 card, then unpin back to a fresh session dir
        pinned_card.write_text(pinned_card.read_text(encoding="utf-8") + "\n- 轨枕 = sleeper\n", encoding="utf-8")
        art2, _ = self._conv_dirs("conv-unpin-dst")
        dst_ledger = art2 / "memory_ledger.md"
        self.assertEqual(
            ledger_main(["unpin", "--workspace", str(self.workspace), "--session-ledger", str(dst_ledger)]),
            0,
        )
        self.assertFalse(pinned_card.exists())
        restored_card = art2 / "memories" / "terms.md"
        self.assertTrue(restored_card.is_file())
        self.assertIn("轨枕 = sleeper", restored_card.read_text(encoding="utf-8"))

    def test_28_l2_frontmatter_fallbacks_and_max_entries_cap(self) -> None:
        art, _ = self._conv_dirs("conv-l2-edge")
        ledger = art / "memory_ledger.md"
        self.assertEqual(ledger_main(["init", "--target", str(ledger)]), 0)
        mem_dir = art / "memories"
        mem_dir.mkdir(parents=True, exist_ok=True)
        # Hand-written file with no frontmatter + pipe character in heading
        (mem_dir / "01_plain.md").write_text("# 规则 A | 规则 B\n正文第一行摘要内容\n", encoding="utf-8")
        # Multiline YAML keyword list
        (mem_dir / "02_yaml_list.md").write_text(
            "---\ntitle: 多行列表卡\ntrigger: 检查 | 复核\nkeywords:\n  - 复核\n  - 校对\nsummary: 测试多行关键词\n---\n内容\n",
            encoding="utf-8",
        )
        (mem_dir / "03_extra.md").write_text("---\ntitle: 第三张\nsummary: 超限截断测试\n---\n内容\n", encoding="utf-8")

        self.assertEqual(ledger_main(["sync-index", "--target", str(ledger), "--max-entries", "2"]), 0)
        text = ledger.read_text(encoding="utf-8")
        self.assertIn(r"规则 A \| 规则 B", text)
        self.assertIn("Showing 2/3 L2 memory cards", text)

    def test_29_l2_keyword_hints_respect_disable_config(self) -> None:
        self._set_user_config({"l2_keyword_hints": False})
        art, tr = self._conv_dirs("conv-l2-off")
        ledger = art / "memory_ledger.md"
        ledger.write_text(DEFAULT_LEDGER_TEMPLATE + "\n- Spec: v1\n", encoding="utf-8")
        self.assertEqual(
            ledger_main(
                [
                    "memory-save",
                    "--target",
                    str(ledger),
                    "--name",
                    "glossary",
                    "--title",
                    "术语表",
                    "--keywords",
                    "术语,符号",
                    "--summary",
                    "术语",
                    "--body",
                    "- A=1",
                ]
            ),
            0,
        )
        write_transcript(tr, build_linear_steps(1))
        res1 = process_hook(
            self._payload("conv-l2-off", art, tr, self.workspace, last_user_input="核对术语"),
            plugin_root=REPO_ROOT,
            data_dir=self.data_dir,
        )
        msg1 = self._assert_valid_injection(res1)
        self.assertNotIn("l2_hint", msg1)

        write_transcript(tr, build_linear_steps(2))
        res2 = process_hook(
            self._payload("conv-l2-off", art, tr, self.workspace, last_user_input="再核对一次符号"),
            plugin_root=REPO_ROOT,
            data_dir=self.data_dir,
        )
        self.assertEqual(res2, {})


if __name__ == "__main__":
    unittest.main()
