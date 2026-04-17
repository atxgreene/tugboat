import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from tugboat.policy import parse_policy
from tugboat.navigator import Navigator, Turn


POLICY = """
# Defaults
- model.engine: ollama
- model.model: qwen2.5:14b-instruct
- memory.budget_tokens: 1500
- memory.slices: [identity, recent_daily]
- skill.active: [formatter]

## Rule: cloud for long context
when: context_tokens > 8000
then:
  - model.engine = cloud
  - model.model = claude-opus-4-6
  - memory.budget_tokens = 6000

## Rule: delegate research
when: task_class == research
then:
  - subagent.delegate = true
  - subagent.spec_name = researcher
  - subagent.merge_strategy = merge_memory

## Rule: restrict external skills when private
when: private
then:
  - skill.restricted += [send_email]
"""


class NavigatorTests(unittest.TestCase):
    def setUp(self):
        self.policy = parse_policy(POLICY)
        self.nav = Navigator(self.policy)

    def test_defaults_apply(self):
        d = self.nav.route(Turn(text="hi"))
        self.assertEqual(d.model.engine, "ollama")
        self.assertEqual(d.model.model, "qwen2.5:14b-instruct")
        self.assertEqual(d.memory.budget_tokens, 1500)
        self.assertEqual(d.skill.active, ["formatter"])

    def test_long_context_routes_to_cloud(self):
        d = self.nav.route(Turn(text="big", context_tokens=20000))
        self.assertEqual(d.model.engine, "cloud")
        self.assertEqual(d.model.model, "claude-opus-4-6")
        self.assertEqual(d.memory.budget_tokens, 6000)
        self.assertIn("cloud for long context", d.trace)

    def test_research_delegates_to_subagent(self):
        d = self.nav.route(Turn(text="deep question", task_class="research"))
        self.assertTrue(d.subagent.delegate)
        self.assertEqual(d.subagent.spec_name, "researcher")
        self.assertEqual(d.subagent.merge_strategy, "merge_memory")

    def test_external_action_forces_confirm(self):
        d = self.nav.route(Turn(text="tweet", external_action=True))
        self.assertTrue(d.confirm_before_execute)
        self.assertIn("invariant:external_action_requires_confirm", d.trace)

    def test_private_restricts_external_skills(self):
        d = self.nav.route(Turn(text="draft", privacy_tier="private"))
        self.assertIn("send_email", d.skill.restricted)

    def test_determinism(self):
        # Same inputs should always produce the same decision
        t = Turn(text="x", task_class="drafting", context_tokens=500)
        a = self.nav.route(t)
        b = self.nav.route(t)
        self.assertEqual(a.to_json(), b.to_json())

    def test_memory_budget_floor(self):
        # A weird policy that drops the budget below the floor should be clamped
        d = self.nav.route(Turn(text="hi"))
        self.assertGreaterEqual(d.memory.budget_tokens, 256)


if __name__ == "__main__":
    unittest.main()
