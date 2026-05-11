"""Small local web UI for exercising the LangGraph workflow."""

from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from .graph import build_graph
from .nodes import classify_node, intake_node, risky_action_node
from .persistence import build_checkpointer
from .state import AgentState, ApprovalDecision, Route, Scenario, initial_state

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig


STATIC_DIR = Path(__file__).with_name("static")
INDEX_HTML = STATIC_DIR / "index.html"


def _index_html() -> bytes:
    return INDEX_HTML.read_bytes()


def _merge_update(state: dict[str, Any], update: dict[str, Any]) -> None:
    for key, value in update.items():
        if key in {"messages", "tool_results", "errors", "events"}:
            state.setdefault(key, [])
            state[key].extend(value)
        else:
            state[key] = value


def _latest_run_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for index in range(len(events) - 1, -1, -1):
        if events[index].get("node") == "intake":
            return events[index:]
    return events


def _scenario(query: str) -> Scenario:
    return Scenario(
        id=f"ui-{uuid4().hex[:8]}",
        query=query,
        expected_route=Route.SIMPLE,
    )


def _response_from_state(
    state: dict[str, Any],
    *,
    pending_approval: bool = False,
) -> dict[str, Any]:
    return {
        "route": state.get("route"),
        "risk_level": state.get("risk_level"),
        "attempt": state.get("attempt", 0),
        "final_answer": state.get("final_answer"),
        "pending_question": state.get("pending_question"),
        "pending_approval": pending_approval,
        "proposed_action": state.get("proposed_action"),
        "message": "Waiting for approval." if pending_approval else "Workflow complete.",
        "events": _latest_run_events(state.get("events", []) or []),
    }


def preview_or_run(query: str) -> dict[str, Any]:
    """Run non-risky queries and pause risky queries before approval."""
    state = dict(initial_state(_scenario(query)))
    _merge_update(state, intake_node(cast(AgentState, state)))
    _merge_update(state, classify_node(cast(AgentState, state)))
    if state.get("route") == Route.RISKY.value:
        _merge_update(state, risky_action_node(cast(AgentState, state)))
        return _response_from_state(state, pending_approval=True)
    return run_query(query)


def run_query(query: str, approval: ApprovalDecision | None = None) -> dict[str, Any]:
    scenario = _scenario(query)
    graph = build_graph(checkpointer=build_checkpointer("memory"))
    state = initial_state(scenario)
    if approval is not None:
        state["approval"] = approval.model_dump()
    config = cast("RunnableConfig", {"configurable": {"thread_id": state["thread_id"]}})
    result = cast(dict[str, Any], graph.invoke(state, config=config))
    return _response_from_state(result)


class UIRequestHandler(BaseHTTPRequestHandler):
    """HTTP handler for the local workflow tester."""

    server_version = "LangGraphAgentLabUI/0.2"

    def do_GET(self) -> None:
        if self.path != "/":
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        body = _index_html()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path == "/api/run":
            self._handle_run()
            return
        if self.path == "/api/decision":
            self._handle_decision()
            return
        self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: object) -> None:
        return

    def _handle_run(self) -> None:
        try:
            query = self._query_from_body()
            self._send_json(preview_or_run(query), HTTPStatus.OK)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_decision(self) -> None:
        try:
            payload = self._read_json()
            query = self._validated_query(payload)
            approved = bool(payload.get("approved"))
            decision = ApprovalDecision(
                approved=approved,
                reviewer="ui-reviewer",
                comment="approved in UI" if approved else "rejected in UI",
            )
            self._send_json(run_query(query, approval=decision), HTTPStatus.OK)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _query_from_body(self) -> str:
        return self._validated_query(self._read_json())

    def _validated_query(self, payload: dict[str, Any]) -> str:
        query = str(payload.get("query", "")).strip()
        if not query:
            raise ValueError("query is required")
        return query

    def _read_json(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length).decode("utf-8")
        payload = json.loads(raw_body or "{}")
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
        return payload

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    httpd = ThreadingHTTPServer((host, port), UIRequestHandler)
    print(f"LangGraph Agent UI: http://{host}:{port}")
    httpd.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the LangGraph Agent Lab UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    args = parser.parse_args()
    serve(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
