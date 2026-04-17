"""Subagent — the spawn/merge primitive.

This is the axis everyone gestures at and nobody ships cleanly. The contract:

  parent decides to delegate (via RoutingDecision.subagent.delegate == True)
  → Tugboat spawns a Subagent with a SubagentSpec from the policy
  → the subagent runs in isolation: its own memory scope, scoped tools,
    optionally a different model
  → the subagent returns a SubagentResult with:
      - an output string
      - pending memory writes (to be merged per merge_strategy)
      - a record of tool/skill calls it made
  → the parent merges according to the spec's merge_strategy:
      "return_only"   — discard memory writes, use output only
      "merge_memory"  — apply memory writes to parent's memory channel
      "append_log"    — stash memory writes in parent's L1 activity log

The subagent gets its own mini-Tugboat under the hood. It's turtles. The
isolation boundary is enforced by passing a restricted set of skills and a
scoped MemoryChannel view.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
import time


@dataclass
class SubagentSpec:
    name: str
    goal: str = ""
    scoped_tools: List[str] = field(default_factory=list)
    restricted_tools: List[str] = field(default_factory=list)
    model_engine: Optional[str] = None          # override parent's engine
    model_id: Optional[str] = None               # override parent's model
    memory_scope: str = "read_parent"            # "isolated" | "read_parent" | "full"
    max_iterations: int = 5
    merge_strategy: str = "return_only"
    budget_tokens: int = 1500

    @classmethod
    def from_policy_dict(cls, name: str, raw: Dict[str, Any]) -> "SubagentSpec":
        return cls(
            name=name,
            goal=str(raw.get("goal", "")),
            scoped_tools=list(raw.get("scoped_tools", []) or []),
            restricted_tools=list(raw.get("restricted_tools", []) or []),
            model_engine=raw.get("model_engine"),
            model_id=raw.get("model_id"),
            memory_scope=str(raw.get("memory_scope", "read_parent")),
            max_iterations=int(raw.get("max_iterations", 5)),
            merge_strategy=str(raw.get("merge_strategy", "return_only")),
            budget_tokens=int(raw.get("budget_tokens", 1500)),
        )


@dataclass
class MemoryWrite:
    slice_name: str
    content: str
    kind: str = "append"  # "append" | "replace"


@dataclass
class SubagentResult:
    name: str
    output: str
    memory_writes: List[MemoryWrite] = field(default_factory=list)
    skill_calls: List[Dict[str, Any]] = field(default_factory=list)
    iterations: int = 0
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    error: Optional[str] = None


class Subagent:
    """One-shot agent spawned by the parent Tugboat for a scoped task.

    This is intentionally simple: it asks the model once (or up to max_iterations
    times) with a scoped prompt, collects tool/skill results, and returns. It
    does not implement a full ReAct loop — real production harnesses would — but
    the spawn/merge *contract* is first-class and testable.
    """

    def __init__(
        self,
        spec: SubagentSpec,
        model_channel: "ModelChannel",
        memory_channel: "MemoryChannel",
        skill_channel: "SkillChannel",
        parent_engine: str = "mock",
        parent_model: str = "local-default",
    ):
        self.spec = spec
        self.model_channel = model_channel
        self.memory_channel = memory_channel
        self.skill_channel = skill_channel
        self.parent_engine = parent_engine
        self.parent_model = parent_model

    def run(self, turn) -> SubagentResult:
        from .navigator import ModelDecision

        result = SubagentResult(name=self.spec.name, output="")
        try:
            # Scoped memory load — the subagent can only see allowed slices
            mem_slices = self._scoped_memory_slices(turn)
            memory = self.memory_channel.load(mem_slices, self.spec.budget_tokens)
            memory_text = "\n\n".join(f"[{m.name}]\n{m.content}" for m in memory)

            # Scoped skills
            scoped_skills = self.skill_channel.resolve(
                active=self.spec.scoped_tools,
                restricted=self.spec.restricted_tools,
                turn=turn,
            )

            # Build the subagent prompt
            prompt = self._build_prompt(turn, memory_text, scoped_skills)

            # Call the model
            model_decision = ModelDecision(
                engine=self.spec.model_engine or self.parent_engine,
                model=self.spec.model_id or self.parent_model,
                max_tokens=self.spec.budget_tokens,
                reason=f"subagent:{self.spec.name}",
            )
            response = self.model_channel.call(model_decision, prompt)
            result.output = response.text
            result.iterations = 1

            # Record which skills were in scope (fire-and-record semantics;
            # a production harness would parse model output and invoke them).
            result.skill_calls = [
                {"skill": s.name, "preview": s.preview} for s in scoped_skills
            ]

            # Subagents can choose to write to memory by emitting a block
            #   <memory slice="...">...</memory>
            # in their output. We scrape those out and return them as
            # pending writes — the parent merges per merge_strategy.
            result.memory_writes = _extract_memory_writes(response.text)

        except Exception as exc:
            result.error = f"{type(exc).__name__}: {exc}"
        finally:
            result.finished_at = time.time()

        return result

    def _scoped_memory_slices(self, turn) -> List[str]:
        # Demo-grade scoping. In a real system this would consult an ACL.
        if self.spec.memory_scope == "isolated":
            return []
        if self.spec.memory_scope == "full":
            return ["identity", "recent_daily", "long_term", "project"]
        # read_parent default: identity + recent daily only
        return ["identity", "recent_daily"]

    def _build_prompt(self, turn, memory_text: str, skills) -> str:
        goal = self.spec.goal or "Handle the turn below and return a concise answer."
        skill_lines = "\n".join(f"  - {s.name}: {s.preview}" for s in skills) or "  (none)"
        return (
            f"[subagent:{self.spec.name}]\n"
            f"GOAL: {goal}\n\n"
            f"AVAILABLE SKILLS:\n{skill_lines}\n\n"
            f"MEMORY:\n{memory_text or '(none)'}\n\n"
            f"TURN:\n{turn.text}\n"
        )


# ---------- memory-write extraction ------------------------------------------

import re

_MEM_RE = re.compile(
    r"<memory\s+slice=\"(?P<slice>[^\"]+)\"(?:\s+kind=\"(?P<kind>[^\"]+)\")?>"
    r"(?P<body>.*?)"
    r"</memory>",
    re.DOTALL,
)


def _extract_memory_writes(text: str) -> List[MemoryWrite]:
    writes: List[MemoryWrite] = []
    for m in _MEM_RE.finditer(text or ""):
        writes.append(
            MemoryWrite(
                slice_name=m.group("slice"),
                content=m.group("body").strip(),
                kind=m.group("kind") or "append",
            )
        )
    return writes


# ---------- merge -------------------------------------------------------------

def merge_subagent_result(
    result: SubagentResult,
    merge_strategy: str,
    memory_channel,
    activity_log: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """Apply a subagent's pending effects according to strategy."""
    if result.error:
        if activity_log is not None:
            activity_log.append({
                "event": "subagent_error",
                "subagent": result.name,
                "error": result.error,
            })
        return

    if merge_strategy == "return_only":
        return

    if merge_strategy == "append_log" and activity_log is not None:
        for w in result.memory_writes:
            activity_log.append({
                "event": "subagent_memory_pending",
                "subagent": result.name,
                "slice": w.slice_name,
                "content": w.content,
                "kind": w.kind,
            })
        return

    if merge_strategy == "merge_memory":
        for w in result.memory_writes:
            _apply_memory_write(memory_channel, w)
        return

    raise ValueError(f"unknown merge_strategy: {merge_strategy!r}")


def _apply_memory_write(memory_channel, write: MemoryWrite) -> None:
    # Best-effort: if the channel supports `add`, use it; else skip.
    if hasattr(memory_channel, "add"):
        if write.kind == "replace":
            memory_channel.add(write.slice_name, write.content)
            return
        # append semantics
        existing = ""
        internal = getattr(memory_channel, "_slices", None)
        if isinstance(internal, dict):
            existing = internal.get(write.slice_name, "")
        sep = "\n\n" if existing else ""
        memory_channel.add(write.slice_name, existing + sep + write.content)
