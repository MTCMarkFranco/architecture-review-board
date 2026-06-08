"""Offline smoke tests for search/build_index.py (issue #33).

Asserts that ``make_embedder`` passes ``dimensions=EMBEDDING_DIMENSIONS``
to the Azure OpenAI client so the index schema's 1536 vector dimension is
honoured even when the deployment defaults to text-embedding-3-large
(native 3072).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from search import build_index


class _StubEmbeddings:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            data=[SimpleNamespace(embedding=[0.0] * build_index.EMBEDDING_DIMENSIONS)]
        )


class _StubClient:
    def __init__(self) -> None:
        self.embeddings = _StubEmbeddings()


def test_embed_passes_dimensions(monkeypatch):
    stub = _StubClient()

    class _StubCred:
        def get_token(self, *_a, **_kw):
            return SimpleNamespace(token="fake-token")

    monkeypatch.setattr(build_index, "DefaultAzureCredential", lambda: _StubCred())
    monkeypatch.setattr(
        "openai.AzureOpenAI", lambda **_kw: stub, raising=True
    )

    embed = build_index.make_embedder(
        endpoint="https://fake.cognitiveservices.azure.com",
        deployment="text-embedding-3-large",
    )

    vec = embed("hello world")

    assert len(vec) == build_index.EMBEDDING_DIMENSIONS == 1536
    assert len(stub.embeddings.calls) == 1
    call = stub.embeddings.calls[0]
    assert call["dimensions"] == 1536, (
        f"embeddings.create called without dimensions=1536: {call}"
    )
    assert call["model"] == "text-embedding-3-large"
