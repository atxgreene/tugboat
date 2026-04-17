"""MockEngine — a deterministic local engine for tests and offline demos.

It echoes back a synthesized reply that includes the routing decision summary,
so tests can assert on *what got routed* without needing a real LLM. It also
honors a few magic trigger words in the prompt to simulate interesting outputs
(memory writes, skill calls, error conditions) for subagent tests.
"""

from typing import Optional, Dict, List
from .base import Engine, EngineResponse


class MockEngine:
    """Deterministic fake engine.

    By default, returns a canned string that includes a summary of the prompt
    length and the model id. Register per-prompt overrides to test specific
    behaviors.
    """

    def __init__(self, canned: Optional[Dict[str, str]] = None):
        self._canned = canned or {}
        self.calls: List[Dict[str, object]] = []

    def override(self, match: str, reply: str) -> None:
        self._canned[match] = reply

    def call(self, model: str, prompt: str, max_tokens: int, temperature: float) -> EngineResponse:
        self.calls.append({
            "model": model,
            "prompt_len": len(prompt),
            "max_tokens": max_tokens,
            "temperature": temperature,
        })
        # Look for any override whose key appears in the prompt
        for needle, reply in self._canned.items():
            if needle in prompt:
                return EngineResponse(
                    text=reply,
                    model=model,
                    engine="mock",
                    usage_tokens=len(reply) // 4,
                    raw={"matched": needle},
                )
        reply = (
            f"[mock:{model}] received prompt of length {len(prompt)} "
            f"(max_tokens={max_tokens}, temp={temperature:.2f})."
        )
        return EngineResponse(
            text=reply,
            model=model,
            engine="mock",
            usage_tokens=len(reply) // 4,
            raw={},
        )
