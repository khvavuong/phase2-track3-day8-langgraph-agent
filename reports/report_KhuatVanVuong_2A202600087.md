# Day 08 Lab Report

## 1. Student

- Name: Khuất Văn Vương
- Repo: khvavuong/phase2-track3-day8-langgraph-agent
- Date: 2026-05-11

## 2. Architecture

The workflow is a typed LangGraph support-ticket agent. It normalizes the query, classifies
the route, executes the appropriate path, records audit events, and always terminates through
`finalize`.

```text
START -> intake -> classify
classify(simple)       -> answer -> finalize -> END
classify(tool)         -> tool -> evaluate -> answer -> finalize -> END
classify(missing_info) -> clarify -> finalize -> END
classify(risky)        -> risky_action -> approval -> tool -> evaluate -> answer -> finalize
classify(error)        -> retry -> tool -> evaluate -> retry|answer
retry(max exceeded)    -> dead_letter -> finalize -> END
```

Node boundaries are intentionally small: `classify` only chooses a route, `routing.py`
selects the next node, `evaluate` is the retry gate, and `approval` is the HITL boundary.

## 3. State Schema

| Field               | Reducer   | Purpose                                                |
| ------------------- | --------- | ------------------------------------------------------ |
| `thread_id`         | overwrite | Stable checkpoint key per scenario run.                |
| `query`             | overwrite | Normalized user request.                               |
| `route`             | overwrite | Current route selected by `classify_node`.             |
| `risk_level`        | overwrite | Current risk estimate for approval and reporting.      |
| `attempt`           | overwrite | Retry attempt counter.                                 |
| `max_attempts`      | overwrite | Scenario-specific retry bound.                         |
| `evaluation_result` | overwrite | Gate value: `success` or `needs_retry`.                |
| `approval`          | overwrite | Serializable human/mock approval decision.             |
| `final_answer`      | overwrite | Final response or dead-letter message.                 |
| `messages`          | append    | Lightweight audit message trail.                       |
| `tool_results`      | append    | Tool evidence across attempts.                         |
| `errors`            | append    | Retry/dead-letter diagnostics.                         |
| `events`            | append    | Structured node audit trail for metrics and debugging. |

## 4. Metrics Summary

- Total scenarios: 7
- Success rate: 100.00%
- Average nodes visited: 6.43
- Total retries: 3
- Total interrupts: 2
- Resume/state-history evidence: yes
- Route distribution: error=2, missing_info=1, risky=2, simple=1, tool=1

## 5. Scenario Results

| Scenario        | Expected     | Actual       | Success | Retries | Approval | Nodes |
| --------------- | ------------ | ------------ | ------: | ------: | -------: | ----: |
| S01_simple      | simple       | simple       |     yes |       0 |       no |     4 |
| S02_tool        | tool         | tool         |     yes |       0 |       no |     6 |
| S03_missing     | missing_info | missing_info |     yes |       0 |       no |     4 |
| S04_risky       | risky        | risky        |     yes |       0 |      yes |     8 |
| S05_error       | error        | error        |     yes |       2 |       no |    10 |
| S06_delete      | risky        | risky        |     yes |       0 |      yes |     8 |
| S07_dead_letter | error        | error        |     yes |       1 |       no |     5 |

## 6. Failure Analysis

The retry path is intentionally bounded. Tool errors are represented as structured `ERROR:`
results, `evaluate_node` marks them as `needs_retry`, and `route_after_retry` sends the
workflow to `dead_letter` once `attempt >= max_attempts`. This prevents infinite loops while
still allowing transient failures to recover.

Risky actions are staged before execution. Queries such as refunds, deletes, sends, cancels,
removes, or revokes route through `risky_action -> approval`. Rejected or missing approvals
route to `clarify` instead of executing the tool path.

| Scenario        | Error count | Latest errors                                                        |
| --------------- | ----------: | -------------------------------------------------------------------- |
| S05_error       |           2 | transient failure attempt=1 of 3<br>transient failure attempt=2 of 3 |
| S07_dead_letter |           1 | transient failure attempt=1 of 1                                     |

## 7. Persistence And Recovery Evidence

The lab runs with SQLite checkpointing via `outputs/checkpoints.db`. Each scenario uses a
stable `thread_id` such as `thread-S05_error`, and the compiled graph can inspect persisted
history with `get_state_history(config)`. In the latest verification, `thread-S05_error`
had persisted state history available after the scenario run.

## 8. Extension Work

- SQLite checkpointer implemented with an explicit `sqlite3.Connection`.
- WAL mode enabled for durable local checkpoint writes.
- Conditional graph edges include explicit path maps, which makes graph inspection and
  Mermaid export cleaner.
- Metrics are resilient to repeated SQLite-backed runs by counting events from the latest
  `intake` event only.

## 9. Improvement Plan

The next production step would be replacing keyword classification with a structured policy
layer backed by tests and trace logs. After that, the approval path should support reject,
edit, timeout escalation, and a real operator UI for interrupt/resume demos.
