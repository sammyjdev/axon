"""LangGraph ``StateGraph`` definition for Praxis orchestration.

The graph is single-step and action-routed: every invocation reads the
``action`` channel, runs exactly one node (plan / get_next / record / replan),
and ends. State persists between invocations through the checkpointer, which is
what makes a session resumable after a process restart.
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from praxis.nodes import get_next_node, plan_node, record_node, replan_node
from praxis.state import GraphState

ACTIONS: tuple[str, ...] = ("plan", "get_next", "record", "replan")


def _route(state: GraphState) -> str:
    action = state.get("action") or ""
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action!r}; expected one of {ACTIONS}")
    return action


def build_graph(checkpointer: BaseCheckpointSaver[Any] | None = None) -> CompiledStateGraph:
    """Compile the action-routed orchestration graph."""
    builder = StateGraph(GraphState)
    builder.add_node("plan", plan_node)
    builder.add_node("get_next", get_next_node)
    builder.add_node("record", record_node)
    builder.add_node("replan", replan_node)
    builder.add_conditional_edges(START, _route, {action: action for action in ACTIONS})
    for action in ACTIONS:
        builder.add_edge(action, END)
    return builder.compile(checkpointer=checkpointer)
