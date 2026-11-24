"""Comprehensive unit and integration tests for Context Anchor."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from common import DEFAULT_LEDGER_TEMPLATE  # noqa: E402
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

    def test_01_quiet_first_6_turns(self) -> None:
        art, tr = self._conv_dirs("conv-quiet")
        for turn in range(1, 7):
            steps = build_linear_steps(turn)
            write_transcript(tr, steps)
            res = process_hook(
                self._payload("conv-quiet", art, tr, self.workspace, initial_num_steps=len(steps)),
                plugin_root=REPO_ROOT,
                data_dir=self.data_dir,
            )
            self.assertEqual(res, {}, f"Turn {turn} should be silent")
        self.assertFalse((art / "memory_ledger.md").exists())

    def test_02_auto_detect_at_turn_7_and_cooldown_to_turn_13(self) -> None:
        art, tr = self._conv_dirs("conv-autodetect")
        # Turn 7 first invocation -> fires auto-detect
        steps_7 = build_linear_steps(7)
        write_transcript(tr, steps_7)
        res7_a = process_hook(
            self._payload("conv-autodetect", art, tr, self.workspace, initial_num_steps=len(steps_7), invocation_num=0),
            plugin_root=REPO_ROOT,
            data_dir=self.data_dir,
        )
        self.assertIn("injectSteps", res7_a)
        msg7 = res7_a["injectSteps"][0]["systemMessage"]["systemMessage"]
        self.assertIn("auto-detect", msg7)
        self.assertIn(str(art / "memory_ledger.md"), msg7)

        # Turn 7 second invocation (after tool call inside same turn) -> silent
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
        self.assertIn("injectSteps", res13)

    def test_03_manual_ledger_delta_and_periodic_6_turn_reanchor(self) -> None:
        art, tr = self._conv_dirs("conv-manual")
        ledger = art / "memory_ledger.md"
        ledger.write_text(DEFAULT_LEDGER_TEMPLATE + "\n- Fixture width: 100mm\n", encoding="utf-8")

        # Turn 2 -> immediate injection ("changed")
        steps_2 = build_linear_steps(2)
        write_transcript(tr, steps_2)
        res2 = process_hook(
            self._payload("conv-manual", art, tr, self.workspace, initial_num_steps=len(steps_2)),
            plugin_root=REPO_ROOT,
            data_dir=self.data_dir,
        )
        self.assertIn("injectSteps", res2)
        self.assertIn("changed", res2["injectSteps"][0]["systemMessage"]["metadata"]["triggers"])

        # Turns 3..7 unchanged -> {}
        for turn in range(3, 8):
            steps = build_linear_steps(turn)
            write_transcript(tr, steps)
            res = process_hook(
                self._payload("conv-manual", art, tr, self.workspace, initial_num_steps=len(steps)),
                plugin_root=REPO_ROOT,
                data_dir=self.data_dir,
            )
            self.assertEqual(res, {}, f"Turn {turn} unchanged should not stack duplicate injections")

        # Turn 8 (2 + 6) -> periodic reanchor
        steps_8 = build_linear_steps(8)
        write_transcript(tr, steps_8)
        res8 = process_hook(
            self._payload("conv-manual", art, tr, self.workspace, initial_num_steps=len(steps_8)),
            plugin_root=REPO_ROOT,
            data_dir=self.data_dir,
        )
        self.assertIn("injectSteps", res8)
        self.assertEqual(res8["injectSteps"][0]["systemMessage"]["metadata"]["triggers"], ["reanchor"])

    def test_04_ledger_modification_triggers_immediate_delta_injection(self) -> None:
        art, tr = self._conv_dirs("conv-delta")
        ledger = art / "memory_ledger.md"
        ledger.write_text(DEFAULT_LEDGER_TEMPLATE + "\n- Version: v1\n", encoding="utf-8")
        write_transcript(tr, build_linear_steps(2))
        res2 = process_hook(self._payload("conv-delta", art, tr, self.workspace), plugin_root=REPO_ROOT, data_dir=self.data_dir)
        self.assertIn("injectSteps", res2)

        # Turn 3 unchanged -> {}
        write_transcript(tr, build_linear_steps(3))
        res3_a = process_hook(self._payload("conv-delta", art, tr, self.workspace), plugin_root=REPO_ROOT, data_dir=self.data_dir)
        self.assertEqual(res3_a, {})

        # Modify ledger -> immediate re-injection even on same turn
        ledger.write_text(DEFAULT_LEDGER_TEMPLATE + "\n- Version: v2\n", encoding="utf-8")
        res3_b = process_hook(
            self._payload("conv-delta", art, tr, self.workspace, invocation_num=1),
            plugin_root=REPO_ROOT,
            data_dir=self.data_dir,
        )
        self.assertIn("injectSteps", res3_b)
        self.assertIn("- Version: v2", res3_b["injectSteps"][0]["systemMessage"]["systemMessage"])
        self.assertIn("changed", res3_b["injectSteps"][0]["systemMessage"]["metadata"]["triggers"])

    def test_05_compaction_checkpoint_triggers_immediate_injection(self) -> None:
        art, tr = self._conv_dirs("conv-compact")
        ledger = art / "memory_ledger.md"
        ledger.write_text(DEFAULT_LEDGER_TEMPLATE + "\n- Tolerance: +/-0.02mm\n", encoding="utf-8")

        write_transcript(tr, build_linear_steps(3))
        res3 = process_hook(self._payload("conv-compact", art, tr, self.workspace), plugin_root=REPO_ROOT, data_dir=self.data_dir)
        self.assertIn("injectSteps", res3)

        # Turn 4 with CHECKPOINT inserted, ledger unchanged -> must inject with "compacted"
        write_transcript(tr, build_linear_steps(4, checkpoints_at=[3]))
        res4 = process_hook(self._payload("conv-compact", art, tr, self.workspace), plugin_root=REPO_ROOT, data_dir=self.data_dir)
        self.assertIn("injectSteps", res4)
        self.assertIn("compacted", res4["injectSteps"][0]["systemMessage"]["metadata"]["triggers"])

        # Turn 5 with no new checkpoint -> silent
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

        # Child forked/promoted from /btw at step 81, initialNumSteps=84
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
        self.assertIn("injectSteps", res_child)
        triggers = res_child["injectSteps"][0]["systemMessage"]["metadata"]["triggers"]
        self.assertIn("inherited", triggers)
        self.assertIn("Use 45# steel", res_child["injectSteps"][0]["systemMessage"]["systemMessage"])

        # Modifying child copy must NOT mutate parent ledger
        child_ledger.write_text(child_ledger.read_text(encoding="utf-8") + "\n- Child update: Nitride coating\n", encoding="utf-8")
        self.assertNotIn("Nitride coating", parent_ledger.read_text(encoding="utf-8"))

    def test_07_pre_ledger_btw_promotion_preserves_turn_count(self) -> None:
        parent_art, parent_tr = self._conv_dirs("parent-pre")
        parent_steps = build_linear_steps(5)  # 5 turns, no ledger yet
        write_transcript(parent_tr, parent_steps)
        res_p = process_hook(
            self._payload("parent-pre", parent_art, parent_tr, self.workspace, initial_num_steps=len(parent_steps)),
            plugin_root=REPO_ROOT,
            data_dir=self.data_dir,
        )
        self.assertEqual(res_p, {})

        # Promoted /btw child at step 12: Turn 1 in child = effective Turn 6 (silent)
        child_art, child_tr = self._conv_dirs("child-pre")
        write_transcript(child_tr, build_linear_steps(1, start_step=12))
        res_c1 = process_hook(
            self._payload("child-pre", child_art, child_tr, self.workspace, initial_num_steps=14),
            plugin_root=REPO_ROOT,
            data_dir=self.data_dir,
        )
        self.assertEqual(res_c1, {})

        # Turn 2 in child = effective Turn 7 -> auto-detect fires!
        write_transcript(child_tr, build_linear_steps(2, start_step=12))
        res_c2 = process_hook(
            self._payload("child-pre", child_art, child_tr, self.workspace, initial_num_steps=16),
            plugin_root=REPO_ROOT,
            data_dir=self.data_dir,
        )
        self.assertIn("injectSteps", res_c2)
        self.assertEqual(res_c2["injectSteps"][0]["systemMessage"]["metadata"]["mode"], "auto-detect")

    def test_08_unrelated_new_conversation_does_not_inherit_session_ledger(self) -> None:
        art1, tr1 = self._conv_dirs("conv-one")
        (art1 / "memory_ledger.md").write_text(DEFAULT_LEDGER_TEMPLATE + "\n- Secret-only-for-conv-one\n", encoding="utf-8")
        write_transcript(tr1, build_linear_steps(3))
        process_hook(self._payload("conv-one", art1, tr1, self.workspace, initial_num_steps=5), plugin_root=REPO_ROOT, data_dir=self.data_dir)

        # Brand-new unrelated conversation starts at step_index 0
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
        self.assertIn("injectSteps", res)
        meta = res["injectSteps"][0]["systemMessage"]["metadata"]
        self.assertEqual(meta["mode"], "project")
        self.assertIn("ISO-2768-m", res["injectSteps"][0]["systemMessage"]["systemMessage"])

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
        self.assertIn("injectSteps", res)
        self.assertIn("WARNING: Ledger exceeds limit", res["injectSteps"][0]["systemMessage"]["systemMessage"])

    def test_12_pure_text_conversation_and_btw_inheritance(self) -> None:
        p_art, p_tr = self._conv_dirs("text-parent")
        (p_art / "memory_ledger.md").write_text(DEFAULT_LEDGER_TEMPLATE + "\n- Tone: Academic concise\n", encoding="utf-8")
        write_transcript(p_tr, build_linear_steps(7))
        res_p = process_hook(
            self._payload("text-parent", p_art, p_tr, workspace=None, initial_num_steps=13),
            plugin_root=REPO_ROOT,
            data_dir=self.data_dir,
        )
        self.assertIn("injectSteps", res_p)

        c_art, c_tr = self._conv_dirs("text-child")
        write_transcript(c_tr, build_linear_steps(1, start_step=25))
        res_c = process_hook(
            self._payload("text-child", c_art, c_tr, workspace=None, initial_num_steps=27),
            plugin_root=REPO_ROOT,
            data_dir=self.data_dir,
        )
        self.assertTrue((c_art / "memory_ledger.md").is_file())
        self.assertIn("Academic concise", res_c["injectSteps"][0]["systemMessage"]["systemMessage"])

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
        self.assertIn("injectSteps", parsed)
        text = parsed["injectSteps"][0]["systemMessage"]["systemMessage"]
        self.assertIn("工装夹具尺寸 120±0.02mm", text)


if __name__ == "__main__":
    unittest.main()
