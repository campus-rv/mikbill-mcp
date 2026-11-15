from __future__ import annotations

from typing import Any

import pytest

from mb_mcp.config import ALL_SCOPES, Settings
from mb_mcp.errors import MikbillError
from mb_mcp.runtime import Runtime


@pytest.fixture
def settings() -> Settings:
    return Settings(base_url="https://mikbill.example.test", login="admin", password="pw",
                    scopes=frozenset(ALL_SCOPES), max_bulk=5)


class FakeClient:
    """Stands in for MikbillClient. `responses` maps uri -> envelope, exception, or callable(params)."""

    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.responses = responses or {}

    async def call(self, uri: str, params: dict[str, Any] | None = None, *, allow_empty: bool = False,
                   no_success_key: bool = False) -> dict[str, Any]:
        self.calls.append((uri, dict(params or {})))
        resp = self.responses.get(uri, {"success": True, "data": []})
        if callable(resp):
            resp = resp(params)
        if isinstance(resp, MikbillError):
            raise resp
        return resp

    def posted(self, uri: str) -> list[dict[str, Any]]:
        return [p for u, p in self.calls if u == uri]


@pytest.fixture
def fake() -> FakeClient:
    return FakeClient()


@pytest.fixture
def runtime(settings: Settings, fake: FakeClient) -> Runtime:
    return Runtime(settings, fake)


ALL_SCOPE_SET = frozenset(ALL_SCOPES)
