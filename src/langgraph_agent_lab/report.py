"""Report generation helper."""

from __future__ import annotations

from pathlib import Path

from .metrics import MetricsReport, ScenarioMetric


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def _scenario_rows(items: list[ScenarioMetric]) -> str:
    rows = [
        "| Scenario | Expected | Actual | Success | Retries | Approval | Nodes |",
        "|---|---|---|---:|---:|---:|---:|",
    ]
    for item in items:
        rows.append(
            "| "
            f"{item.scenario_id} | "
            f"{item.expected_route} | "
            f"{item.actual_route or 'unknown'} | "
            f"{_yes_no(item.success)} | "
            f"{item.retry_count} | "
            f"{_yes_no(item.approval_observed)} | "
            f"{item.nodes_visited} |"
        )
    return "\n".join(rows)


def _route_summary(items: list[ScenarioMetric]) -> str:
    counts: dict[str, int] = {}
    for item in items:
        route = item.actual_route or "unknown"
        counts[route] = counts.get(route, 0) + 1
    return ", ".join(f"{route}={count}" for route, count in sorted(counts.items()))


def _failure_rows(items: list[ScenarioMetric]) -> str:
    rows = [
        "| Scenario | Error count | Latest errors |",
        "|---|---:|---|",
    ]
    for item in items:
        if not item.errors:
            continue
        latest_errors = "<br>".join(item.errors)
        rows.append(f"| {item.scenario_id} | {len(item.errors)} | {latest_errors} |")
    if len(rows) == 2:
        rows.append("| none | 0 | No runtime errors recorded. |")
    return "\n".join(rows)


def render_report(metrics: MetricsReport) -> str:
    """Render a complete lab report from the scenario metrics."""
    return f"""# Day 08 Lab Report

## 1. Student

- Name: Khvavuong
- Lab: Day 08 - LangGraph Agentic Orchestration
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

| Field | Reducer | Purpose |
|---|---|---|
| `thread_id` | overwrite | Stable checkpoint key per scenario run. |
| `query` | overwrite | Normalized user request. |
| `route` | overwrite | Current route selected by `classify_node`. |
| `risk_level` | overwrite | Current risk estimate for approval and reporting. |
| `attempt` | overwrite | Retry attempt counter. |
| `max_attempts` | overwrite | Scenario-specific retry bound. |
| `evaluation_result` | overwrite | Gate value: `success` or `needs_retry`. |
| `approval` | overwrite | Serializable human/mock approval decision. |
| `final_answer` | overwrite | Final response or dead-letter message. |
| `messages` | append | Lightweight audit message trail. |
| `tool_results` | append | Tool evidence across attempts. |
| `errors` | append | Retry/dead-letter diagnostics. |
| `events` | append | Structured node audit trail for metrics and debugging. |

## 4. Metrics Summary

- Total scenarios: {metrics.total_scenarios}
- Success rate: {metrics.success_rate:.2%}
- Average nodes visited: {metrics.avg_nodes_visited:.2f}
- Total retries: {metrics.total_retries}
- Total interrupts: {metrics.total_interrupts}
- Resume/state-history evidence: {_yes_no(metrics.resume_success)}
- Route distribution: {_route_summary(metrics.scenario_metrics)}

## 5. Scenario Results

{_scenario_rows(metrics.scenario_metrics)}

## 6. Failure Analysis

The retry path is intentionally bounded. Tool errors are represented as structured `ERROR:`
results, `evaluate_node` marks them as `needs_retry`, and `route_after_retry` sends the
workflow to `dead_letter` once `attempt >= max_attempts`. This prevents infinite loops while
still allowing transient failures to recover.

Risky actions are staged before execution. Queries such as refunds, deletes, sends, cancels,
removes, or revokes route through `risky_action -> approval`. Rejected or missing approvals
route to `clarify` instead of executing the tool path.

{_failure_rows(metrics.scenario_metrics)}

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
"""


def write_report(metrics: MetricsReport, output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(metrics), encoding="utf-8")
