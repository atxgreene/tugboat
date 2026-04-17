"""Policy — parses markdown rule files into executable routing policy.

This is the interpreter that reads the kind of rules Austin already writes in
AGENTS.md / SOUL.md / local_model_roadmap.md and compiles them into Rule
objects the Navigator can execute.

Supported markdown forms:

    # Defaults
    - model.engine: ollama
    - model.model: qwen2.5:14b-instruct
    - memory.budget_tokens: 2000
    - memory.slices: [identity, recent_daily]
    - skill.active: [formatter]

    ## Rule: cloud for long context
    when: context_tokens > 8000
    then:
      model.engine = cloud
      model.model = claude-opus-4-6
      model.reason = long context exceeds local window

    ## Rule: subagent for research tasks
    when: task_class == research
    then:
      subagent.delegate = true
      subagent.spec_name = researcher
      subagent.merge_strategy = merge_memory

    ## Rule: confirm before external
    when: external_action
    then:
      confirm_before_execute = true

Rule bodies are intentionally small. Matching conditions are a tiny predicate
language: `<field> <op> <value>` joined by `and`. Ops: ==, !=, >, <, >=, <=,
contains, in. Two boolean shorthand conditions: `external_action`, `private`.

Rules fire in file order. Later rules override earlier ones for the same field.
"""

from dataclasses import dataclass, field
from typing import Callable, List, Dict, Any, Optional
import re
from pathlib import Path


# ---------- predicates --------------------------------------------------------

def _parse_value(raw: str) -> Any:
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        return [_parse_value(x) for x in _split_top_level(inner, ",")]
    if raw.lower() in ("true", "yes"):
        return True
    if raw.lower() in ("false", "no"):
        return False
    if raw.lower() in ("null", "none"):
        return None
    # numeric
    try:
        if "." in raw:
            return float(raw)
        return int(raw)
    except ValueError:
        pass
    # quoted string
    if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
        return raw[1:-1]
    return raw  # bare string (identifier, etc.)


def _split_top_level(text: str, sep: str) -> List[str]:
    """Split on sep, respecting [] and () nesting."""
    out, buf, depth = [], [], 0
    for ch in text:
        if ch in "[(":
            depth += 1
        elif ch in "])":
            depth -= 1
        if ch == sep and depth == 0:
            out.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if buf:
        out.append("".join(buf).strip())
    return out


_COND_RE = re.compile(
    r"^\s*(?P<field>[a-zA-Z_][a-zA-Z0-9_.]*)"
    r"\s*(?P<op>==|!=|>=|<=|>|<|contains|in)\s*"
    r"(?P<value>.+?)\s*$"
)


def _compile_condition(cond: str) -> Callable[["Turn", "RoutingDecision"], bool]:
    from .navigator import Turn, RoutingDecision  # local import to avoid cycle

    cond = cond.strip()
    if not cond:
        return lambda t, d: True

    # Boolean shorthand
    if cond == "external_action":
        return lambda t, d: bool(t.external_action)
    if cond == "private":
        return lambda t, d: t.privacy_tier == "private"
    if cond == "public":
        return lambda t, d: t.privacy_tier == "public"
    if cond == "always":
        return lambda t, d: True

    m = _COND_RE.match(cond)
    if not m:
        raise ValueError(f"unparseable condition: {cond!r}")

    field_ = m.group("field")
    op = m.group("op")
    value = _parse_value(m.group("value"))

    def get(turn, decision):
        # Flat-path lookup across Turn and RoutingDecision fields
        if "." in field_:
            head, tail = field_.split(".", 1)
            target = {"turn": turn, "decision": decision}.get(head, turn)
            return _dig(target, tail)
        return getattr(turn, field_, None)

    def check(t, d):
        actual = get(t, d)
        if op == "==":
            return actual == value
        if op == "!=":
            return actual != value
        if op == ">":
            return actual is not None and actual > value
        if op == "<":
            return actual is not None and actual < value
        if op == ">=":
            return actual is not None and actual >= value
        if op == "<=":
            return actual is not None and actual <= value
        if op == "contains":
            return actual is not None and value in actual
        if op == "in":
            return actual in (value or [])
        return False

    return check


def _dig(obj: Any, path: str) -> Any:
    for part in path.split("."):
        if obj is None:
            return None
        if isinstance(obj, dict):
            obj = obj.get(part)
        else:
            obj = getattr(obj, part, None)
    return obj


def _compile_condition_chain(raw: str) -> Callable:
    """`a and b and c` — no OR yet; add if needed."""
    parts = [p.strip() for p in re.split(r"\band\b", raw) if p.strip()]
    if not parts:
        return lambda t, d: True
    checks = [_compile_condition(p) for p in parts]
    return lambda t, d: all(c(t, d) for c in checks)


# ---------- actions -----------------------------------------------------------

_ASSIGN_RE = re.compile(
    r"^\s*(?P<path>[a-zA-Z_][a-zA-Z0-9_.]*)\s*(?P<op>=|\+=)\s*(?P<value>.+)$"
)


