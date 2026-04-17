"""Navigator — the decision-only routing primitive.

The Navigator takes a Turn and a Policy and produces a RoutingDecision without
executing it. That separation is deliberate: route() is pure, execute() has
side effects. A consumer can call route() to inspect what Tugboat *would* do,
or call Tugboat.execute() to run it.

A RoutingDecision is the four-axis answer:
  memory   — which memory slices to load, with a byte budget
  subagent — whether to delegate, and to which named subagent spec
  skill    — which skills are in scope for this turn, in priority order
  model    — which engine / model id to call

The decision is an artifact, not a call. It can be logged, diffed, replayed,
and evaluated against a no-routing baseline. That is how you measure whether
the harness thesis is a principle or an aesthetic.
"""

from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any
import json
import time


@dataclass
class Turn:
    """A single unit of work for the Navigator to route."""
    text: str
    task_class: str = "general"        # hint; policy may override via classifier
    context_tokens: int = 0            # approximate incoming context size
    external_action: bool = False      # true if this turn requires outbound side effects
    privacy_tier: str = "normal"       # "normal" | "private" | "public"
    user: Optional[str] = None         # stable user id, for per-user rules
    context: Optional[str] = None      # free-form label (e.g. "morning_brief")
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MemoryDecision:
    slices: List[str] = field(default_factory=list)   # named memory sources to load
    budget_tokens: int = 2000                          # budget across all slices
    include_identity: bool = True                      # always-on identity lock


@dataclass
class SubagentDecision:
    delegate: bool = False
    spec_name: Optional[str] = None
    merge_strategy: str = "return_only"


@dataclass
class SkillDecision:
    active: List[str] = field(default_factory=list)   # ordered; first match wins
    restricted: List[str] = field(default_factory=list)  # explicitly blocked


@dataclass
class ModelDecision:
    engine: str = "mock"              # "mock" | "ollama" | custom
    model: str = "local-default"
    max_tokens: int = 512
    temperature: float = 0.3
    reason: str = ""                  # human-readable justification for logs


@dataclass
class RoutingDecision:
    memory: MemoryDecision = field(default_factory=MemoryDecision)
    subagent: SubagentDecision = field(default_factory=SubagentDecision)
    skill: SkillDecision = field(default_factory=SkillDecision)
    model: ModelDecision = field(default_factory=ModelDecision)
    confirm_before_execute: bool = False
    trace: List[str] = field(default_factory=list)    # ordered list of policy rules fired
    decided_at: float = field(default_factory=time.time)

    def to_json(self, include_timestamp: bool = False) -> str:
        d = asdict(self)
        if not include_timestamp:
            d.pop("decided_at", None)
        return json.dumps(d, indent=2)

    def summary(self) -> str:
        """Single-line human-readable summary — good for logs."""
        subagent_note = f" → subagent[{self.subagent.spec_name}]" if self.subagent.delegate else ""
        confirm_note = " [CONFIRM]" if self.confirm_before_execute else ""
        return (
            f"route: model={self.model.engine}:{self.model.model} "
            f"skills={self.skill.active} "
            f"mem={len(self.memory.slices)}slices/{self.memory.budget_tokens}tok"
            f"{subagent_note}{confirm_note}"
        )


class Navigator:
    """Pure routing. Takes Turn + Policy, returns RoutingDecision. No side effects.

    The Navigator evaluates the policy's rules against the turn, in order, and
    accumulates mutations to a starting decision. The final decision is the
    reduction of (starting_decision, *matched_rules).

    This is deliberately deterministic: the same (turn, policy) pair always
    produces the same RoutingDecision. That's what makes it replayable and
    testable.
    """

    def __init__(self, policy: "Policy"):
        self.policy = policy

    def route(self, turn: Turn) -> RoutingDecision:
        decision = self._initial_decision()

        for rule in self.policy.rules:
            if rule.matches(turn, decision):
                rule.apply(decision)
                decision.trace.append(rule.name)

        # Final clamps / invariants that should always hold
        self._enforce_invariants(turn, decision)

        return decision

    def _initial_decision(self) -> RoutingDecision:
        """Start from policy defaults, then let rules mutate."""
        d = RoutingDecision()
        defaults = self.policy.defaults
        if "model.engine" in defaults:
            d.model.engine = defaults["model.engine"]
        if "model.model" in defaults:
            d.model.model = defaults["model.model"]
        if "memory.budget_tokens" in defaults:
            d.memory.budget_tokens = int(defaults["memory.budget_tokens"])
        if "memory.slices" in defaults:
            d.memory.slices = list(defaults["memory.slices"])
        if "skill.active" in defaults:
            d.skill.active = list(defaults["skill.active"])
        return d

    def _enforce_invariants(self, turn: Turn, decision: RoutingDecision) -> None:
        # External actions always require confirmation. No policy can override this.
        if turn.external_action:
            decision.confirm_before_execute = True
            decision.trace.append("invariant:external_action_requires_confirm")

        # Identity memory is always loaded. This is the Mnemosyne lock in miniature.
        decision.memory.include_identity = True

        # Budget floor. A turn with zero memory budget is almost certainly a bug.
        if decision.memory.budget_tokens < 256:
            decision.memory.budget_tokens = 256
