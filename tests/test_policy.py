import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from tugboat.policy import parse_policy, load_policy


SIMPLE = """
# Defaults
- model.engine: ollama
- model.model: qwen2.5:14b-instruct
- memory.budget_tokens: 1500

## Rule: cloud for long context
when: context_tokens > 8000
then:
  - model.engine = cloud
  - model.model = claude-opus-4-6

## Rule: confirm external
when: external_action
then:
  - confirm_before_execute = true

## Subagent: researcher
- goal: research stuff
- scoped_tools: [web_search]
- max_iterations: 4
"""


class PolicyParseTests(unittest.TestCase):
    def test_defaults_parsed(self):
        p = parse_policy(SIMPLE)
        self.assertEqual(p.defaults["model.engine"], "ollama")
        self.assertEqual(p.defaults["model.model"], "qwen2.5:14b-instruct")
        self.assertEqual(p.defaults["memory.budget_tokens"], 1500)

    def test_rules_parsed(self):
        p = parse_policy(SIMPLE)
        self.assertEqual(
            p.rule_names(),
            ["cloud for long context", "confirm external"],
        )

    def test_subagent_parsed(self):
        p = parse_policy(SIMPLE)
        self.assertIn("researcher", p.subagents)
        self.assertEqual(p.subagents["researcher"]["max_iterations"], 4)
        self.assertEqual(
            p.subagents["researcher"]["scoped_tools"],
            ["web_search"],
        )

    def test_ben_policy_file_loads(self):
        policy_path = os.path.join(ROOT, "examples", "ben_policy.md")
        p = load_policy(policy_path)
        self.assertGreater(len(p.rules), 0)
        self.assertIn("researcher", p.subagents)


if __name__ == "__main__":
    unittest.main()
