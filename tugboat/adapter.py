"""Tugboat — the top-level adapter / driver.

Two modes:

  1. Plugin mode (just the decision):
       tug = Tugboat(policy=..., memory=..., skills=..., models=...)
       decision = tug.route(turn)
       # hand the decision back to your host harness

  2. Driver mode (end-to-end turn):
       response = tug.execute(turn)
       # Tugboat assembles the prompt, calls the model, optionally spawns a
       # subagent, and returns the final text plus the decision trail

Tugboat is plugin-shaped. You give it pluggable Channels for memory, skills,
and models; it gives you a routing layer you can pin to a policy file.

The execute() path is small. It's intentionally not a full ReAct loop — the
point is to showcase the routing; a host harness can wrap Tugboat with its own
loop if it wants one. The subagent spawn IS a first-class part of execute()
because that's where Tugboat differentiates.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
import time

from .navigator import Navigator, RoutingDecision, Turn, ModelDecision
from .policy import Policy, load_policy
from .channels import (
    MemoryChannel,
    SubagentChannel,
    SkillChannel,
    ModelChannel,
    InMemoryMemoryChannel,
    DictSkillChannel,
    MultiEngineModelChannel,
)
from .subagent import (
    Subagent,
    SubagentSpec,
    SubagentResult,
    merge_subagent_result,
)


@dataclass
class TurnResult:
    turn: Turn
    decision: RoutingDecision
    output: str = ""
    engine_response: Optional[Any] = None
    subagent_result: Optional[SubagentResult] = None
    activity_log: List[Dict[str, Any]] = field(default_factory=list)
    latency_ms: float = 0.0

    def summary(self) -> str:
        pieces = [self.decision.summary()]
        if self.subagent_result:
            pieces.append(f"subagent:{self.subagent_result.name}")
        pieces.append(f"{int(self.latency_ms)}ms")
        return " | ".join(pieces)


class Tugboat:
    """Top-level routing driver.

    Channels are required:
      memory  — a MemoryChannel (InMemoryMemoryChannel is fine for defaults)
      skills  — a SkillChannel (DictSkillChannel is fine)
      models  — a ModelChannel (MultiEngineModelChannel wrapping engines)

    The Policy holds defaults, rules, and subagent specs. A single Tugboat can
    handle many turns with the same policy; construct once, call many times.
    """

    def __init__(
        self,
        policy: Policy,
        memory: MemoryChannel,
        skills: SkillChannel,
        models: ModelChannel,
    ):
        self.policy = policy
        self.memory = memory
        self.skills = skills
        self.models = models
        self.navigator = Navigator(policy)

    # -------- convenience constructors ---------------------------------------

    @classmethod
    def from_files(
        cls,
        policy_path: str,
        memory: Optional[MemoryChannel] = None,
        skills: Optional[SkillChannel] = None,
        models: Optional[ModelChannel] = None,
    ) -> "Tugboat":
        policy = load_policy(policy_path)
        return cls(
            policy=policy,
            memory=memory or InMemoryMemoryChannel(),
            skills=skills or DictSkillChannel(),
            models=models or MultiEngineModelChannel(),
        )

    # -------- public API -----------------------------------------------------

    def route(self, turn: Turn) -> RoutingDecision:
        """Plugin-mode call: return the decision without executing it."""
        return self.navigator.route(turn)

    def execute(self, turn: Turn, *, confirm_callback=None) -> TurnResult:
        """Driver-mode call: route, assemble, dispatch, and return TurnResult.

        If decision.confirm_before_execute is True, confirm_callback(decision)
        must return truthy or execute() raises PermissionError.
        """
        t0 = time.time()
        decision = self.navigator.route(turn)
        result = TurnResult(turn=turn, decision=decision)

        if decision.confirm_before_execute:
            if confirm_callback is None or not confirm_callback(decision):
                raise PermissionError(
                    f"turn requires confirmation; none given. "
                    f"decision: {decision.summary()}"
                )

        if decision.subagent.delegate and decision.subagent.spec_name:
            result.subagent_result = self._run_subagent(turn, decision)
            if result.subagent_result and not result.subagent_result.error:
                result.output = result.subagent_result.output
                merge_subagent_result(
                    result.subagent_result,
                    decision.subagent.merge_strategy,
                    self.memory,
                    activity_log=result.activity_log,
                )
        else:
            # Main-path execution
            prompt = self._assemble_prompt(turn, decision)
            response = self.models.call(decision.model, prompt)
            result.engine_response = response
            result.output = response.text

        result.latency_ms = (time.time() - t0) * 1000.0
        return result

    def explain(self, turn: Turn) -> str:
        """Human-readable 'what would you do' trace. Useful for evals."""
        decision = self.navigator.route(turn)
        lines = [
            f"Tugboat routing for turn: {turn.text[:60]!r}",
            f"  model:    {decision.model.engine} :: {decision.model.model}",
            f"  reason:   {decision.model.reason or '(default)'}",
            f"  memory:   {decision.memory.slices} budget={decision.memory.budget_tokens}",
            f"  skills:   active={decision.skill.active} restricted={decision.skill.restricted}",
            f"  subagent: {'→ ' + decision.subagent.spec_name if decision.subagent.delegate else '(none)'}",
            f"  confirm:  {decision.confirm_before_execute}",
            f"  trace:    {decision.trace}",
        ]
        return "\n".join(lines)

    def regret(self, turn: Turn, proposed_policy: Policy) -> Dict[str, Any]:
        """Compare current policy's decision vs. a proposed policy's decision.

        Returns a dict describing which axes differ. Used to review proposed
        policy diffs before accepting them — the 'PR diff for routing' UX.
        """
        current = self.navigator.route(turn)
        proposed = Navigator(proposed_policy).route(turn)

        diffs: Dict[str, Any] = {}
        if current.model.engine != proposed.model.engine or current.model.model != proposed.model.model:
            diffs["model"] = {
                "before": f"{current.model.engine}:{current.model.model}",
                "after":  f"{proposed.model.engine}:{proposed.model.model}",
            }
        if current.memory.slices != proposed.memory.slices or current.memory.budget_tokens != proposed.memory.budget_tokens:
            diffs["memory"] = {
                "before": {"slices": current.memory.slices, "budget": current.memory.budget_tokens},
                "after":  {"slices": proposed.memory.slices, "budget": proposed.memory.budget_tokens},
            }
        if current.skill.active != proposed.skill.active or current.skill.restricted != proposed.skill.restricted:
            diffs["skills"] = {
                "before": {"active": current.skill.active, "restricted": current.skill.restricted},
                "after":  {"active": proposed.skill.active, "restricted": proposed.skill.restricted},
            }
        if (current.subagent.delegate != proposed.subagent.delegate
                or current.subagent.spec_name != proposed.subagent.spec_name):
            diffs["subagent"] = {
                "before": {"delegate": current.subagent.delegate, "spec": current.subagent.spec_name},
                "after":  {"delegate": proposed.subagent.delegate, "spec": proposed.subagent.spec_name},
            }
        return {
            "turn": turn.text[:80],
            "changed": bool(diffs),
            "diffs": diffs,
            "current_trace":  current.trace,
            "proposed_trace": proposed.trace,
        }

    # -------- internals ------------------------------------------------------

    def _assemble_prompt(self, turn: Turn, decision: RoutingDecision) -> str:
        parts: List[str] = []

        if decision.memory.include_identity:
            identity = self.memory.identity_preamble()
            if identity:
                parts.append(f"[IDENTITY]\n{identity}")

        loaded = self.memory.load(decision.memory.slices, decision.memory.budget_tokens)
        if loaded:
            mem_block = "\n\n".join(f"[{m.name}]\n{m.content}" for m in loaded)
            parts.append(f"[MEMORY]\n{mem_block}")

        skills = self.skills.resolve(
            decision.skill.active, decision.skill.restricted, turn
        )
        if skills:
            skill_lines = "\n".join(f"  - {s.name}: {s.preview}" for s in skills)
            parts.append(f"[SKILLS IN SCOPE]\n{skill_lines}")

        parts.append(f"[TURN]\n{turn.text}")
        return "\n\n".join(parts)

    def _run_subagent(self, turn: Turn, decision: RoutingDecision) -> SubagentResult:
        spec_name = decision.subagent.spec_name or ""
        raw_spec = self.policy.subagents.get(spec_name)
        if raw_spec is None:
            return SubagentResult(
                name=spec_name,
                output="",
                error=f"no subagent spec named {spec_name!r} in policy",
            )

        spec = SubagentSpec.from_policy_dict(spec_name, raw_spec)
        subagent = Subagent(
            spec=spec,
            model_channel=self.models,
            memory_channel=self.memory,
            skill_channel=self.skills,
            parent_engine=decision.model.engine,
            parent_model=decision.model.model,
        )
        return subagent.run(turn)
