"""Node skeletons for the LangGraph workflow.

Each function should be small, testable, and return a partial state update. Avoid mutating the
input state in place.
"""

from __future__ import annotations

import os
import re

from .state import AgentState, ApprovalDecision, Route, make_event

RISKY_KEYWORDS = frozenset({"refund", "delete", "send", "cancel", "remove", "revoke"})
TOOL_KEYWORDS = frozenset({"status", "order", "lookup", "check", "track", "find", "search"})
ERROR_KEYWORDS = frozenset({"timeout", "fail", "failure", "error", "crash", "unavailable"})
VAGUE_PRONOUNS = frozenset({"it", "this", "that", "something", "anything"})


def _tokenize(query: str) -> list[str]:
    """Return lowercase word tokens for deterministic routing."""
    return re.findall(r"[a-z0-9]+", query.lower())


def _contains_phrase(query: str, phrase: str) -> bool:
    return re.search(rf"\b{re.escape(phrase)}\b", query.lower()) is not None


def intake_node(state: AgentState) -> dict:
    """Normalize raw query into state fields.

    Keep intake deterministic and side-effect free so checkpoint replay is stable.
    """
    query = " ".join(state.get("query", "").strip().split())
    return {
        "query": query,
        "messages": [f"intake:{query[:40]}"],
        "events": [make_event("intake", "completed", "query normalized", length=len(query))],
    }


def classify_node(state: AgentState) -> dict:
    """Classify the query into a route.

    Routing is keyword based by design for this lab. It avoids scenario-id hard-coding and
    uses explicit priority so hidden scenarios with mixed intent remain predictable.
    """
    query = state.get("query", "")
    normalized_query = query.lower()
    tokens = _tokenize(query)
    token_set = set(tokens)

    route = Route.SIMPLE
    risk_level = "low"
    matched_keyword = "default"

    if token_set & RISKY_KEYWORDS:
        route = Route.RISKY
        risk_level = "high"
        matched_keyword = sorted(token_set & RISKY_KEYWORDS)[0]
    elif token_set & TOOL_KEYWORDS:
        route = Route.TOOL
        matched_keyword = sorted(token_set & TOOL_KEYWORDS)[0]
    elif len(tokens) < 5 and (token_set & VAGUE_PRONOUNS):
        route = Route.MISSING_INFO
        matched_keyword = sorted(token_set & VAGUE_PRONOUNS)[0]
    elif (token_set & ERROR_KEYWORDS) or _contains_phrase(normalized_query, "cannot recover"):
        route = Route.ERROR
        risk_level = "medium"
        matched_keyword = (
            sorted(token_set & ERROR_KEYWORDS)[0]
            if token_set & ERROR_KEYWORDS
            else "cannot recover"
        )

    return {
        "route": route.value,
        "risk_level": risk_level,
        "events": [
            make_event(
                "classify",
                "completed",
                f"route={route.value}",
                risk_level=risk_level,
                matched_keyword=matched_keyword,
            )
        ],
    }


def ask_clarification_node(state: AgentState) -> dict:
    """Ask for missing information instead of hallucinating.

    The graph should stop and ask for context rather than guessing when the query is vague.
    """
    question = (
        "Can you provide the customer, order, or account details needed "
        "to handle this request?"
    )
    return {
        "pending_question": question,
        "final_answer": question,
        "events": [make_event("clarify", "completed", "missing information requested")],
    }


def tool_node(state: AgentState) -> dict:
    """Call a mock tool.

    Simulates transient failures for error-route scenarios to demonstrate retry loops.
    """
    attempt = int(state.get("attempt", 0))
    if state.get("route") == Route.ERROR.value and attempt < 2:
        result = (
            "ERROR: transient support-system failure "
            f"attempt={attempt} scenario={state.get('scenario_id', 'unknown')}"
        )
    else:
        result = (
            "OK: support lookup completed "
            f"attempt={attempt} scenario={state.get('scenario_id', 'unknown')}"
        )
    return {
        "tool_results": [result],
        "events": [
            make_event(
                "tool",
                "completed",
                f"tool executed attempt={attempt}",
                attempt=attempt,
                result_status="error" if result.startswith("ERROR:") else "ok",
            )
        ],
    }


