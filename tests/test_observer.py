import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from tugboat.observer import TurnLogger, TurnRecord, orient, RuleProposal
from tugboat.navigator import Turn, RoutingDecision, ModelDecision
from tugboat.adapter import TurnResult


def _fake_result(*, engine="ollama", model="qwen", tclass="drafting",
                 user=None, context=None, ctx_tokens=0):
    turn = Turn(text="hi", task_class=tclass, context_tokens=ctx_tokens,
                user=user, context=context)
    decision = RoutingDecision()
    decision.model = ModelDecision(engine=engine, model=model, reason="test")
    return turn, TurnResult(turn=turn, decision=decision, output="ok",
                            latency_ms=1.5)


class ObserverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(
            delete=False, suffix=".jsonl", mode="w", encoding="utf-8"
        )
        self.tmp.close()
        self.path = self.tmp.name

    def tearDown(self):
        try:
            os.unlink(self.path)
        except OSError:
            pass

    def test_log_writes_jsonl_row(self):
        logger = TurnLogger(self.path)
        turn, result = _fake_result(engine="cloud")
        logger.log(turn, result)

        rows = logger.read_all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].model_engine, "cloud")

    def test_log_append_preserves_history(self):
        logger = TurnLogger(self.path)
        for i in range(5):
            t, r = _fake_result(engine="cloud" if i % 2 else "ollama")
            logger.log(t, r)
        rows = logger.read_all()
        self.assertEqual(len(rows), 5)

    def test_orient_finds_per_user_pattern(self):
        logger = TurnLogger(self.path)
        # Aus runs 4 drafting turns, all on ollama
        for _ in range(4):
            t, r = _fake_result(engine="ollama", model="qwen",
                                tclass="drafting", user="aus")
            logger.log(t, r)

        proposals = orient(logger.read_all(), min_support=3)
        self.assertTrue(any(
            "aus" in p.name and "drafting" in p.name for p in proposals
        ))

    def test_orient_detects_missed_cloud_fallback(self):
        logger = TurnLogger(self.path)
        for _ in range(3):
            t, r = _fake_result(engine="ollama", ctx_tokens=12000)
            logger.log(t, r)

        proposals = orient(logger.read_all(), min_support=3)
        names = [p.name for p in proposals]
        self.assertTrue(any("long context" in n for n in names))

    def test_proposal_to_markdown_is_policy_compileable(self):
        from tugboat.policy import parse_policy

        proposal = RuleProposal(
            name="example rule",
            rationale="test",
            when="task_class == drafting",
            then=["model.engine = ollama"],
            support=3,
            confidence=0.3,
        )
        md = "# Defaults\n- model.engine: cloud\n\n" + proposal.to_markdown()
        policy = parse_policy(md)
        self.assertEqual(len(policy.rules), 1)
        self.assertEqual(policy.rules[0].name, "example rule")

    def test_empty_log_returns_no_proposals(self):
        self.assertEqual(orient([]), [])


class RegretTests(unittest.TestCase):
    def test_regret_shows_model_diff(self):
        from tugboat.policy import parse_policy
        from tugboat.adapter import Tugboat
        from tugboat.channels import (
            InMemoryMemoryChannel, DictSkillChannel, MultiEngineModelChannel,
        )
        from tugboat.engines import MockEngine

        current = parse_policy(
            "# Defaults\n- model.engine: ollama\n- model.model: qwen\n"
        )
        proposed = parse_policy(
            "# Defaults\n- model.engine: cloud\n- model.model: claude-opus-4-6\n"
        )
        mock = MockEngine()
        tug = Tugboat(
            policy=current,
            memory=InMemoryMemoryChannel(),
            skills=DictSkillChannel(),
            models=MultiEngineModelChannel({"ollama": mock, "cloud": mock}),
        )
        diff = tug.regret(Turn(text="x"), proposed)
        self.assertTrue(diff["changed"])
        self.assertIn("model", diff["diffs"])
        self.assertEqual(diff["diffs"]["model"]["before"], "ollama:qwen")
        self.assertEqual(diff["diffs"]["model"]["after"], "cloud:claude-opus-4-6")

    def test_regret_no_change_when_policies_equivalent(self):
        from tugboat.policy import parse_policy
        from tugboat.adapter import Tugboat
        from tugboat.channels import (
            InMemoryMemoryChannel, DictSkillChannel, MultiEngineModelChannel,
        )
        from tugboat.engines import MockEngine

        p = parse_policy("# Defaults\n- model.engine: ollama\n- model.model: qwen\n")
        tug = Tugboat(
            policy=p,
            memory=InMemoryMemoryChannel(),
            skills=DictSkillChannel(),
            models=MultiEngineModelChannel({"ollama": MockEngine()}),
        )
        diff = tug.regret(Turn(text="x"), p)
        self.assertFalse(diff["changed"])


class PersonalizationTests(unittest.TestCase):
    def test_user_field_matches_policy_condition(self):
        from tugboat.policy import parse_policy
        from tugboat.navigator import Navigator

        src = (
            "# Defaults\n"
            "- model.engine: ollama\n"
            "- model.model: qwen\n"
            "\n"
            "## Rule: aus prefers cloud for reasoning\n"
            "when: user == aus and task_class == reasoning\n"
            "then:\n"
            "  - model.engine = cloud\n"
            "  - model.model = claude-opus-4-6\n"
        )
        nav = Navigator(parse_policy(src))

        generic = nav.route(Turn(text="q", task_class="reasoning"))
        self.assertEqual(generic.model.engine, "ollama")

        personal = nav.route(Turn(text="q", task_class="reasoning", user="aus"))
        self.assertEqual(personal.model.engine, "cloud")

    def test_context_field_matches_policy_condition(self):
        from tugboat.policy import parse_policy
        from tugboat.navigator import Navigator

        src = (
            "# Defaults\n"
            "- model.engine: ollama\n"
            "\n"
            "## Rule: morning brief uses cloud\n"
            "when: context == morning_brief\n"
            "then:\n"
            "  - model.engine = cloud\n"
        )
        nav = Navigator(parse_policy(src))

        d = nav.route(Turn(text="brief me", context="morning_brief"))
        self.assertEqual(d.model.engine, "cloud")


if __name__ == "__main__":
    unittest.main()
