"""Engine base types. Kept tiny on purpose — the interface is the contract."""

from dataclasses import dataclass, field
from typing import Dict, Any, Protocol


@dataclass
class EngineResponse:
    text: str
    model: str
    engine: str
    usage_tokens: int = 0
    raw: Dict[str, Any] = field(default_factory=dict)


class Engine(Protocol):
    def call(self, model: str, prompt: str, max_tokens: int, temperature: float) -> EngineResponse: ...
