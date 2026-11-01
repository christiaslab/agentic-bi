"""A small HTTP client over the standard library, limited to the USAspending hosts.

The hosts drop roughly one connection in two with an empty reply, so every request is
retried with a short backoff. A transport function can be injected, which is how the
tests replay recorded responses with no network.
"""

from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from decimal import Decimal

from agentic_bi.ingest.spec import ALLOWED_HOSTS, API_BASE

USER_AGENT = "agentic-bi-ingest/0.1 (+https://github.com/christiaslab/agentic-bi)"
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

# (method, url, json body or None) -> (status, body bytes)
Transport = Callable[[str, str, bytes | None], tuple[int, bytes]]


class HttpError(RuntimeError):
    """A request that failed for good: a refused host, a client error, or retries spent."""


def check_url(url: str) -> str:
    """Return ``url`` when it is https on an allowed host; raise otherwise."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS:
        raise HttpError(
            f"refused URL outside the USAspending hosts: {parts.scheme}://{parts.hostname}"
        )
    if parts.username or parts.password or parts.port not in (None, 443):
        raise HttpError("refused URL with credentials or a non-default port")
    return url


def urllib_transport(timeout: float = 120.0) -> Transport:
    """The live transport. Proxy and CA settings come from the environment."""

    def send(method: str, url: str, body: bytes | None) -> tuple[int, bytes]:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json, */*"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    return send


class Client:
    """JSON and file requests against the USAspending hosts, with retries."""

    def __init__(
        self,
        transport: Transport | None = None,
        *,
        attempts: int = 8,
        backoff: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._transport = transport or urllib_transport()
        self._attempts = attempts
        self._backoff = backoff
        self._sleep = sleep

    def request(self, method: str, url: str, body: dict | None = None) -> bytes:
        """Send one request and return the body; raise ``HttpError`` when it cannot succeed."""
        check_url(url)
        payload = None if body is None else json.dumps(body, sort_keys=True).encode("utf-8")
        last = "no attempt made"
        for attempt in range(1, self._attempts + 1):
            try:
                status, data = self._transport(method, url, payload)
            except (urllib.error.URLError, http.client.HTTPException, OSError) as error:
                last = f"{type(error).__name__}: {error}"
            else:
                if 200 <= status < 300 and data:
                    return data
                if 200 <= status < 300:
                    last = "empty body"
                elif status in RETRYABLE_STATUS:
                    last = f"HTTP {status}"
                else:
                    detail = data[:300].decode("utf-8", "replace")
                    raise HttpError(f"{method} {url} answered HTTP {status}: {detail}")
            if attempt < self._attempts:
                self._sleep(min(self._backoff * attempt, 10.0))
        raise HttpError(f"{method} {url} failed after {self._attempts} attempts: {last}")

    def get_json(self, path: str, params: dict[str, str] | None = None) -> dict:
        """GET an API path; decimals stay exact."""
        url = api_url(path, params)
        return json.loads(self.request("GET", url), parse_float=Decimal)

    def post_json(self, path: str, body: dict) -> dict:
        """POST a JSON body to an API path; decimals stay exact."""
        return json.loads(self.request("POST", api_url(path), body), parse_float=Decimal)

    def get_bytes(self, url: str) -> bytes:
        """GET a file, for example a generated download."""
        return self.request("GET", url)


def api_url(path: str, params: dict[str, str] | None = None) -> str:
    """The full URL of an API path such as ``/api/v2/search/spending_by_award_count/``."""
    if not path.startswith("/api/v2/"):
        raise HttpError(f"not an API v2 path: {path}")
    query = "" if not params else "?" + urllib.parse.urlencode(params)
    return API_BASE + path + query
