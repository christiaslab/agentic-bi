"""SQLMesh configuration: DuckDB under data/tmp/, the committed snapshot as the only source.

The settings live in ``agentic_bi.warehouse`` so that tests can build the same project
into a temporary database.
"""

from agentic_bi.warehouse import project_config

config = project_config()