def risky_action_node(state: AgentState) -> dict:
    """Prepare a risky action for approval.

    Risky actions are staged first; the tool/action path only continues after approval.
    """
    query = state.get("query", "requested support action")
    return {
        "proposed_action": f"Review and approve risky support action: {query}",
        "events": [
            make_event(
                "risky_action",
                "pending_approval",
                "approval required",
                risk_level=state.get("risk_level", "unknown"),
            )
        ],
    }


def approval_node(state: AgentState) -> dict:
    """Human approval step with optional LangGraph interrupt().

    Set LANGGRAPH_INTERRUPT=true to use real interrupt() for HITL demos.
    Default uses mock decision so tests and CI run offline.

    Mock approval keeps CI deterministic; LANGGRAPH_INTERRUPT=true enables a real HITL demo.
    """
    if os.getenv("LANGGRAPH_INTERRUPT", "").lower() == "true":
        from langgraph.types import interrupt

        value = interrupt({
            "proposed_action": state.get("proposed_action"),
            "risk_level": state.get("risk_level"),
        })
        if isinstance(value, dict):
            decision = ApprovalDecision(**value)
        else:
            decision = ApprovalDecision(approved=bool(value))
    else:
        decision = ApprovalDecision(approved=True, comment="mock approval for lab")
    return {
        "approval": decision.model_dump(),
        "events": [
            make_event(
                "approval",
                "completed",
                f"approved={decision.approved}",
                reviewer=decision.reviewer,
            )
        ],
    }


def retry_or_fallback_node(state: AgentState) -> dict:
    """Record a retry attempt or fallback decision.

    The routing layer decides whether this attempt goes back to the tool or dead-letters.
    """
    attempt = int(state.get("attempt", 0)) + 1
    max_attempts = int(state.get("max_attempts", 3))
    errors = [f"transient failure attempt={attempt} of {max_attempts}"]
    return {
        "attempt": attempt,
        "errors": errors,
        "events": [
            make_event(
                "retry",
                "completed",
                "retry attempt recorded",
                attempt=attempt,
                max_attempts=max_attempts,
                next_step="dead_letter" if attempt >= max_attempts else "tool",
            )
        ],
    }


def answer_node(state: AgentState) -> dict:
    """Produce a final response.

    Answers are grounded in the latest available state instead of scenario-specific strings.
    """
    route = state.get("route", Route.SIMPLE.value)
    approval = state.get("approval") or {}
    if route == Route.RISKY.value and approval.get("approved") and state.get("tool_results"):
        answer = f"Approved action completed. Evidence: {state['tool_results'][-1]}"
    elif state.get("tool_results"):
        answer = f"I found: {state['tool_results'][-1]}"
    else:
        answer = "I can help with that request using the available support guidance."
    return {
        "final_answer": answer,
        "events": [make_event("answer", "completed", "answer generated", route=route)],
    }


def evaluate_node(state: AgentState) -> dict:
    """Evaluate tool results — the 'done?' check that enables retry loops.

    For the lab, structured status prefixes act as a deterministic evaluator.
    """
    tool_results = state.get("tool_results", [])
    latest = tool_results[-1] if tool_results else ""
    if latest.startswith("ERROR:"):
        return {
            "evaluation_result": "needs_retry",
            "events": [
                make_event(
                    "evaluate",
                    "completed",
                    "tool result indicates failure, retry needed",
                    latest_result=latest,
                )
            ],
        }
    return {
        "evaluation_result": "success",
        "events": [
            make_event(
                "evaluate",
                "completed",
                "tool result satisfactory",
                latest_result=latest,
            )
        ],
    }


def dead_letter_node(state: AgentState) -> dict:
    """Log unresolvable failures for manual review.

    Third layer of error strategy: retry -> fallback -> dead letter.
    """
    attempt = int(state.get("attempt", 0))
    return {
        "final_answer": (
            "Request could not be completed after maximum retry attempts. "
            "Logged for manual review."
        ),
        "events": [
            make_event(
                "dead_letter",
                "completed",
                f"max retries exceeded, attempt={attempt}",
                attempt=attempt,
                scenario_id=state.get("scenario_id", "unknown"),
            )
        ],
    }


def finalize_node(state: AgentState) -> dict:
    """Finalize the run and emit a final audit event."""
    return {"events": [make_event("finalize", "completed", "workflow finished")]}
