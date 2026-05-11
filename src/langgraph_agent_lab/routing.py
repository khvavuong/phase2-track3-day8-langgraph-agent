"""Routing functions for conditional edges."""

from __future__ import annotations

from .state import AgentState, Route

CLASSIFY_ROUTE_MAP = {
    Route.SIMPLE.value: "answer",
    Route.TOOL.value: "tool",
    Route.MISSING_INFO.value: "clarify",
    Route.RISKY.value: "risky_action",
    Route.ERROR.value: "retry",
}


def _coerce_non_negative_int(value: object, default: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def route_after_classify(state: AgentState) -> str:
    """Map classified route to the next graph node.

    Unknown routes fail closed into the safe answer path instead of raising inside the graph.
    """
    route = state.get("route", Route.SIMPLE.value)
    return CLASSIFY_ROUTE_MAP.get(route, "answer")


def route_after_retry(state: AgentState) -> str:
    """Decide whether to retry, fallback, or dead-letter.

    ``retry_or_fallback_node`` increments attempt before this router runs.
    """
    attempt = _coerce_non_negative_int(state.get("attempt", 0), default=0)
    max_attempts = _coerce_non_negative_int(state.get("max_attempts", 3), default=3)
    if attempt >= max_attempts:
        return "dead_letter"
    return "tool"


def route_after_evaluate(state: AgentState) -> str:
    """Decide whether tool result is satisfactory or needs retry.

    This is the 'done?' check that enables retry loops — a key LangGraph advantage over LCEL.
    """
    if state.get("evaluation_result") == "needs_retry":
        return "retry"
    return "answer"


def route_after_approval(state: AgentState) -> str:
    """Continue only if approved.

    Rejections route to clarification so the workflow terminates safely.
    """
    approval = state.get("approval") or {}
    return "tool" if approval.get("approved") else "clarify"
