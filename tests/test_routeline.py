import io
import json
import unittest
from contextlib import redirect_stdout

import routeline


class CorpusTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = routeline.load()


class SceneParsingTest(CorpusTestCase):
    def test_quotes_keep_speaker_and_stance(self):
        hill = self.corpus.scenes["hill-a"]
        self.assertEqual(hill.quotes[0].speaker, "守寨人")
        self.assertEqual(hill.quotes[0].stance, "promise")
        self.assertEqual(hill.quotes[0].condition, "等我收了你的黑旗")
        water = self.corpus.scenes["water-a"]
        self.assertEqual(water.quotes[0].speaker, "商队")
        self.assertEqual(water.quotes[0].stance, "irony")

    def test_action_note_marks_handover(self):
        hill = self.corpus.scenes["hill-a"]
        handover = [n for n in hill.notes if n.kind == "handover"]
        self.assertEqual(len(handover), 1)
        self.assertTrue(hill.fills_handover)

    def test_water_scene_is_a_warning_not_a_handover(self):
        water = self.corpus.scenes["water-a"]
        self.assertFalse(water.fills_handover)
        self.assertEqual(routeline.classify(self.corpus, water), "shared")

    def test_bridge_scenes_are_distinguished(self):
        b = self.corpus.scenes["bridge-b"]
        c = self.corpus.scenes["bridge-c"]
        d = self.corpus.scenes["bridge-d"]
        self.assertTrue(b.forbids_flag_talk)
        self.assertTrue(b.doubt)
        self.assertTrue(c.requires_handover)
        self.assertTrue(d.requires_holding)
        self.assertTrue(d.denies_attribution)
        self.assertEqual(d.claim_phrase, "还来的旧物")
        self.assertNotEqual(routeline.classify(self.corpus, d), "shared")


class GroupStateTest(CorpusTestCase):
    def test_each_group_keeps_its_own_cast(self):
        self.assertEqual(self.corpus.states["甲组"].cast, {"守寨人": "何岚"})
        self.assertEqual(self.corpus.states["乙组"].cast, {"商队掌柜": "何岚"})

    def test_only_jia_handed_over_a_flag(self):
        self.assertEqual(self.corpus.states["甲组"].handovers, {"黑旗"})
        self.assertEqual(self.corpus.states["乙组"].holdings, {"白旗"})
        self.assertEqual(self.corpus.states["甲组"].holdings, set())

    def test_only_jia_has_a_spoken_promise(self):
        self.assertEqual(self.corpus.states["甲组"].promises, {"带过桥": "g1"})
        self.assertEqual(self.corpus.states["乙组"].promises, {})

    def test_heard_lines_come_from_the_logs(self):
        heard = self.corpus.states["甲组"].heard
        self.assertEqual([(s, q, log) for s, q, log in heard][0][0], "hill-a")
        self.assertEqual([log for _, _, log in heard], ["g1"])


class EvaluationTest(CorpusTestCase):
    def test_bridge_c_fits_jia_only(self):
        scene = self.corpus.scenes["bridge-c"]
        self.assertTrue(routeline.evaluate(self.corpus, "甲组", scene).ok)
        verdict = routeline.evaluate(self.corpus, "乙组", scene)
        self.assertFalse(verdict.ok)
        self.assertTrue(any("交旗" in reason for reason in verdict.reasons))
        self.assertTrue(any("甲组" in reason for reason in verdict.reasons))

    def test_bridge_d_rejects_even_the_holder(self):
        scene = self.corpus.scenes["bridge-d"]
        for group in ("甲组", "乙组"):
            verdict = routeline.evaluate(self.corpus, group, scene)
            self.assertFalse(verdict.ok)
        self.assertTrue(any("旧诺" in r or "许" in r for r in
                            routeline.evaluate(self.corpus, "乙组", scene).reasons))

    def test_shared_opening_is_usable_by_both(self):
        scene = self.corpus.scenes["bridge-b"]
        for group in ("甲组", "乙组"):
            self.assertTrue(routeline.evaluate(self.corpus, group, scene).ok)

    def test_actor_identity_is_not_transferred(self):
        # 何岚在乙组演商队；对甲组说商队的话，不能借用乙组身份。
        verdict = routeline.evaluate(self.corpus, "甲组", self.corpus.scenes["water-a"])
        self.assertFalse(verdict.ok)
        self.assertTrue(any("何岚" in reason for reason in verdict.reasons))

    def test_implied_referent_is_filled_for_jia(self):
        verdict = routeline.evaluate(self.corpus, "甲组", self.corpus.scenes["bridge-c"])
        self.assertEqual(verdict.implied[0]["指认"], "甲组（刚交旗的观众）")


