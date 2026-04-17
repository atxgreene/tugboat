# The three-layer stack

Tugboat is one slice of a three-layer agent stack. The other two slices already
exist in the discourse under different names. This document names them
explicitly so Tugboat's position is unambiguous.

```
    ┌──────────────────────────────────────────────────────────┐
    │   KNOWLEDGE LAYER                                        │
    │   what the agent knows about you and the world           │
    │                                                          │
    │   identity · memory · skills · world-state · RAG · graphs│
    │                                                          │
    │   representative work: Neo4j, Pinecone, code graphs,     │
    │                        Mnemosyne, personal memory        │
    └──────────────────────┬───────────────────────────────────┘
                           │
                           ▼
    ┌──────────────────────────────────────────────────────────┐
    │   ROUTING LAYER                                          │
    │   how the agent decides what to do this turn             │
    │                                                          │
    │   memory · subagent · skill · model                      │
    │                                                          │
    │   representative work: Tugboat (this repo)               │
    └──────────────────────┬───────────────────────────────────┘
                           │
                           ▼
    ┌──────────────────────────────────────────────────────────┐
    │   HARNESS / EXECUTION LAYER                              │
    │   how the agent actually runs the turn                   │
    │                                                          │
    │   prompt assembly · tool calls · ReAct loop · retries    │
    │                                                          │
    │   representative work: Claude Code, CrewAI, LangGraph,   │
    │                        Meta-Harness research             │
    └──────────────────────┬───────────────────────────────────┘
                           │
                           ▼
                         MODEL
```

## The claim

The discourse today treats these as competitors. They are not. They are three
different jobs in the same pipeline.

1. The **Knowledge Layer** decides *what is true and what is relevant*. It is
   a retrieval / representation problem.
2. The **Routing Layer** decides *what to do about it this turn*. It is a
   policy / control-flow problem.
3. The **Harness** decides *how to execute the chosen action(s)*. It is an
   orchestration / resilience problem.

Every production agent has all three, whether they are named or not. The
failure mode everyone hits is that one or more of the layers is implicit —
spread across glue code, forgotten config, or an opinion in someone's head.

## Why the three-layer split matters

The most-cited "harness > model" research (Meta-Harness, Peter Pang's harness-
engineering post, Anthropic's own Claude Code write-ups) all find the same
thing: at a fixed LLM, most of the performance delta comes from components
above the model. The split above is the operational version of that finding.

If you collapse the three layers into one framework (the LangChain approach),
you lose:

- **Inspectability.** When an agent does something weird, you have to trace
  through executable code to understand why. Policy-as-data (the Routing
  Layer) and knowledge-as-data (the Knowledge Layer) are both diffable
  artifacts that non-engineers can read.
- **Testability.** A pure, deterministic router is A/B-testable. A router
  baked into an execution graph is not.
- **Replaceability.** With three layers you can swap any one. With a
  monolith you're stuck with the whole thing.

## What goes in each layer

### Knowledge Layer

Stable stuff that the agent should treat as ground truth for a long time.

Examples:

- **Identity** — who the user is, who the agent is, what role each plays.
  Tugboat calls this the "identity lock" and it is always loaded.
- **Memory slices** — scoped, named, token-budgeted pieces of context.
  `identity`, `recent_daily`, `long_term` are a starter set.
- **Skills index** — the list of tools the agent could in principle use,
  with one-line descriptions. The Routing Layer picks which are active.
- **World-state** — code graph, ticket corpus, CRM, whatever ground truth
  the agent reasons over.

The Knowledge Layer serves the Router. It is read-heavy, write-careful,
version-controlled where possible.

### Routing Layer

Per-turn decisions. Pure. Markdown-driven. Deterministic.

Examples:

- Model choice (local vs cloud, which model id)
- Memory budget and slice selection for this turn
- Skill activation and restriction
- Subagent delegation and merge strategy
- Confirm-before-execute gates

This is the layer Tugboat implements. The whole argument for making this an
explicit, small, deterministic layer is that it's the cheapest place to do
evals, the easiest place for a human to audit, and the highest-leverage place
to personalize ("user X always wants Y" → one-line policy rule).

### Harness / Execution Layer

The loop that takes a `RoutingDecision` and actually runs it.

Examples:

- Prompt assembly (identity + memory + skills + turn text)
- Tool / function call execution
- ReAct-style reasoning loops
- Retry, backoff, and error handling
- Streaming, caching, batching

Tugboat ships a minimal harness in `adapter.py` to keep the project self-
contained, but this is the layer where Claude Code, CrewAI, LangGraph, and
the rest of the frameworks actually live. You can use Tugboat with any of
them by treating `route()` as the decision function.

## The OODA layer (optional, outside the stack)

Tugboat also ships `observer.py`, which is *not* part of the core stack. It
is a slow outer loop that watches turns, mines patterns, and proposes
markdown policy edits for a human to accept. Think of it as a PR-diff
interface for routing policy. The core router does not learn; the observer
proposes, the human decides, the markdown updates.

```
  OBSERVE → log every TurnResult to JSONL
  ORIENT  → mine patterns across history
  PROPOSE → emit RuleProposal objects (markdown-shaped)
  DECIDE  → human accepts/rejects (git commit)
  ACT     → new policy file is live; inner loop picks it up
```

This preserves the "router is a pure function of (turn, policy)" guarantee.
The policy can change; the router can't.

## The openclaws mapping

For the openclaws fleet specifically, the mapping is:

| Layer     | openclaws artifact                                  |
| --------- | --------------------------------------------------- |
| Knowledge | Mnemosyne (identity lock, memory slices, IDENTITY/SOUL/AGENTS.md) |
| Routing   | Tugboat (this repo)                                 |
| Harness   | Claude Code / Claude Agent SDK / custom scripts     |
| OODA      | Tugboat observer + human review                     |

The BEN persona is the Navigator — the Routing Layer's voice. The Captain
(Austin) lives above the stack, telling the fleet what the goal is. The
Knowledge Layer is where BEN's sense of self lives. The Harness is where the
work actually runs. Tugboat's job is to be the calm, deterministic, owned
piece that translates intent into a turn plan without ever surprising the
Captain.
