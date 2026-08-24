"""P3-2/P3-3：本機 Ollama semantic index、可追溯 retrieval 與 omni ask。"""

from __future__ import annotations

import hashlib
import json
import math
import struct
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Sequence
from urllib.parse import urlparse

import requests
from sqlalchemy import desc

from core.config import get_config
from core.database import get_db
from core.models import (
    AIPromptEvent,
    FileActivityEvent,
    GitActivityEvent,
    OpenLoop,
    ProjectState,
    SemanticDocument,
)
from core.time_utils import get_local_now


@dataclass(frozen=True)
class SourceDocument:
    source_type: str
    source_id: str
    source_ref: str
    updated_at: datetime | None
    project_key: str | None
    title: str
    content: str
    trust_status: str

    @property
    def content_hash(self) -> str:
        payload = "\n".join(
            (
                self.source_type,
                self.source_id,
                self.project_key or "",
                self.title,
                self.content,
                self.trust_status,
            )
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _loopback_only(base_url: str, allow_remote: bool) -> str:
    parsed = urlparse(str(base_url).strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("semantic_index base_url must be an absolute HTTP(S) URL")
    if not allow_remote and parsed.hostname.lower() not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("semantic_index is local-only; remote embedding/generation is disabled")
    return str(base_url).rstrip("/")


class OllamaEmbeddingProvider:
    def __init__(self, cfg: Any | None = None) -> None:
        cfg = cfg or get_config()
        self.model = str(cfg.get("semantic_index.embedding_model", "bge-m3:latest"))
        self.base_url = _loopback_only(
            str(cfg.get("semantic_index.base_url", "http://127.0.0.1:11434")),
            bool(cfg.get("semantic_index.allow_remote", False)),
        )

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        response = requests.post(
            f"{self.base_url}/api/embed",
            json={"model": self.model, "input": list(texts)},
            timeout=180,
        )
        if not response.ok:
            raise RuntimeError(
                f"Ollama embedding HTTP {response.status_code}: {response.text[:500]}"
            )
        vectors = response.json().get("embeddings") or []
        if len(vectors) != len(texts):
            raise RuntimeError("Ollama returned an unexpected embedding count")
        return [[float(value) for value in vector] for vector in vectors]


def _pack_vector(vector: Sequence[float]) -> bytes:
    if not vector or not all(math.isfinite(float(value)) for value in vector):
        raise ValueError("embedding vector must contain finite values")
    return struct.pack(f"<{len(vector)}f", *[float(value) for value in vector])


def _unpack_vector(blob: bytes, dimensions: int) -> tuple[float, ...]:
    if dimensions <= 0 or len(blob) != dimensions * 4:
        raise ValueError("stored embedding dimensions do not match the BLOB length")
    return struct.unpack(f"<{dimensions}f", blob)


def _bounded(text: Any, limit: int) -> str:
    return str(text or "").strip()[:limit]


def _embedding_text(text: str, limit: int) -> str:
    """移除可能使本機 embedding model 產生 NaN 的 control characters。"""
    normalized = unicodedata.normalize("NFKC", str(text or ""))
    cleaned = "".join(
        char for char in normalized
        if char in {"\n", "\t"} or not unicodedata.category(char).startswith("C")
    )
    return cleaned[:limit].strip() or "(empty observed record)"


def _embed_resilient(
    provider: Any,
    documents: Sequence[SourceDocument],
    input_limit: int,
) -> tuple[list[tuple[SourceDocument, list[float], str]], list[dict[str, str]]]:
    """二分隔離 Ollama batch failure；單筆再逐級縮短，最後明確列入 failure。"""
    if not documents:
        return [], []
    texts = [_embedding_text(item.content, input_limit) for item in documents]
    try:
        vectors = provider.embed(texts)
        if len(vectors) != len(documents):
            raise RuntimeError("embedding provider returned an incomplete index batch")
        completed = []
        for item, vector in zip(documents, vectors):
            mode = "normalized_full" if len(item.content) <= input_limit else "normalized_truncated"
            completed.append((item, vector, mode))
        return completed, []
    except Exception as exc:
        if len(documents) > 1:
            midpoint = len(documents) // 2
            left_ok, left_failed = _embed_resilient(provider, documents[:midpoint], input_limit)
            right_ok, right_failed = _embed_resilient(provider, documents[midpoint:], input_limit)
            return left_ok + right_ok, left_failed + right_failed

        item = documents[0]
        last_error = exc
        for retry_limit in sorted({max(500, input_limit // 2), 1000, 500}, reverse=True):
            if retry_limit >= input_limit:
                continue
            try:
                vector = provider.embed([_embedding_text(item.content, retry_limit)])[0]
                return [(item, vector, f"normalized_truncated_{retry_limit}")], []
            except Exception as retry_exc:
                last_error = retry_exc
        ascii_text = _embedding_text(item.content, input_limit).encode(
            "ascii", "ignore"
        ).decode("ascii").strip()
        if ascii_text:
            try:
                vector = provider.embed([ascii_text])[0]
                return [(item, vector, "ascii_fallback")], []
            except Exception as retry_exc:
                last_error = retry_exc
        metadata_text = (
            f"{item.source_type} {item.title} project {item.project_key or 'unknown'} "
            f"trust {item.trust_status}"
        )
        try:
            vector = provider.embed([metadata_text])[0]
            return [(item, vector, "metadata_only")], []
        except Exception as retry_exc:
            last_error = retry_exc
        return [], [{
            "source_ref": item.source_ref,
            "error": f"{type(last_error).__name__}: {str(last_error)[:300]}",
        }]


def collect_source_documents(
    *,
    database: Any | None = None,
    project: str | None = None,
    max_document_chars: int = 6000,
    limit: int | None = None,
) -> list[SourceDocument]:
    """只索引既有本機 evidence rows，不掃描未授權檔案內容。"""
    database = database or get_db()
    project_text = str(project or "").strip().lower()
    documents: list[SourceDocument] = []

    def matches(*values: Any) -> bool:
        if not project_text:
            return True
        return any(project_text in str(value or "").lower() for value in values)

    with database.session_scope() as session:
        ai_rows = session.query(AIPromptEvent).order_by(desc(AIPromptEvent.timestamp)).all()
        git_rows = session.query(GitActivityEvent).order_by(desc(GitActivityEvent.timestamp)).all()
        file_rows = session.query(FileActivityEvent).order_by(desc(FileActivityEvent.timestamp)).all()
        loop_rows = session.query(OpenLoop).order_by(desc(OpenLoop.last_seen_at)).all()
        project_rows = session.query(ProjectState).order_by(desc(ProjectState.last_activity_at)).all()

        for row in ai_rows:
            if not matches(row.project_tag, row.cwd):
                continue
            prompt = _bounded(row.prompt_text, max_document_chars)
            if len(prompt) < 2:
                continue
            trust = str(row.response_status or "legacy_unverified")
            response = ""
            if trust == "final_candidate":
                response = _bounded(row.response_text, max_document_chars)
            content = f"Prompt:\n{prompt}"
            if response:
                content += f"\n\nTrusted response candidate:\n{response}"
            documents.append(SourceDocument(
                source_type="ai_turn",
                source_id=str(row.id),
                source_ref=f"ai_prompt_events:{row.id}",
                updated_at=row.timestamp,
                project_key=row.project_tag or row.cwd,
                title=f"{row.platform} AI turn",
                content=content[:max_document_chars],
                trust_status=trust,
            ))

        for row in git_rows:
            if not matches(row.repo_name, row.repo_path):
                continue
            content = (
                f"Repository: {row.repo_name}\nBranch: {row.branch}\n"
                f"Commit: {row.commit_hash}\nMessage: {row.message}\n"
                f"Files changed: {row.files_changed_count}; "
                f"insertions: {row.insertions}; deletions: {row.deletions}"
            )
            documents.append(SourceDocument(
                "git_commit", str(row.id), f"git_activity_events:{row.id}",
                row.timestamp, row.repo_name, f"Commit {row.commit_hash[:10]}",
                content[:max_document_chars], "git_observed",
            ))

        for row in file_rows:
            if not matches(row.project_name, row.file_path):
                continue
            content = (
                f"File: {row.file_path}\nAction: {row.action}\n"
                f"Type: {row.file_type}\nObserved diff: {row.diff_summary or 'not available'}"
            )
            documents.append(SourceDocument(
                "file_activity", str(row.id), f"file_activity_events:{row.id}",
                row.timestamp, row.project_name, f"{row.action}: {row.file_name}",
                content[:max_document_chars], "file_metadata_observed",
            ))

        for row in loop_rows:
            if not matches(row.project_key):
                continue
            content = (
                f"Open loop: {row.title}\nStatus: {row.status}\n"
                f"Resolution note: {row.resolution_note or 'none'}"
            )
            documents.append(SourceDocument(
                "open_loop", str(row.id), f"open_loops:{row.id}",
                row.updated_at or row.last_seen_at or row.created_at,
                row.project_key, row.title, content[:max_document_chars],
                f"lifecycle_{row.status}",
            ))

        for row in project_rows:
            if not matches(row.project_key, row.display_name):
                continue
            content = (
                f"Project: {row.display_name}\nCategory: {row.category}\n"
                f"Status: {row.status}\nLast action: {row.last_action_summary or 'not available'}"
            )
            documents.append(SourceDocument(
                "project_state", str(row.id), f"project_states:{row.id}",
                row.last_activity_at, row.project_key, row.display_name,
                content[:max_document_chars], "project_state_observed",
            ))

    documents.sort(key=lambda item: item.updated_at or datetime.min, reverse=True)
    return documents[: max(0, int(limit))] if limit is not None else documents


def build_semantic_index(
    *,
    database: Any | None = None,
    cfg: Any | None = None,
    provider: Any | None = None,
    project: str | None = None,
    rebuild: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    cfg = cfg or get_config()
    if not bool(cfg.get("semantic_index.enabled", True)):
        return {"status": "disabled", "reason": "semantic_index_disabled"}
    database = database or get_db()
    provider = provider or OllamaEmbeddingProvider(cfg)
    model = str(provider.model)
    batch_size = max(1, min(int(cfg.get("semantic_index.batch_size", 8)), 64))
    max_chars = max(500, min(int(cfg.get("semantic_index.max_document_chars", 6000)), 20000))
    embedding_input_chars = max(
        500,
        min(int(cfg.get("semantic_index.embedding_input_chars", 3000)), max_chars),
    )
    sources = collect_source_documents(
        database=database,
        project=project,
        max_document_chars=max_chars,
        limit=limit,
    )

    with database.session_scope() as session:
        query = session.query(SemanticDocument)
        if project:
            query = query.filter(SemanticDocument.project_key == project)
        if rebuild:
            query.delete(synchronize_session=False)
        existing_rows = session.query(SemanticDocument).all()
        existing = {(row.source_type, row.source_id): (row.content_hash, row.embedding_model) for row in existing_rows}

    changed = [
        item for item in sources
        if existing.get((item.source_type, item.source_id)) != (item.content_hash, model)
    ]
    now = get_local_now()
    dimensions: set[int] = set()
    indexed_count = 0
    failures: list[dict[str, str]] = []
    input_modes: dict[str, int] = {}
    for start in range(0, len(changed), batch_size):
        batch = changed[start:start + batch_size]
        completed, batch_failures = _embed_resilient(
            provider,
            batch,
            embedding_input_chars,
        )
        failures.extend(batch_failures)
        # 每個成功 batch 各自原子提交；中斷後可依 content hash 接續，不重算已完成批次。
        with database.session_scope() as session:
            for item, vector, input_mode in completed:
                dimensions.add(len(vector))
                input_modes[input_mode] = input_modes.get(input_mode, 0) + 1
                row = session.query(SemanticDocument).filter_by(
                    source_type=item.source_type,
                    source_id=item.source_id,
                ).first()
                if row is None:
                    row = SemanticDocument(source_type=item.source_type, source_id=item.source_id)
                    session.add(row)
                row.source_ref = item.source_ref
                row.source_updated_at = item.updated_at
                row.project_key = item.project_key
                row.title = item.title
                row.content = item.content
                row.trust_status = item.trust_status
                row.content_hash = item.content_hash
                row.embedding_model = model
                row.embedding_input_mode = input_mode
                row.embedding_dimensions = len(vector)
                row.embedding = _pack_vector(vector)
                row.indexed_at = now
        indexed_count += len(completed)

    return {
        "status": "indexed" if not failures else "indexed_with_failures",
        "provider": "ollama" if isinstance(provider, OllamaEmbeddingProvider) else type(provider).__name__,
        "embedding_model": model,
        "source_documents": len(sources),
        "indexed": indexed_count,
        "unchanged": len(sources) - len(changed),
        "dimensions": sorted(dimensions),
        "embedding_input_modes": input_modes,
        "failures": failures,
        "project": project,
        "rebuild": rebuild,
        "claim_boundary": "Local semantic retrieval over observed SQLite evidence; not proof of completeness or correctness.",
    }


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return -1.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    return dot / (left_norm * right_norm) if left_norm and right_norm else -1.0


def semantic_search(
    question: str,
    *,
    database: Any | None = None,
    cfg: Any | None = None,
    provider: Any | None = None,
    project: str | None = None,
    top_k: int | None = None,
) -> dict[str, Any]:
    question = str(question or "").strip()
    if len(question) < 2:
        raise ValueError("question must contain at least two characters")
    cfg = cfg or get_config()
    database = database or get_db()
    provider = provider or OllamaEmbeddingProvider(cfg)
    model = str(provider.model)
    limit = max(1, min(int(top_k or cfg.get("semantic_index.default_top_k", 6)), 20))
    query_vector = provider.embed([question])[0]

    with database.session_scope() as session:
        query = session.query(SemanticDocument).filter(
            SemanticDocument.embedding_model == model
        )
        if project:
            query = query.filter(SemanticDocument.project_key == project)
        rows = query.all()
        candidates = []
        for row in rows:
            try:
                score = _cosine(
                    query_vector,
                    _unpack_vector(row.embedding, row.embedding_dimensions),
                )
            except ValueError:
                continue
            candidates.append({
                "score": round(score, 6),
                "source_type": row.source_type,
                "source_id": row.source_id,
                "source_ref": row.source_ref,
                "project_key": row.project_key,
                "title": row.title,
                "trust_status": row.trust_status,
                "embedding_input_mode": row.embedding_input_mode,
                "source_updated_at": row.source_updated_at.isoformat(timespec="seconds") if row.source_updated_at else None,
                "excerpt": row.content[:800],
            })
    candidates.sort(key=lambda item: item["score"], reverse=True)
    selected = candidates[:limit]
    for index, item in enumerate(selected, start=1):
        item["citation"] = f"S{index}"
    return {
        "question": question,
        "embedding_model": model,
        "project": project,
        "indexed_candidates": len(candidates),
        "sources": selected,
        "claim_boundary": "Similarity ranks local evidence; it does not validate source truth or coverage.",
    }


def _generate_local_answer(question: str, sources: Sequence[dict], cfg: Any) -> tuple[str, str]:
    base_url = _loopback_only(
        str(cfg.get("synthesizer.ollama.base_url", "http://127.0.0.1:11434")),
        bool(cfg.get("semantic_index.allow_remote", False)),
    )
    model = str(cfg.get("synthesizer.ollama.model", "llama3.1:8b"))
    evidence = "\n\n".join(
        f"[{item['citation']}] source={item['source_ref']} trust={item['trust_status']} "
        f"embedding_input={item['embedding_input_mode']} "
        f"project={item.get('project_key')} updated={item.get('source_updated_at')}\n{item['excerpt']}"
        for item in sources
    )
    prompt = (
        "你是 OmniContext 本機證據問答器。只能依下列 evidence 回答；"
        "不得把相似度、AI 對話或 metadata 說成已證實事實。每個實質主張使用 [S1] 格式引用。"
        "若 evidence 不足，明確回答資料不足。使用繁體中文。\n\n"
        f"Question:\n{question}\n\nEvidence:\n{evidence[:14000]}"
    )
    response = requests.post(
        f"{base_url}/api/generate",
        json={
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.1},
        },
        timeout=240,
    )
    response.raise_for_status()
    answer = str(response.json().get("response") or "").strip()
    if not answer:
        raise RuntimeError("Ollama returned an empty answer")
    return answer, model


def ask_local_context(
    question: str,
    *,
    database: Any | None = None,
    cfg: Any | None = None,
    provider: Any | None = None,
    project: str | None = None,
    top_k: int | None = None,
    synthesize: bool = True,
) -> dict[str, Any]:
    cfg = cfg or get_config()
    result = semantic_search(
        question,
        database=database,
        cfg=cfg,
        provider=provider,
        project=project,
        top_k=top_k,
    )
    if not result["sources"]:
        result.update({"status": "insufficient_evidence", "answer": "本機索引中沒有足夠資料可回答。", "answer_model": None})
        return result
    if synthesize:
        answer, model = _generate_local_answer(question, result["sources"], cfg)
        result.update({"status": "answered", "answer": answer, "answer_model": model})
    else:
        result.update({"status": "retrieved", "answer": None, "answer_model": None})
    return result
