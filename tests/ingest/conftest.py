"""Fixtures for the ingest tests: offline replay, no network."""

from __future__ import annotations

import pytest
from usaspending_replay import Replay

from agentic_bi.ingest.client import Client


@pytest.fixture
def replay() -> Replay:
    return Replay()


@pytest.fixture
def client(replay: Replay) -> Client:
    return Client(replay, attempts=2, sleep=lambda _: None)
