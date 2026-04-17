"""OllamaEngine — stdlib-only adapter to a local Ollama server.

Uses urllib. No third-party deps. Points at http://localhost:11434 by default,
configurable. Gracefully raises a clear error if Ollama isn't reachable so
the mock path can be used in its place.

This is the model-routing escape hatch: when the Navigator decides
model.engine == 'ollama', this is what actually runs.
"""

from dataclasses import dataclass
from typing import Optional
import json
import urllib.request
import urllib.error

from .base import EngineResponse


@dataclass
class OllamaEngine:
    base_url: str = "http://localhost:11434"
    timeout_seconds: float = 120.0

    def call(self, model: str, prompt: str, max_tokens: int, temperature: float) -> EngineResponse:
        url = f"{self.base_url.rstrip('/')}/api/generate"
        body = json.dumps({
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "num_predict": int(max_tokens),
                "temperature": float(temperature),
            },
        }).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"OllamaEngine could not reach {url}: {exc}. "
                f"Is `ollama serve` running?"
            ) from exc

        text = payload.get("response", "")
        return EngineResponse(
            text=text,
            model=model,
            engine="ollama",
            usage_tokens=int(payload.get("eval_count", 0) or 0),
            raw=payload,
        )
