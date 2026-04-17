"""`python -m tugboat` — tiny demo CLI.

Two modes:

  route:    python -m tugboat route PATH_TO_POLICY "turn text" [--task-class X] [--context-tokens N] [--external]
  explain:  python -m tugboat explain PATH_TO_POLICY "turn text" [...]

Uses MockEngine so it always runs offline. For real execution, import the
library from Python and supply OllamaEngine or your own.
"""

import argparse
import sys

from .adapter import Tugboat
from .policy import load_policy
from .navigator import Turn
from .channels import InMemoryMemoryChannel, DictSkillChannel, MultiEngineModelChannel
from .engines import MockEngine


def _build_default_tug(policy_path: str) -> Tugboat:
    policy = load_policy(policy_path)
    memory = InMemoryMemoryChannel(
        slices={"recent_daily": "(demo)", "long_term": "(demo)"},
        identity="You are B.E.N., the Navigator.",
    )
    skills = DictSkillChannel({"formatter": lambda t: ("formatter", t.text)})
    mock = MockEngine()
    models = MultiEngineModelChannel({"mock": mock, "ollama": mock, "cloud": mock})
    return Tugboat(policy=policy, memory=memory, skills=skills, models=models)


def _turn_from_args(args) -> Turn:
    return Turn(
        text=args.text,
        task_class=args.task_class,
        context_tokens=args.context_tokens,
        external_action=args.external,
        privacy_tier=args.privacy,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(prog="tugboat", description="universal channel navigator")
    sub = parser.add_subparsers(dest="cmd", required=True)

    for cmd in ("route", "explain", "execute"):
        p = sub.add_parser(cmd)
        p.add_argument("policy")
        p.add_argument("text")
        p.add_argument("--task-class", default="general")
        p.add_argument("--context-tokens", type=int, default=0)
        p.add_argument("--external", action="store_true")
        p.add_argument("--privacy", default="normal", choices=["normal", "private", "public"])

    args = parser.parse_args(argv)
    tug = _build_default_tug(args.policy)
    turn = _turn_from_args(args)

    if args.cmd == "route":
        print(tug.route(turn).to_json())
        return 0
    if args.cmd == "explain":
        print(tug.explain(turn))
        return 0
    if args.cmd == "execute":
        try:
            result = tug.execute(turn, confirm_callback=lambda d: True)
        except PermissionError as exc:
            print(f"blocked: {exc}", file=sys.stderr)
            return 2
        print(result.summary())
        print(result.output)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
