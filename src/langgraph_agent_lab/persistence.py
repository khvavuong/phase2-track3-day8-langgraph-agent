"""Checkpointer adapter."""

from __future__ import annotations

import sqlite3
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langgraph.checkpoint.base import BaseCheckpointSaver


def _sqlite_database_path(database_url: str | None) -> str:
    if not database_url:
        return "checkpoints.db"
    if database_url.startswith("sqlite:///"):
        return database_url.removeprefix("sqlite:///")
    return database_url


def _build_sqlite_checkpointer(database_url: str | None) -> BaseCheckpointSaver[Any]:
    database_path = _sqlite_database_path(database_url)
    if database_path not in {":memory:", ""} and not database_path.startswith("file:"):
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)

    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError as exc:
        raise RuntimeError(
            "SQLite checkpointer requires: pip install langgraph-checkpoint-sqlite"
        ) from exc

    conn = sqlite3.connect(
        database_path,
        check_same_thread=False,
        uri=database_path.startswith("file:"),
    )
    conn.execute("PRAGMA journal_mode=WAL")
    return SqliteSaver(conn=conn)


def build_checkpointer(
    kind: str = "memory",
    database_url: str | None = None,
) -> BaseCheckpointSaver[Any] | None:
    """Return a LangGraph checkpointer.

    Memory is the local default. SQLite is durable enough for the lab's recovery evidence and
    uses an explicit connection because ``SqliteSaver.from_conn_string`` is a context manager.
    """
    normalized_kind = kind.strip().lower()
    if normalized_kind == "none":
        return None
    if normalized_kind == "memory":
        from langgraph.checkpoint.memory import MemorySaver

        return MemorySaver()
    if normalized_kind == "sqlite":
        return _build_sqlite_checkpointer(database_url)
    if normalized_kind == "postgres":
        try:
            postgres_module = import_module("langgraph.checkpoint.postgres")
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "Postgres checkpointer requires: pip install langgraph-checkpoint-postgres"
            ) from exc
        return postgres_module.PostgresSaver.from_conn_string(database_url or "")
    raise ValueError(f"Unknown checkpointer kind: {kind}")
