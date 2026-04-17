"""Basic usage demo for Tugboat.

Run: python -m examples.basic_usage   (from the tugboat project root)

Shows:
  1. Load a declarative policy from markdown.
  2. Construct channels (memory, skills, models) with sensible defaults.
  3. Route a few turns and print the decisions.
  4. Execute one turn end-to-end against a mock engine.
  5. Execute a research turn to show subagent delegation.
"""

import sys
import os

# Allow `python examples/basic_usage.py` from the project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tugboat import (
    Tugboat,
    Turn,
    load_policy,
)
from tugboat.channels import (
    InMemoryMemoryChannel,
    DictSkillChannel,
    MultiEngineModelChannel,
)
from tugboat.engines import MockEngine


def build_demo_tug():
    policy_path = os.path.join(os.path.dirname(__file__), "ben_policy.md")
    policy = load_policy(policy_path)

    memory = InMemoryMemoryChannel(
        slices={
            "recent_daily": "2026-04-16: Austin renamed the architecture to Tugboat.",
            "long_term": "Captain = Austin. Navigator = BEN.",
            "project": "openclaws = the universal channel navigator project.",
        },
        identity=(
            "You are B.E.N., the Navigator. Austin is the Captain. "
            "You route decisions across memory, subagents, skills, and models. "
            "You do not generate the cargo; you guide the ship."
        ),
    )

    skills = DictSkillChannel({
        "formatter": lambda turn: ("formatted", turn.text),
        "memory_writer": lambda turn: ("memory_writer", "would write to memory"),
        "web_search": lambda turn: ("web_search", f"would search: {turn.text[:40]}"),
        "read_file": lambda turn: ("read_file", "would read file"),
        "send_email": lambda turn: ("send_email", "would send email"),
    })

    mock_cloud = MockEngine()
    mock_cloud.override(
        "[subagent:researcher]",
        "Short sourced summary (cloud mock).\n"
        "<memory slice=\"long_term\">Researched 2026-04-16: Tugboat thesis validated.</memory>",
    )
    mock_local = MockEngine()

    models = MultiEngineModelChannel({
        "cloud": mock_cloud,
        "ollama": mock_local,
        "mock": mock_local,
    })

    return Tugboat(policy=policy, memory=memory, skills=skills, models=models)


def main():
    tug = build_demo_tug()

    turns = [
        Turn(text="Draft a tweet announcing Tugboat.", task_class="drafting"),
        Turn(text="Explain the Meta-Harness paper's main finding.",
             task_class="reasoning", context_tokens=12000),
        Turn(text="Research harness-as-product discourse in Q1 2026.",
             task_class="research"),
        Turn(text="Send this reply on Twitter.", external_action=True),
    ]

    for i, turn in enumerate(turns, 1):
        print(f"\n=== turn {i}: {turn.text!r} ===")
        print(tug.explain(turn))

    print("\n\n=== executing the drafting turn ===")
    result = tug.execute(turns[0])
    print(result.summary())
    print(f"output: {result.output!r}")

    print("\n\n=== executing the research turn (subagent) ===")
    result = tug.execute(turns[2])
    print(result.summary())
    if result.subagent_result:
        print(f"subagent output: {result.subagent_result.output[:120]!r}")
        print(f"subagent memory_writes: {len(result.subagent_result.memory_writes)}")

    # Confirm the researcher's merge_memory strategy actually updated the store
    internal = getattr(tug.memory, "_slices", {})
    print(f"\nlong_term slice after research:\n  {internal.get('long_term')!r}")

    print("\n\n=== confirming external-action gate ===")
    try:
        tug.execute(turns[3])
    except PermissionError as exc:
        print(f"blocked as expected: {exc}")


if __name__ == "__main__":
    main()
