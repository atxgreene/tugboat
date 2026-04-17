"""Tugboat — the universal channel navigator.

Tugboat is the routing layer of an agent harness. It doesn't generate, it doesn't
execute; it decides. Given a turn and a declarative policy, Tugboat produces a
four-axis RoutingDecision (memory, subagent, skill, model) and either returns it
(plugin mode) or runs the turn end-to-end (driver mode).

Design constraints:
  - Stdlib only. No third-party dependencies.
  - Plugin-shaped. Tugboat wraps an existing harness; it does not replace it.
  - Declarative policy. Rules live in markdown. A thin interpreter compiles them.
  - Subagent spawn/merge is first-class.

Public API:
  Tugboat         — the entry point (harness wrapper + driver)
  Navigator       — the routing primitive (decision-only)
  Policy          — parsed declarative policy
  RoutingDecision — the four-axis decision object
  Turn            — a single input to route
  Subagent        — spawn/merge primitive
  Engine          — base class for LLM engines (Mock, Ollama, custom)
"""

from .navigator import Navigator, RoutingDecision, Turn
from .policy import Policy, load_policy
from .subagent import Subagent, SubagentSpec, SubagentResult
from .channels import MemoryChannel, SubagentChannel, SkillChannel, ModelChannel
from .adapter import Tugboat
from .engines.base import Engine, EngineResponse
from .observer import TurnLogger, TurnRecord, RuleProposal, orient

__version__ = "0.1.0"

__all__ = [
    "Tugboat",
    "Navigator",
    "RoutingDecision",
    "Turn",
    "Policy",
    "load_policy",
    "Subagent",
    "SubagentSpec",
    "SubagentResult",
    "MemoryChannel",
    "SubagentChannel",
    "SkillChannel",
    "ModelChannel",
    "Engine",
    "EngineResponse",
    "TurnLogger",
    "TurnRecord",
    "RuleProposal",
    "orient",
]