def _compile_action(line: str) -> Callable[["RoutingDecision"], None]:
    m = _ASSIGN_RE.match(line)
    if not m:
        raise ValueError(f"unparseable action: {line!r}")
    path = m.group("path")
    op = m.group("op")
    value = _parse_value(m.group("value"))

    def apply(decision):
        obj, attr = _resolve_path(decision, path)
        if op == "=":
            setattr(obj, attr, value)
        elif op == "+=":
            current = getattr(obj, attr, None)
            if isinstance(current, list):
                if isinstance(value, list):
                    current.extend(value)
                else:
                    current.append(value)
            elif isinstance(current, (int, float)):
                setattr(obj, attr, current + value)
            else:
                raise TypeError(f"+= not supported for {path} (type {type(current).__name__})")

    return apply


def _resolve_path(decision, path: str):
    """Return (parent_obj, final_attr) for a dotted path rooted at decision."""
    parts = path.split(".")
    obj = decision
    for p in parts[:-1]:
        obj = getattr(obj, p)
    return obj, parts[-1]


# ---------- Rule + Policy -----------------------------------------------------

@dataclass
class Rule:
    name: str
    condition: Callable
    actions: List[Callable]

    def matches(self, turn, decision) -> bool:
        try:
            return bool(self.condition(turn, decision))
        except Exception:
            return False

    def apply(self, decision) -> None:
        for a in self.actions:
            a(decision)


@dataclass
class Policy:
    defaults: Dict[str, Any] = field(default_factory=dict)
    rules: List[Rule] = field(default_factory=list)
    subagents: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    raw_source: str = ""

    def rule_names(self) -> List[str]:
        return [r.name for r in self.rules]


# ---------- parser ------------------------------------------------------------

_H1 = re.compile(r"^#\s+(.+?)\s*$")
_H2 = re.compile(r"^##\s+(.+?)\s*$")
_BULLET_KV = re.compile(r"^-\s+(?P<key>[a-zA-Z_][a-zA-Z0-9_.]*)\s*:\s*(?P<val>.+?)\s*$")


def load_policy(path: str) -> Policy:
    source = Path(path).read_text(encoding="utf-8")
    return parse_policy(source)


def parse_policy(source: str) -> Policy:
    policy = Policy(raw_source=source)
    lines = source.splitlines()
    i = 0
    section: Optional[str] = None

    while i < len(lines):
        line = lines[i].rstrip()

        if not line.strip() or line.strip().startswith("<!--"):
            i += 1
            continue

        m1 = _H1.match(line)
        if m1:
            section = m1.group(1).strip().lower()
            i += 1
            continue

        m2 = _H2.match(line)
        if m2:
            heading = m2.group(1).strip()
            lower = heading.lower()
            if lower.startswith("rule:"):
                rule_name = heading.split(":", 1)[1].strip()
                rule, consumed = _parse_rule_block(rule_name, lines, i + 1)
                policy.rules.append(rule)
                i = i + 1 + consumed
                continue
            if lower.startswith("subagent:"):
                spec_name = heading.split(":", 1)[1].strip()
                spec, consumed = _parse_subagent_block(lines, i + 1)
                policy.subagents[spec_name] = spec
                i = i + 1 + consumed
                continue
            # Generic section heading
            section = lower
            i += 1
            continue

        # Bullet k:v under a section
        mb = _BULLET_KV.match(line)
        if mb and section in ("defaults", None):
            policy.defaults[mb.group("key")] = _parse_value(mb.group("val"))
            i += 1
            continue

        i += 1

    return policy


def _parse_rule_block(name: str, lines: List[str], start: int):
    """Parse a Rule block: `when: ...` then `then:` followed by bulleted actions."""
    when_re = re.compile(r"^when\s*:\s*(.+?)\s*$", re.IGNORECASE)
    then_re = re.compile(r"^then\s*:\s*$", re.IGNORECASE)
    condition_src = "always"
    actions_src: List[str] = []
    in_then = False

    i = start
    while i < len(lines):
        line = lines[i].rstrip()
        # Stop at next heading
        if _H1.match(line) or _H2.match(line):
            break
        stripped = line.strip()
        if not stripped or stripped.startswith("<!--"):
            i += 1
            continue
        if not in_then:
            mw = when_re.match(stripped)
            if mw:
                condition_src = mw.group(1).strip()
                i += 1
                continue
            if then_re.match(stripped):
                in_then = True
                i += 1
                continue
            # Unknown line before then — skip
            i += 1
            continue
        else:
            # In the `then` block we accept `- key = val` or `key = val`
            body = stripped[2:].strip() if stripped.startswith("- ") else stripped
            if body:
                actions_src.append(body)
            i += 1

    condition = _compile_condition_chain(condition_src)
    actions = [_compile_action(a) for a in actions_src]
    return Rule(name=name, condition=condition, actions=actions), (i - start)


def _parse_subagent_block(lines: List[str], start: int):
    spec: Dict[str, Any] = {}
    i = start
    while i < len(lines):
        line = lines[i].rstrip()
        if _H1.match(line) or _H2.match(line):
            break
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        mb = _BULLET_KV.match(stripped)
        if mb:
            spec[mb.group("key")] = _parse_value(mb.group("val"))
        i += 1
    return spec, (i - start)
