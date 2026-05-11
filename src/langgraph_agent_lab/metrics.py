"""Metrics schema and helpers."""

from __future__ import annotations

import json
from pathlib import Path
from statistics import mean
from typing import Any

from pydantic import BaseModel, Field


class ScenarioMetric(BaseModel):
    scenario_id: str
    success: bool
    expected_route: str
    actual_route: str | None = None
    nodes_visited: int = 0
    retry_count: int = 0
    interrupt_count: int = 0
    approval_required: bool = False
    approval_observed: bool = False
    latency_ms: int = 0
    errors: list[str] = Field(default_factory=list)


class MetricsReport(BaseModel):
    total_scenarios: int
    success_rate: float
    avg_nodes_visited: float
    total_retries: int
    total_interrupts: int
    resume_success: bool = False
    scenario_metrics: list[ScenarioMetric]


def _current_run_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return events from the latest intake onward.

    Durable checkpoints can contain prior runs with the same thread id. Metrics should describe
    the current scenario execution, not the entire persisted thread history.
    """
    for index in range(len(events) - 1, -1, -1):
        if events[index].get("node") == "intake":
            return events[index:]
    return events


def metric_from_state(
    state: dict[str, Any],
    expected_route: str,
    approval_required: bool,
) -> ScenarioMetric:
    events = _current_run_events(state.get("events", []) or [])
    errors = state.get("errors", []) or []
    actual_route = state.get("route")
    approval = state.get("approval")
    nodes = [event.get("node", "unknown") for event in events]
    retry_count = sum(1 for node in nodes if node == "retry")
    interrupt_count = sum(1 for node in nodes if node == "approval")
    output_observed = bool(state.get("final_answer") or state.get("pending_question"))
    approval_observed = "approval" in nodes and approval is not None
    success = actual_route == expected_route and output_observed
    if approval_required:
        success = success and approval_observed
    return ScenarioMetric(
        scenario_id=str(state.get("scenario_id", "unknown")),
        success=success,
        expected_route=expected_route,
        actual_route=actual_route,
        nodes_visited=len(nodes),
        retry_count=retry_count,
        interrupt_count=interrupt_count,
        approval_required=approval_required,
        approval_observed=approval_observed,
        latency_ms=sum(int(event.get("latency_ms", 0)) for event in events),
        errors=list(errors)[-retry_count:] if retry_count else [],
    )


def summarize_metrics(items: list[ScenarioMetric]) -> MetricsReport:
    if not items:
        raise ValueError("No scenario metrics to summarize")
    return MetricsReport(
        total_scenarios=len(items),
        success_rate=sum(1 for item in items if item.success) / len(items),
        avg_nodes_visited=mean(item.nodes_visited for item in items),
        total_retries=sum(item.retry_count for item in items),
        total_interrupts=sum(item.interrupt_count for item in items),
        resume_success=False,
        scenario_metrics=items,
    )


def write_metrics(report: MetricsReport, output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