class PlanTest(CorpusTestCase):
    def test_change_plan_keeps_spoken_promise(self):
        result = routeline.plan(self.corpus, ("甲组", "乙组"),
                                skip=("hill-a",), join=True)
        self.assertEqual(result["保留的已出口承诺"], {"甲组": {"带过桥": "g1"}})
        self.assertIn("bridge-c", [s["幕"] for s in result["分组安排"]["甲组"]["可继续"]])
        self.assertNotIn("bridge-c", [s["幕"] for s in result["分组安排"]["乙组"]["可继续"]])

    def test_shared_prompt_precedes_private_prompts(self):
        result = routeline.plan(self.corpus, ("甲组", "乙组"),
                                skip=("hill-a",), join=True)
        self.assertEqual(result["共用提示"]["幕"], "bridge-b")
        private = {entry["组"]: entry for entry in result["合场"]["单组承接"]}
        self.assertEqual(private["甲组"]["幕"], "bridge-c")
        self.assertIn("乙组", private["甲组"]["不透露"]["其他组"])

    def test_stranded_plan_lists_playable_and_split(self):
        result = routeline.plan(self.corpus, ("甲组", "乙组"),
                                skip=("hill-a", "bridge-c"), join=True)
        stranded = result["未履行承诺"]
        self.assertEqual(len(stranded), 1)
        entry = stranded[0]
        self.assertEqual(entry["组"], "甲组")
        self.assertIn("bridge-b", entry["可以先演"])
        self.assertTrue(any(item["幕"] == "bridge-c" for item in entry["必须分组补演"]))

    def test_groups_without_records_are_rejected(self):
        with self.assertRaises(routeline.CorpusError):
            routeline.plan(self.corpus, ("丙组",))

    def test_unknown_skip_is_rejected(self):
        with self.assertRaises(routeline.CorpusError):
            routeline.plan(self.corpus, ("甲组",), skip=("nowhere",))

    def test_join_requires_two_groups(self):
        with self.assertRaises(routeline.CorpusError):
            routeline.plan(self.corpus, ("甲组",), join=True)


class CheckTest(CorpusTestCase):
    def test_check_flags_misplaced_line(self):
        result = routeline.check_lineup(self.corpus, "乙组", ["bridge-c"])
        self.assertFalse(result["通过"])
        self.assertEqual(result["错接"][0]["问题"], "错接")

    def test_check_accepts_jia_continuation(self):
        result = routeline.check_lineup(self.corpus, "甲组", ["bridge-b", "bridge-c"])
        self.assertTrue(result["通过"])
        self.assertEqual(result["错接"], [])

    def test_check_warns_when_flag_talk_follows_quiet_opening(self):
        result = routeline.check_lineup(self.corpus, "甲组", ["bridge-b", "bridge-c"])
        self.assertTrue(result["通过"])
        self.assertEqual(len(result["提醒"]), 1)
        self.assertIn("不提旗", result["提醒"][0]["提醒"])

    def test_check_reports_unknown_scene(self):
        result = routeline.check_lineup(self.corpus, "甲组", ["nowhere"])
        self.assertFalse(result["通过"])


class CommandLineTest(CorpusTestCase):
    def run_cli(self, *args):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = routeline.main(list(args))
        return code, json.loads(buffer.getvalue())

    def test_report_lists_all_records(self):
        code, payload = self.run_cli("report")
        self.assertEqual(code, 0)
        self.assertEqual(set(payload["幕"]), set(self.corpus.scenes))
        self.assertEqual(payload["检查"], [])

    def test_check_exit_code_reflects_misplacement(self):
        code, payload = self.run_cli("check", "--group", "乙组", "--lineup", "bridge-c")
        self.assertEqual(code, 1)
        self.assertFalse(payload["通过"])

    def test_show_returns_heard_lines(self):
        code, payload = self.run_cli("show", "--group", "乙组")
        self.assertEqual(code, 0)
        self.assertEqual(payload["实际听过的台词"][0]["场记"], "g2")

    def test_validate_passes_on_shipped_corpus(self):
        code, payload = self.run_cli("validate")
        self.assertEqual(code, 0)
        self.assertTrue(payload["通过"])

    def test_bad_change_id_exits_two(self):
        with redirect_stdout(io.StringIO()), \
                __import__("contextlib").redirect_stderr(io.StringIO()):
            code = routeline.main(["plan", "--change", "nope"])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
