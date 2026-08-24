from contextlib import contextmanager
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.models import AIPromptEvent, Base, SemanticDocument
from core.semantic_index import (
    OllamaEmbeddingProvider,
    SourceDocument,
    _embed_resilient,
    ask_local_context,
    build_semantic_index,
    collect_source_documents,
    semantic_search,
)


class DictConfig:
    def __init__(self, data):
        self.data = data

    def get(self, key_path, default=None):
        value = self.data
        for key in key_path.split("."):
            if not isinstance(value, dict) or key not in value:
                return default
            value = value[key]
        return value


class TempDatabase:
    def __init__(self, path):
        self.engine = create_engine(f"sqlite:///{path.as_posix()}")
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine)

    @contextmanager
    def session_scope(self):
        session = self.factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


class FakeEmbeddingProvider:
    model = "fake-semantic-v1"

    def embed(self, texts):
        return [
            [1.0, 0.0] if "rollback" in text.lower() else [0.0, 1.0]
            for text in texts
        ]


def _config():
    return DictConfig({
        "semantic_index": {
            "enabled": True,
            "batch_size": 2,
            "max_document_chars": 6000,
            "default_top_k": 3,
            "allow_remote": False,
        }
    })


def _seed(database):
    with database.session_scope() as session:
        session.add_all([
            AIPromptEvent(
                timestamp=datetime(2026, 8, 25, 9, 0),
                platform="codex",
                prompt_text="Prepare the rollback procedure",
                response_text="Restore both package and schema backup.",
                project_tag="alpha",
                turn_key="alpha-1",
                response_status="final_candidate",
            ),
            AIPromptEvent(
                timestamp=datetime(2026, 8, 25, 10, 0),
                platform="claude",
                prompt_text="Draft the release announcement",
                response_text="untrusted response must not be indexed",
                project_tag="beta",
                turn_key="beta-1",
                response_status="partial",
            ),
        ])


def test_incremental_index_and_semantic_retrieval_preserve_provenance(tmp_path):
    database = TempDatabase(tmp_path / "semantic.db")
    _seed(database)
    provider = FakeEmbeddingProvider()

    first = build_semantic_index(database=database, cfg=_config(), provider=provider)
    second = build_semantic_index(database=database, cfg=_config(), provider=provider)
    result = semantic_search(
        "What is the rollback plan?",
        database=database,
        cfg=_config(),
        provider=provider,
        top_k=1,
    )

    assert first["indexed"] == 2
    assert second["indexed"] == 0
    assert second["unchanged"] == 2
    assert result["sources"][0]["project_key"] == "alpha"
    assert result["sources"][0]["source_ref"].startswith("ai_prompt_events:")
    assert result["sources"][0]["trust_status"] == "final_candidate"
    with database.session_scope() as session:
        assert session.query(SemanticDocument).count() == 2


def test_partial_ai_response_is_not_promoted_into_index_content(tmp_path):
    database = TempDatabase(tmp_path / "trust.db")
    _seed(database)
    documents = collect_source_documents(database=database, project="beta")
    assert len(documents) == 1
    assert "untrusted response" not in documents[0].content
    assert documents[0].trust_status == "partial"


def test_ask_can_return_retrieval_only_without_generation(tmp_path):
    database = TempDatabase(tmp_path / "ask.db")
    _seed(database)
    provider = FakeEmbeddingProvider()
    build_semantic_index(database=database, cfg=_config(), provider=provider)
    result = ask_local_context(
        "rollback",
        database=database,
        cfg=_config(),
        provider=provider,
        synthesize=False,
    )
    assert result["status"] == "retrieved"
    assert result["answer"] is None
    assert result["sources"][0]["citation"] == "S1"


def test_remote_embedding_endpoint_is_fail_closed():
    cfg = DictConfig({
        "semantic_index": {
            "base_url": "https://example.com",
            "embedding_model": "example",
            "allow_remote": False,
        }
    })
    with pytest.raises(ValueError, match="local-only"):
        OllamaEmbeddingProvider(cfg)


def test_non_ascii_model_failure_is_explicitly_degraded():
    class AsciiOnlyProvider:
        model = "ascii-only"

        def embed(self, texts):
            if any(any(ord(char) > 127 for char in text) for text in texts):
                raise RuntimeError("model produced NaN")
            return [[1.0, 0.0] for _ in texts]

    document = SourceDocument(
        "ai_turn",
        "1",
        "ai_prompt_events:1",
        datetime(2026, 8, 25, 12, 0),
        "alpha",
        "Codex turn",
        "rollback 回復流程",
        "final_candidate",
    )
    completed, failures = _embed_resilient(AsciiOnlyProvider(), [document], 3000)
    assert failures == []
    assert completed[0][2] == "ascii_fallback"
