"""Observer — the JSONL log that feeds the OODA loop.

Tugboat's core router is pure and deterministic. The Observer is the optional
outer loop: it watches every turn, appends a structured record to a JSONL
file, and provides utilities for an offline "orient" step that mines the log
for patterns worth turning into new policy rules.

Design rules:
  - The core router does not depend on the observer. You can run Tugboat
    forever without one. Observability is opt-in.
  - The log is JSONL so it's grep-able, head-able, diff-able, and you can
    pipe it into anything.
  - The orient layer returns *proposals*, not mutations. The human decides.
    Self-modifying routers are where agent systems go to die.
"""

from dataclasses import dataclass, asdict, field
from typing import List, Dict, Any, Iterable, Optional, Callable
from pathlib import Path
import json
import time


@dataclass
class TurnRecord:
    """A single row in the Observer log.

    Enough fields to reconstruct the decision and its outcome, without
    requiring the original Turn/Policy to be present.
    """
    timestamp: float
    turn_text: str
    turn_task_class: str
    turn_context_tokens: int
    turn_external_action: bool
    turn_privacy_tier: str
    turn_user: Optional[str]
    turn_context: Optional[str]

    # Decision
    model_engine: str
    model_name: str
    model_reason: str
    memory_slices: List[str]
    memory_budget: int
    skill_active: List[str]
    skill_restricted: List[str]
    subagent_delegate: bool
    subagent_spec: Optional[str]
    subagent_merge: str
    confirm_required: bool
    trace: List[str]

    # Outcome (populated post-execute)
    output_preview: str = ""
    subagent_used: bool = False
    latency_ms: float = 0.0
    had_error: bool = False
    user_rating: Optional[int] = None   # filled in later via record_rating()
    user_note: str = ""


class TurnLogger:
    """Appends TurnRecord rows to a JSONL file.

    Usage:
        logger = TurnLogger("tugboat.jsonl")
        result = tug.execute(turn)
        logger.log(turn, result)

    Or plug it as a callback:
        tug = Tugboat(..., on_turn=logger.log)
    """

    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, turn, result) -> TurnRecord:
        record = _record_from_turn_result(turn, result)
        self.append(record)
        return record

    def append(self, record: TurnRecord) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")

    def read_all(self) -> List[TurnRecord]:
        if not self.path.exists():
            return []
        out: List[TurnRecord] = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                raw = json.loads(line)
                out.append(TurnRecord(**raw))
        return out


def _record_from_turn_result(turn, result) -> TurnRecord:
    d = result.decision
    output_preview = (result.output or "").strip().replace("\n", " ")[:240]
    return TurnRecord(
        timestamp=time.time(),
        turn_text=turn.text[:500],
        turn_task_class=turn.task_class,
        turn_context_tokens=int(turn.context_tokens),
        turn_external_action=bool(turn.external_action),
        turn_privacy_tier=turn.privacy_tier,
        turn_user=getattr(turn, "user", None),
        turn_context=getattr(turn, "context", None),
        model_engine=d.model.engine,
        model_name=d.model.model,
        model_reason=d.model.reason,
        memory_slices=list(d.memory.slices),
        memory_budget=int(d.memory.budget_tokens),
        skill_active=list(d.skill.active),
        skill_restricted=list(d.skill.restricted),
        subagent_delegate=bool(d.subagent.delegate),
        subagent_spec=d.subagent.spec_name,
        subagent_merge=d.subagent.merge_strategy,
        confirm_required=bool(d.confirm_before_execute),
        trace=list(d.trace),
        output_preview=output_preview,
        subagent_used=bool(result.subagent_result is not None),
        latency_ms=float(result.latency_ms),
        had_error=bool(result.subagent_result and result.subagent_result.error),
    )


# ----------- orient: mine the log for proposals ------------------------------

@dataclass
class RuleProposal:
    """A candidate markdown rule produced by scanning the log.

    This is a PROPOSAL. The caller is expected to review before accepting.
    """
    name: str
    rationale: str          # human-readable why
    when: str               # compileable condition
    then: List[str]         # compileable actions
    support: int            # number of historical turns this would have changed
    confidence: float       # 0..1; rough heuristic

    def to_markdown(self) -> str:
        lines = [
            f"## Rule: {self.name}",
            "",
            f"<!-- support={self.support}  confidence={self.confidence:.2f} -->",
            f"<!-- {self.rationale} -->",
            "",
            f"when: {self.when}",
            "then:",
        ]
        for a in self.then:
            lines.append(f"  - {a}")
        return "\n".join(lines)


def orient(
    records: Iterable[TurnRecord],
    *,
    min_support: int = 3,
) -> List[RuleProposal]:
    """Scan the log for simple, defensible patterns and propose rules.

    This is intentionally a small, explainable heuristic set — not an ML
    model. Each proposal comes with its support count so a human can sanity
    check it. Add new patterns here as real signals emerge.
    """
    records = list(records)
    if not records:
        return []

    proposals: List[RuleProposal] = []

    # Pattern 1: user consistently uses a task_class with a different engine
    # than the policy default → propose a per-user rule.
    per_user: Dict[str, Dict[str, List[TurnRecord]]] = {}
    for r in records:
        if not r.turn_user:
            continue
        per_user.setdefault(r.turn_user, {}).setdefault(r.turn_task_class, []).append(r)

    for user, by_class in per_user.items():
        for tclass, rs in by_class.items():
            if len(rs) < min_support:
                continue
            engines = {r.model_engine for r in rs}
            models = {r.model_name for r in rs}
            if len(engines) == 1 and len(models) == 1:
                engine = next(iter(engines))
                model = next(iter(models))
                proposals.append(RuleProposal(
                    name=f"{user} {tclass} uses {engine}",
                    rationale=f"{user} ran {len(rs)} {tclass!r} turns; all routed to {engine}/{model}.",
                    when=f"user == {user} and task_class == {tclass}",
                    then=[f"model.engine = {engine}", f"model.model = {model}"],
                    support=len(rs),
                    confidence=min(1.0, len(rs) / 10.0),
                ))

    # Pattern 2: turns that required confirmation were all external_action
    # → no-op (already enforced by invariant), skip.

    # Pattern 3: long-context turns where engine was NOT the long-context
    # cloud engine → propose raising the threshold or adding a rule.
    long_and_not_cloud = [r for r in records if r.turn_context_tokens > 8000
                          and r.model_engine != "cloud"]
    if len(long_and_not_cloud) >= min_support:
        proposals.append(RuleProposal(
            name="suggest cloud fallback for long context (missed)",
            rationale=(
                f"{len(long_and_not_cloud)} turns had >8k context but did not "
                f"route to cloud. Consider adding/strengthening a cloud rule."
            ),
            when="context_tokens > 8000",
            then=["model.engine = cloud"],
            support=len(long_and_not_cloud),
            confidence=min(1.0, len(long_and_not_cloud) / 10.0),
        ))

    return proposals
