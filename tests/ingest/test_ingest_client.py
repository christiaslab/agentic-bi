"""The standard-library client: host limits, retries and exact decimals."""

import urllib.request
from decimal import Decimal

import pytest

from agentic_bi.ingest.client import (
    AllowedHostsRedirect,
    Client,
    HttpError,
    api_url,
    check_url,
)


@pytest.mark.parametrize(
    "url",
    [
        "http://api.usaspending.gov/api/v2/",
        "https://example.com/api/v2/",
        "https://api.usaspending.gov.example.com/",
        "https://user:pw@api.usaspending.gov/",
        "https://api.usaspending.gov:8443/",
        "file:///etc/passwd",
    ],
)
def test_refuses_urls_outside_the_usaspending_hosts(url: str) -> None:
    with pytest.raises(HttpError):
        check_url(url)


def test_accepts_both_usaspending_hosts() -> None:
    assert check_url("https://files.usaspending.gov/generated_downloads/x.zip")
    assert api_url("/api/v2/download/status", {"file_name": "a b.zip"}).endswith(
        "/api/v2/download/status?file_name=a+b.zip"
    )
    for path in ("/../etc", "/api/v2/../../etc", "/api/v2/x?y=1"):
        with pytest.raises(HttpError):
            api_url(path)


def _scripted(*answers):
    calls = []

    def transport(method, url, body):
        calls.append(url)
        answer = answers[len(calls) - 1]
        if isinstance(answer, BaseException):
            raise answer
        return answer

    return transport, calls


def test_retries_empty_replies_and_dropped_connections() -> None:
    transport, calls = _scripted(
        ConnectionResetError("empty reply"), (200, b""), (503, b"busy"), (200, b'{"a": 1.10}')
    )
    sleeps = []
    client = Client(transport, attempts=4, sleep=sleeps.append)
    assert client.get_json("/api/v2/x/") == {"a": Decimal("1.10")}
    assert len(calls) == 4
    assert len(sleeps) == 3


def test_client_error_is_not_retried() -> None:
    transport, calls = _scripted((400, b'{"detail": "bad filter"}'))
    with pytest.raises(HttpError, match="HTTP 400"):
        Client(transport, attempts=5, sleep=lambda _: None).post_json("/api/v2/x/", {})
    assert len(calls) == 1


def test_gives_up_after_the_last_attempt() -> None:
    transport, calls = _scripted(*[OSError("down")] * 3)
    with pytest.raises(HttpError, match="after 3 attempts"):
        Client(transport, attempts=3, sleep=lambda _: None).get_bytes(
            "https://files.usaspending.gov/generated_downloads/x.zip"
        )
    assert len(calls) == 3


def test_redirect_off_the_usaspending_hosts_is_refused() -> None:
    request = urllib.request.Request("https://api.usaspending.gov/api/v2/x/")
    handler = AllowedHostsRedirect()
    with pytest.raises(HttpError):
        handler.redirect_request(request, None, 302, "Found", {}, "https://example.com/x")
    moved = handler.redirect_request(
        request, None, 302, "Found", {}, "https://files.usaspending.gov/generated_downloads/x.zip"
    )
    assert moved.full_url.startswith("https://files.usaspending.gov/")
