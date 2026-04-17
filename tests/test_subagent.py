import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from tugboat.channels import InMemoryMemoryChannel, DictSkillChannel, MultiEngineModelChannel
from tugboat.engines import MockEngine
from tugboat.navigator import Turn
from tugboat.subagent import Subagent, SubagentSpec, merge_subagent_result, _extract_memory_writes


class SubagentTests(unittest.TestCase):
    def setUp(self):
        self.memory = InMemoryMemoryChannel(
            slices={"identity": "I am BEN.", "recent_daily": "Today: tested Tugboat."},
            identity="I am BEN.",
        )
        self.skills = DictSkillChannel({
            "web_search": lambda t: ("web_search", t.text),
        })
        self.engine = MockEngine()
        self.models = MultiEngineModelChannel({"mock": self.engine, "ollama": self.engine})

    def test_subagent_runs_and_returns_output(self):
        spec = SubagentSpec(
            name="researcher",
            goal="test",
            scoped_tools=["web_search"],
            memory_scope="read_parent",
        )
        sub = Subagent(
            spec=spec,
            model_channel=self.models,
            memory_channel=self.memory,
            skill_channel=self.skills,
            parent_engine="mock",
            parent_model="m0",
        )
        result = sub.run(Turn(text="find something"))
        self.assertIsNone(result.error)
        self.assertIn("[mock:m0]", result.output)
        self.assertEqual(result.iterations, 1)
        self.assertEqual(len(result.skill_calls), 1)

    def test_memory_write_extraction(self):
        text = (
            "some answer. <memory slice=\"long_term\">a fact</memory> "
            "also <memory slice=\"notes\" kind=\"replace\">fresh</memory>"
        )
        writes = _extract_memory_writes(text)
        self.assertEqual(len(writes), 2)
        self.assertEqual(writes[0].slice_name, "long_term")
        self.assertEqual(writes[0].kind, "append")
        self.assertEqual(writes[1].slice_name, "notes")
        self.assertEqual(writes[1].kind, "replace")

    def test_merge_memory_updates_memory_channel(self):
        self.engine.override(
            "[subagent:researcher]",
            "answer. <memory slice=\"long_term\">fact A</memory>",
        )
        spec = SubagentSpec(
            name="researcher",
            scoped_tools=[],
            memory_scope="isolated",
            merge_strategy="merge_memory",
        )
        sub = Subagent(spec, self.models, self.memory, self.skills,
                       parent_engine="mock", parent_model="m0")
        result = sub.run(Turn(text="q"))
        merge_subagent_result(result, "merge_memory", self.memory)

        loaded = self.memory.load(["long_term"], 2000)
        self.assertEqual(len(loaded), 1)
        self.assertIn("fact A", loaded[0].content)

    def test_append_log_does_not_mutate_memory(self):
        self.engine.override(
            "[subagent:triager]",
            "<memory slice=\"long_term\">should not apply</memory>",
        )
        spec = SubagentSpec(
            name="triager",
            scoped_tools=[],
            memory_scope="isolated",
            merge_strategy="append_log",
        )
        sub = Subagent(spec, self.models, self.memory, self.skills,
                       parent_engine="mock", parent_model="m0")
        result = sub.run(Turn(text="q"))

        log = []
        before = dict(getattr(self.memory, "_slices", {}))
        merge_subagent_result(result, "append_log", self.memory, activity_log=log)
        after = dict(getattr(self.memory, "_slices", {}))

        self.assertEqual(before, after)  # memory unchanged
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["event"], "subagent_memory_pending")

    def test_return_only_discards_memory(self):
        self.engine.override(
            "[subagent:x]",
            "<memory slice=\"long_term\">discarded</memory>",
        )
        spec = SubagentSpec(name="x", scoped_tools=[], merge_strategy="return_only")
        sub = Subagent(spec, self.models, self.memory, self.skills,
                       parent_engine="mock", parent_model="m0")
        result = sub.run(Turn(text="q"))
        before = dict(getattr(self.memory, "_slices", {}))
        merge_subagent_result(result, "return_only", self.memory)
        after = dict(getattr(self.memory, "_slices", {}))
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
