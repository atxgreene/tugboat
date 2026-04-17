# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-04-16

First public release. Stdlib-only, 34 unit tests green.

### Added
- `Navigator.route(turn)` — pure function from (Turn, Policy) to RoutingDecision.
- `Policy` — markdown parser supporting `# Defaults`, `## Rule: name` blocks
  with `when:` predicates and `then:` actions, and `## Subagent: name` specs.
- `Tugboat` adapter — plugin mode (`route`) and driver mode (`execute`) on top
  of the Navigator, with pluggable Memory, Skill, and Model channels.
- `Subagent` spawn primitive with three merge strategies: `return_only`,
  `append_log`, and `merge_memory`. Inline `<memory slice="..." kind="...">`
  markup for subagent memory writes.
- `MockEngine` and `OllamaEngine` reference implementations.
- `TurnLogger` — JSONL observer that appends a structured record per turn.
- `orient()` + `RuleProposal` — offline pattern miner that turns log history
  into markdown rule proposals for human review.
- `Tugboat.regret(turn, proposed_policy)` — diff the current policy's decision
  vs. a proposed policy's decision. The PR-diff UX for policy refinement.
- `Turn.user` and `Turn.context` fields, matchable in policy conditions for
  per-user and per-context rules.
- `examples/ben_policy.md` — reference policy distilled from the openclaws
  workspace (identity lock, local-first drafting, cloud fallback for long
  context, subagent delegation for research, external-action confirmation).
- `examples/basic_usage.py` — full end-to-end demo including subagent memory
  merge and external-action gate.
- `python -m tugboat` CLI with `route`, `explain`, and `execute` subcommands.
- `docs/architecture.md` — the three-layer stack (Knowledge / Routing /
  Harness) and where Tugboat fits.

### Invariants (unchangeable by policy)
- External actions always require confirmation.
- Identity memory is always loaded.
- Memory token budget is floored at 256 tokens.
