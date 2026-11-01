import duckdb

import agentic_bi


def test_package_imports() -> None:
    assert agentic_bi.__version__ == "0.1.0"


def test_duckdb_runs_in_memory() -> None:
    assert duckdb.connect().execute("select 40 + 2").fetchone() == (42,)
