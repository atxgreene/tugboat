<!--
  BEN's routing policy for Tugboat.
  Distilled from openclaw-workspace-backup/AGENTS.md, SOUL.md, IDENTITY.md,
  and local_model_roadmap.md. Edit freely — this file IS the router.
-->

# Defaults

- model.engine: ollama
- model.model: qwen2.5:14b-instruct
- memory.budget_tokens: 2000
- memory.slices: [identity, recent_daily]
- skill.active: [formatter, memory_writer]

## Rule: cloud for high complexity reasoning

when: task_class == reasoning
then:
  - model.engine = cloud
  - model.model = claude-opus-4-6
  - model.reason = reasoning task, cloud preferred per Phase 4 routing policy

## Rule: local for drafting and summaries

when: task_class == drafting
then:
  - model.engine = ollama
  - model.model = qwen2.5:14b-instruct
  - model.reason = drafting, local first per Phase 4 routing policy

## Rule: cloud fallback for long context

<!-- Applied after task-class rules so long context wins even on drafting turns. -->

when: context_tokens > 8000
then:
  - model.engine = cloud
  - model.model = claude-opus-4-6
  - model.reason = long context exceeds local window
  - memory.budget_tokens = 6000

## Rule: restrict external skills when private

when: private
then:
  - skill.restricted += [send_email, send_tweet, post_public]

## Rule: confirm before external actions

when: external_action
then:
  - confirm_before_execute = true

## Rule: delegate research to the researcher subagent

when: task_class == research
then:
  - subagent.delegate = true
  - subagent.spec_name = researcher
  - subagent.merge_strategy = merge_memory

## Rule: delegate triage to the triager subagent

when: task_class == triage
then:
  - subagent.delegate = true
  - subagent.spec_name = triager
  - subagent.merge_strategy = append_log

## Subagent: researcher

- goal: investigate a topic across available sources and return a terse, sourced summary
- scoped_tools: [web_search, read_file, memory_writer]
- model_engine: cloud
- model_id: claude-opus-4-6
- memory_scope: read_parent
- max_iterations: 8
- merge_strategy: merge_memory
- budget_tokens: 4000

## Subagent: triager

- goal: classify an incoming issue by severity and propose next action
- scoped_tools: [read_file, memory_writer]
- model_engine: ollama
- model_id: qwen2.5:14b-instruct
- memory_scope: isolated
- max_iterations: 3
- merge_strategy: append_log
- budget_tokens: 1500
