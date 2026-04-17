"""Channels — the four routing axes, as pluggable interfaces.

Tugboat routes across four axes. Each axis is a Channel: a small interface that
the Navigator's RoutingDecision gets executed against. Channels are injected at
Tugboat construction time, so you can swap your memory store, skill registry,
or model engine without touching policy code.

This is where Tugboat stops being a concept and starts being a plugin. A host
harness supplies its own concrete Channels; Tugboat supplies the routing.
"""

from typing import Protocol, List, Dict, Any, Optional
from dataclasses import dataclass


# ---------- Memory channel ----------------------------------------------------

@dataclass
class MemorySlice:
    """A named chunk of context content loadable into a prompt."""
    name: str
    content: str
    tokens: int


class MemoryChannel(Protocol):
    """Anything that can answer 'given these slice names, return content under budget'."""

    def load(self, slices: List[str], budget_tokens: int) -> List[MemorySlice]: ...
    def identity_preamble(self) -> str: ...


class InMemoryMemoryChannel:
    """Dict-backed reference implementation. Useful for tests and simple use."""

    def __init__(
        self,
        slices: Optional[Dict[str, str]] = None,
        identity: str = "",
        tokens_per_char: float = 0.25,
    ):
        self._slices = slices or {}
        self._identity = identity
        self._tpc = tokens_per_char

    def add(self, name: str, content: str) -> None:
        self._slices[name] = content

    def load(self, slices: List[str], budget_tokens: int) -> List[MemorySlice]:
        out: List[MemorySlice] = []
        remaining = budget_tokens
        for name in slices:
            if name not in self._slices:
                continue
            content = self._slices[name]
            tokens = max(1, int(len(content) * self._tpc))
            if tokens > remaining:
                # Truncate to fit
                max_chars = max(0, int(remaining / self._tpc))
                content = content[:max_chars]
                tokens = remaining
            if tokens > 0:
                out.append(MemorySlice(name=name, content=content, tokens=tokens))
                remaining -= tokens
            if remaining <= 0:
                break
        return out

    def identity_preamble(self) -> str:
        return self._identity


# ---------- Subagent channel --------------------------------------------------

class SubagentChannel(Protocol):
    """Spawns named subagents by spec name. Real impl lives in subagent.py."""
    def spawn(self, spec_name: str, turn, parent_decision) -> "SubagentResult": ...


# ---------- Skill channel -----------------------------------------------------

@dataclass
class SkillMatch:
    name: str
    preview: str           # what the skill *would* do, for logs and confirmation
    callable_: Any         # zero-arg thunk returning the skill's result


class SkillChannel(Protocol):
    """Given active skill names and a turn, return the matching skill in order."""

    def resolve(self, active: List[str], restricted: List[str], turn) -> List[SkillMatch]: ...


class DictSkillChannel:
    """Reference impl. skills is a dict of name -> callable(turn) -> (preview, result)."""

    def __init__(self, skills: Optional[Dict[str, Any]] = None):
        self._skills = skills or {}

    def register(self, name: str, fn) -> None:
        self._skills[name] = fn

    def resolve(self, active: List[str], restricted: List[str], turn) -> List[SkillMatch]:
        out: List[SkillMatch] = []
        for name in active:
            if name in restricted or name not in self._skills:
                continue
            fn = self._skills[name]
            # Lazy: don't call the skill yet. Return a thunk.
            def thunk(fn=fn, turn=turn):
                return fn(turn)
            preview = getattr(fn, "preview", None)
            if callable(preview):
                preview_text = preview(turn)
            else:
                preview_text = f"{name}(turn)"
            out.append(SkillMatch(name=name, preview=preview_text, callable_=thunk))
        return out


# ---------- Model channel -----------------------------------------------------

class ModelChannel(Protocol):
    """Given a ModelDecision + assembled prompt, call the right engine."""

    def call(self, model_decision, prompt: str) -> "EngineResponse": ...
    def supports(self, engine: str) -> bool: ...


class MultiEngineModelChannel:
    """Holds a dict of engines and dispatches by ModelDecision.engine."""

    def __init__(self, engines: Optional[Dict[str, Any]] = None):
        self._engines = engines or {}

    def register(self, engine_name: str, engine) -> None:
        self._engines[engine_name] = engine

    def supports(self, engine: str) -> bool:
        return engine in self._engines

    def call(self, model_decision, prompt: str):
        engine = self._engines.get(model_decision.engine)
        if engine is None:
            raise RuntimeError(
                f"no engine registered for '{model_decision.engine}'. "
                f"registered: {list(self._engines)}"
            )
        return engine.call(
            model=model_decision.model,
            prompt=prompt,
            max_tokens=model_decision.max_tokens,
            temperature=model_decision.temperature,
        )
