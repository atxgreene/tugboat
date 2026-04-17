import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from tugboat.adapter import Tugboat
from tugboat.policy import load_policy
from tugboat.navigator import Turn
from tugboat.channels import InMemoryMemoryChannel, DictSkillChannel, MultiEngineModelChannel
from tugboat.engines import MockEngine


def build_tug():
    policy_path = os.path.join(ROOT, "examples", "ben_policy.md")
    policy = load_policy(policy_path)
    memory = InMemoryMemoryChannel(
        slices={"identity": "BEN", "recent_daily": "today", "long_term": "seed"},
        identity="I am BEN, the Navigator.",
    )
    skills = DictSkillChannel({
        "formatter": lambda t: ("formatter", t.text),
        "memory_writer": lambda t: ("memory_writer", "w"),
        "web_search": lambda t: ("web_search", t.text),
        "read_file": lambda t: ("read_file", "r"),
        "send_email": lambda t: ("send_email", "e"),
    })
    mock_cloud = MockEngine()
    mock_cloud.override(
        "[subagent:researcher]",
        "cloud research: done. <memory slice=\"long_term\">fact learned</memory>",
    )
    mock_local = MockEngine()
    models = MultiEngineModelChannel({
        "cloud": mock_cloud,
        "ollama": mock_local,
        "mock": mock_local,
    })
    return Tugboat(policy=policy, memory=memory, skills=skills, models=models)


class AdapterTests(unittest.TestCase):
    def test_route_is_pure(self):
        tug = build_tug()
        turn = Turn(text="hi", task_class="drafting")
        d1 = tug.route(turn).to_json()
        d2 = tug.route(turn).to_json()
        self.assertEqual(d1, d2)

    def test_execute_drafting_returns_output(self):
        tug = build_tug()
        result = tug.execute(Turn(text="draft something", task_class="drafting"))
        self.assertTrue(result.output)
        self.assertFalse(result.decision.subagent.delegate)
        self.assertEqual(result.decision.model.engine, "ollama")

    def test_execute_long_context_routes_cloud(self):
        tug = build_tug()
        result = tug.execute(Turn(
            text="long input",
            task_class="drafting",
            context_tokens=20000,
        ))
        self.assertEqual(result.decision.model.engine, "cloud")
        self.assertEqual(result.decision.model.model, "claude-opus-4-6")

    def test_execute_research_uses_subagent_and_merges_memory(self):
        tug = build_tug()
        before = dict(getattr(tug.memory, "_slices", {}))
        result = tug.execute(Turn(text="research harness thesis", task_class="research"))
        self.assertIsNotNone(result.subagent_result)
        self.assertIsNone(result.subagent_result.error)
        after = dict(getattr(tug.memory, "_slices", {}))
        self.assertIn("fact learned", after["long_term"])
        self.assertNotEqual(before["long_term"], after["long_term"])

    def test_external_action_without_confirm_raises(self):
        tug = build_tug()
        with self.assertRaises(PermissionError):
            tug.execute(Turn(text="tweet this", external_action=True))

    def test_external_action_with_confirm_runs(self):
        tug = build_tug()
        result = tug.execute(
            Turn(text="tweet this", external_action=True),
            confirm_callback=lambda d: True,
        )
        self.assertTrue(result.output)

    def test_explain_contains_all_axes(self):
        tug = build_tug()
        text = tug.explain(Turn(text="x", task_class="drafting"))
        self.assertIn("model:", text)
        self.assertIn("memory:", text)
        self.assertIn("skills:", text)
        self.assertIn("subagent:", text)

    def test_identity_always_loaded(self):
        tug = build_tug()
        d = tug.route(Turn(text="x"))
        self.assertTrue(d.memory.include_identity)


if __name__ == "__main__":
    unittest.main()
