"""Unit tests for the ASD chunker, embedder, categorizer, and the chunk-based
validate path (issue #65). All Azure calls are mocked."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agents import asd_chunker, categorize_chunk, embeddings
from agents import validate_agent as va
from agents.categories import PolicyCategory
from agents.config import Config


# ---------------------------------------------------------------------------
# asd_chunker._split_markdown
# ---------------------------------------------------------------------------

def test_split_markdown_cuts_on_headings():
    md = (
        "# A\nfirst section body.\n\n"
        "# B\nsecond section body.\n\n"
        "## C\nthird subsection body.\n"
    )
    chunks = asd_chunker._split_markdown(md, max_chars=4000, overlap=0, min_chars=10)
    assert len(chunks) == 3
    assert chunks[0].startswith("# A")
    assert chunks[1].startswith("# B")
    assert chunks[2].startswith("## C")


def test_split_markdown_windows_oversized_sections():
    big = "# Big\n" + ("paragraph. " * 1000)  # ~11000 chars
    chunks = asd_chunker._split_markdown(big, max_chars=3500, overlap=200, min_chars=10)
    assert len(chunks) >= 3
    for c in chunks:
        assert len(c) <= 3500


def test_split_markdown_merges_tiny_tails():
    md = "# A\n" + ("x" * 3500) + "\n# B\nshort tail"
    chunks = asd_chunker._split_markdown(md, max_chars=3500, overlap=0, min_chars=200)
    # Tail "# B\nshort tail" is ~14 chars, should be merged onto the previous chunk.
    assert "# B" in chunks[-1]
    assert "short tail" in chunks[-1]


def test_split_markdown_empty_input_returns_empty():
    assert asd_chunker._split_markdown("", 3500, 200, 200) == []
    assert asd_chunker._split_markdown("   \n  ", 3500, 200, 200) == []


# ---------------------------------------------------------------------------
# embeddings.embed_text  (monkeypatch the OpenAI client)
# ---------------------------------------------------------------------------

class _FakeEmbResp:
    def __init__(self, dim):
        from types import SimpleNamespace
        self.data = [SimpleNamespace(embedding=[0.1] * dim)]


class _FakeEmbeddingsOp:
    def __init__(self):
        self.calls = []

    def create(self, *, model, input, **kw):
        self.calls.append({"model": model, "input": input})
        return _FakeEmbResp(embeddings.EMBEDDING_DIMENSIONS)


class _FakeAOAI:
    def __init__(self):
        self.embeddings = _FakeEmbeddingsOp()


@pytest.fixture
def _patch_embeddings_client(monkeypatch):
    fake = _FakeAOAI()
    embeddings._CLIENT_CACHE.clear()
    monkeypatch.setattr(embeddings, "_get_aoai_client", lambda endpoint: fake)
    monkeypatch.setenv("FOUNDRY_ENDPOINT", "https://test/")
    monkeypatch.setenv("FOUNDRY_EMBEDDINGS_DEPLOYMENT", "text-embedding-3-large")
    yield fake
    embeddings._CLIENT_CACHE.clear()


def test_embed_text_returns_native_dim_vector(_patch_embeddings_client):
    vec = embeddings.embed_text("hello world")
    assert len(vec) == embeddings.EMBEDDING_DIMENSIONS == 3072


def test_embed_text_empty_returns_zero_vector(_patch_embeddings_client):
    vec = embeddings.embed_text("")
    assert vec == [0.0] * embeddings.EMBEDDING_DIMENSIONS


def test_embed_text_dimension_mismatch_raises(monkeypatch, _patch_embeddings_client):
    class _BadEmb:
        def create(self, **_kw):
            from types import SimpleNamespace
            return SimpleNamespace(data=[SimpleNamespace(embedding=[0.1] * 1536)])
    monkeypatch.setattr(embeddings, "_get_aoai_client",
                        lambda endpoint: type("X", (), {"embeddings": _BadEmb()})())
    with pytest.raises(RuntimeError, match="Embedding dimensions mismatch"):
        embeddings.embed_text("hi")


# ---------------------------------------------------------------------------
# categorize_chunk.categorize_chunk
# ---------------------------------------------------------------------------

class _FakeChatResp:
    def __init__(self, content):
        from types import SimpleNamespace
        msg = SimpleNamespace(content=content)
        choice = SimpleNamespace(message=msg)
        self.choices = [choice]


class _FakeChat:
    def __init__(self, reply):
        self._reply = reply

    class _Completions:
        def __init__(self, reply):
            self._reply = reply

        def create(self, **_kw):
            return _FakeChatResp(self._reply)

    @property
    def completions(self):
        return _FakeChat._Completions(self._reply)


class _FakeChatAOAI:
    def __init__(self, reply):
        self.chat = _FakeChat(reply)


@pytest.fixture
def _patch_categorize_env(monkeypatch):
    monkeypatch.setenv("FOUNDRY_ENDPOINT", "https://test/")
    monkeypatch.setenv("FOUNDRY_MODEL_DEPLOYMENT", "gpt-test")
    categorize_chunk._CLIENT_CACHE.clear()
    yield
    categorize_chunk._CLIENT_CACHE.clear()


def test_categorize_chunk_parses_known_value(monkeypatch, _patch_categorize_env):
    monkeypatch.setattr(categorize_chunk, "_get_aoai_client",
                        lambda endpoint: _FakeChatAOAI("Network"))
    assert categorize_chunk.categorize_chunk("hub-spoke vnet") is PolicyCategory.NETWORK


def test_categorize_chunk_unknown_value_falls_back_to_general(
    monkeypatch, _patch_categorize_env
):
    monkeypatch.setattr(categorize_chunk, "_get_aoai_client",
                        lambda endpoint: _FakeChatAOAI("Not a category"))
    assert categorize_chunk.categorize_chunk("some text") is PolicyCategory.GENERAL


def test_categorize_chunk_empty_text_short_circuits(_patch_categorize_env):
    assert categorize_chunk.categorize_chunk("") is PolicyCategory.GENERAL
    assert categorize_chunk.categorize_chunk("   ") is PolicyCategory.GENERAL


# ---------------------------------------------------------------------------
# validate_arb_chunks end-to-end with all Azure paths mocked
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, text):
        self.output_text = text


class _FakeResponsesOp:
    def __init__(self, capture):
        self._capture = capture
        self._reply = capture.get("reply", "[]")

    def create(self, *, model, input, **_kw):
        # Capture every prompt the agent sees — last-one-wins for inspection.
        self._capture.setdefault("prompts", []).append(input)
        return _FakeResponse(self._reply)


class _FakeOpenAIClient:
    def __init__(self, capture):
        self.responses = _FakeResponsesOp(capture)


class _FakeProjectClient:
    def __init__(self, reply="[]"):
        self._capture = {"reply": reply}

    def get_openai_client(self, *, agent_name):
        return _FakeOpenAIClient(self._capture)

    @property
    def prompts(self):
        return self._capture.get("prompts", [])


def _make_cfg() -> Config:
    cfg = Config()
    cfg.foundry_project_endpoint = "https://test"
    cfg.foundry_model_deployment = "gpt-test"
    return cfg


def test_validate_arb_chunks_full_path(monkeypatch):
    """End-to-end: 3 chunks → 3 agent calls → aggregated findings.

    Mocks the chunker, categorizer, embedder, search, and the agent so the
    test runs offline. Asserts that:
      * one prompt is sent per chunk,
      * each prompt carries the chunk's chosen category,
      * findings from all chunks are flattened into the return list.
    """
    monkeypatch.setattr(va, "_resolve_agent_id", lambda name, cfg: "agent-id-1",
                        raising=False)

    # Three chunks of mock content.
    monkeypatch.setattr(
        "agents.asd_chunker.chunk_asd_document",
        lambda b, fn=None: ["chunk-a network text", "chunk-b storage text", "chunk-c reliability text"],
    )

    # Distinct category per chunk so we can assert the prompts.
    categories = iter([PolicyCategory.NETWORK,
                       PolicyCategory.STORAGE_AND_DATA,
                       PolicyCategory.RELIABILITY])
    monkeypatch.setattr(
        "agents.categorize_chunk.categorize_chunk",
        lambda text: next(categories),
    )

    # Embeddings are mocked to a constant vector.
    monkeypatch.setattr(
        "agents.embeddings.embed_text",
        lambda text: [0.0] * 3072,
    )

    # Retrieval — one hit per call, category-tagged so the prompt shows it.
    def _fake_retrieve(query, category, vector, top_k=8):
        return [{"header": None, "category": category, "content": f"policy for {category}",
                 "@rerank": 1.0}]
    monkeypatch.setattr(va, "_retrieve_for_chunk", _fake_retrieve)

    # Agent emits one finding per call, threading category through Principles.
    fake = _FakeProjectClient(
        reply='[{"Type":"Violation","Issue":"x","Description":"y",'
              '"Principles":"P","Mandatory":true,"Category":"Network"}]'
    )

    out = asyncio.run(va.validate_arb_chunks(
        file_bytes=b"<<fake bytes>>",
        filename="sample.docx",
        config=_make_cfg(),
        client=fake,
    ))

    assert len(out) == 3, f"Expected 3 findings (one per chunk), got {len(out)}: {out}"
    assert len(fake.prompts) == 3
    # Each prompt should contain the chunk's chosen category.
    joined = "\n----\n".join(fake.prompts)
    assert "Network" in joined
    assert "Storage and Data" in joined
    assert "Reliability" in joined


def test_validate_arb_chunks_chunker_failure_returns_single_error(monkeypatch):
    def _boom(file_bytes, filename=None):
        raise RuntimeError("DocIntel down")
    monkeypatch.setattr("agents.asd_chunker.chunk_asd_document", _boom)

    out = asyncio.run(va.validate_arb_chunks(
        file_bytes=b"x", filename="x.docx",
        config=_make_cfg(), client=_FakeProjectClient(),
    ))
    assert len(out) == 1
    assert out[0]["Type"] == "Error"
    assert out[0]["Issue"] == "chunking_failed"
    assert "DocIntel down" in out[0]["Description"]


def test_validate_arb_chunks_empty_chunks_returns_empty(monkeypatch):
    monkeypatch.setattr("agents.asd_chunker.chunk_asd_document",
                        lambda b, fn=None: [])
    out = asyncio.run(va.validate_arb_chunks(
        file_bytes=b"x", filename="x.docx",
        config=_make_cfg(), client=_FakeProjectClient(),
    ))
    assert out == []
