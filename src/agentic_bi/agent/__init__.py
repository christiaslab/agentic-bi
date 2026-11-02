"""The analyst agent of ADR-0002 and ADR-0005.

A model answers spending questions only through four project tools (``catalog``, ``schema``,
``run_query``, ``explain_measure``), never writes SQL, and returns a typed ``Answer`` with the
structured queries it ran, their SQL, parameters and rows, and a short text whose every figure
appears in those rows, or a refusal naming the catalog reason. ``ask`` is the loop;
``AnthropicModel`` is the live model and ``ReplayModel`` replays hand-written turns offline.
Reading guide: docs/agent.md.
"""

from .answer import Answer, QueryRun, Refusal
from .loop import AgentResult, ask
from .model import AnthropicModel, ModelError, ReplayModel
from .settings import DEFAULT_MODEL, AgentSettings
from .tools import ToolBox, catalog, explain_measure, run_query, schema

__all__ = [
    "DEFAULT_MODEL",
    "AgentResult",
    "AgentSettings",
    "Answer",
    "AnthropicModel",
    "ModelError",
    "QueryRun",
    "Refusal",
    "ReplayModel",
    "ToolBox",
    "ask",
    "catalog",
    "explain_measure",
    "run_query",
    "schema",
]
