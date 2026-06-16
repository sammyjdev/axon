"""Tests for the AXON OpenAI-compatible HTTP endpoint.

All retrieval and LLM calls are monkeypatched so no network or LLM access is
needed.  The tests verify:
- response shape (choices, contexts, usage.total_tokens)
- that the query is taken from the last user message
- that retrieval segments are surfaced verbatim in ``contexts``
- that ``usage.total_tokens`` is a positive integer
- that a missing user message returns 422
- that retrieval errors return 500 with a useful detail
- the /health liveness probe

Patch targets use the source modules (axon.mcp.server and axon.router.engine)
because the handler imports them lazily inside the function body.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed; skipping HTTP endpoint tests")
pytest.importorskip("httpx", reason="httpx not installed; skipping HTTP endpoint tests")

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from axon.context.contracts import ContextPack, RetrievalStrategy
from axon.context.registry import DEFAULT_SEARCH_CONTEXTS
from axon.http.app import app

# ---------------------------------------------------------------------------
# Helpers / constants
# ---------------------------------------------------------------------------

_FAKE_STRATEGY = RetrievalStrategy(
    name="balanced",
    contexts=DEFAULT_SEARCH_CONTEXTS,
    max_segments=8,
    max_chars=8_000,
    prefer_local=False,
    enable_compression=True,
)

_FAKE_SEGMENTS = (
    "### recall_context (python)\nArquivo: axon/recall/strategy.py\nScore: 0.91\nTrecho: recall ranked by recency",
    "### ContextPack (python)\nArquivo: axon/context/contracts.py\nScore: 0.85\nTrecho: frozen dataclass with segments",
)

_FAKE_PACK = ContextPack(
    strategy=_FAKE_STRATEGY,
    task_type="CODE_ANALYSIS",
    profile="free",
    mode="hybrid-local",
    contexts=DEFAULT_SEARCH_CONTEXTS,
    segments=_FAKE_SEGMENTS,
    metadata=(("ctx", "auto"), ("hits", "2")),
)

_FAKE_RAW_CONTEXT = "\n\n".join(_FAKE_SEGMENTS)
_FAKE_ANSWER = "AXON uses exponential-decay recency scoring for recall ranking."

# Patch targets: the handler imports these lazily from their source modules.
_PATCH_RETRIEVE = "axon.mcp.server._retrieve_context"
_PATCH_COMPLETE = "axon.router.engine.complete"


def _make_retrieve_mock() -> AsyncMock:
    """Return an AsyncMock for _retrieve_context that yields our fake pack."""
    return AsyncMock(return_value=(_FAKE_RAW_CONTEXT, _FAKE_PACK, []))


def _make_complete_mock() -> AsyncMock:
    """Return an AsyncMock for complete that returns _FAKE_ANSWER."""
    return AsyncMock(return_value=_FAKE_ANSWER)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    """TestClient with retrieval and LLM calls mocked out."""
    with (
        patch(_PATCH_RETRIEVE, new=_make_retrieve_mock()),
        patch(_PATCH_COMPLETE, new=_make_complete_mock()),
    ):
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c


# ---------------------------------------------------------------------------
# Tests — response shape
# ---------------------------------------------------------------------------


def test_chat_completions_returns_200(client: TestClient) -> None:
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "axon", "messages": [{"role": "user", "content": "How does recall work?"}]},
    )
    assert resp.status_code == 200


def test_chat_completions_has_choices(client: TestClient) -> None:
    resp = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "explain recall"}]},
    )
    body = resp.json()
    assert "choices" in body
    assert len(body["choices"]) == 1
    choice = body["choices"][0]
    assert choice["index"] == 0
    assert choice["message"]["role"] == "assistant"
    assert isinstance(choice["message"]["content"], str)
    assert choice["message"]["content"]


def test_chat_completions_answer_matches_mock(client: TestClient) -> None:
    resp = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "explain recall"}]},
    )
    assert resp.json()["choices"][0]["message"]["content"] == _FAKE_ANSWER


def test_chat_completions_contexts_is_list_of_strings(client: TestClient) -> None:
    resp = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "explain recall"}]},
    )
    body = resp.json()
    assert "contexts" in body, "top-level 'contexts' key is required by gnomon-eval"
    contexts = body["contexts"]
    assert isinstance(contexts, list)
    assert all(isinstance(c, str) for c in contexts)


def test_chat_completions_contexts_contain_segments(client: TestClient) -> None:
    resp = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "explain recall"}]},
    )
    contexts = resp.json()["contexts"]
    # The fake pack has 2 segments; both must appear verbatim.
    assert len(contexts) == len(_FAKE_SEGMENTS)
    for expected_seg in _FAKE_SEGMENTS:
        assert expected_seg in contexts


def test_chat_completions_usage_total_tokens_present(client: TestClient) -> None:
    resp = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "explain recall"}]},
    )
    body = resp.json()
    assert "usage" in body, "'usage' key is required by gnomon-eval"
    assert "total_tokens" in body["usage"], "'usage.total_tokens' is required by gnomon-eval"
    assert isinstance(body["usage"]["total_tokens"], int)
    assert body["usage"]["total_tokens"] > 0


def test_chat_completions_id_present(client: TestClient) -> None:
    resp = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "x"}]},
    )
    body = resp.json()
    assert "id" in body
    assert body["id"].startswith("axon-")


def test_chat_completions_object_field(client: TestClient) -> None:
    resp = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "x"}]},
    )
    assert resp.json()["object"] == "chat.completion"


# ---------------------------------------------------------------------------
# Tests — query extraction
# ---------------------------------------------------------------------------


def test_uses_last_user_message_as_query() -> None:
    """The endpoint must pick the last user message when there are many."""
    mock_retrieve = _make_retrieve_mock()
    with (
        patch(_PATCH_RETRIEVE, new=mock_retrieve),
        patch(_PATCH_COMPLETE, new=_make_complete_mock()),
    ):
        with TestClient(app) as c:
            c.post(
                "/v1/chat/completions",
                json={
                    "messages": [
                        {"role": "user", "content": "first question"},
                        {"role": "assistant", "content": "some answer"},
                        {"role": "user", "content": "follow-up question"},
                    ]
                },
            )
    # The query passed to _retrieve_context must be the last user message.
    call_kwargs = mock_retrieve.call_args.kwargs
    assert call_kwargs["query"] == "follow-up question"


# ---------------------------------------------------------------------------
# Tests — error paths
# ---------------------------------------------------------------------------


def test_no_user_message_returns_422() -> None:
    with TestClient(app) as c:
        resp = c.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "system", "content": "you are helpful"}]},
        )
    assert resp.status_code == 422


def test_retrieval_error_returns_500() -> None:
    with (
        patch(
            _PATCH_RETRIEVE,
            new=AsyncMock(side_effect=RuntimeError("qdrant down")),
        ),
    ):
        with TestClient(app, raise_server_exceptions=False) as c:
            resp = c.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "anything"}]},
            )
    assert resp.status_code == 500
    assert "qdrant down" in resp.json()["detail"]


def test_llm_error_still_returns_contexts() -> None:
    """When the LLM call fails, contexts are still returned so the evaluator can score recall."""
    with (
        patch(_PATCH_RETRIEVE, new=_make_retrieve_mock()),
        patch(
            _PATCH_COMPLETE,
            new=AsyncMock(side_effect=RuntimeError("model offline")),
        ),
    ):
        with TestClient(app) as c:
            resp = c.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "anything"}]},
            )
    assert resp.status_code == 200
    body = resp.json()
    # contexts must still be present
    assert body["contexts"] == list(_FAKE_SEGMENTS)
    # answer should mention the failure
    assert "LLM unavailable" in body["choices"][0]["message"]["content"]
    # usage must still be present
    assert body["usage"]["total_tokens"] > 0


# ---------------------------------------------------------------------------
# Tests — health probe
# ---------------------------------------------------------------------------


def test_health_endpoint() -> None:
    with TestClient(app) as c:
        resp = c.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
