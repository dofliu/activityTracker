import os
import logging
from typing import List, Dict, Any, Optional
import chromadb
from chromadb.config import Settings as ChromaSettings
from rag.config import rag_settings
from rag.chunker import ChunkItem
from rag.embeddings import embedding_service

logger = logging.getLogger("OmniContext.RAG.VectorStore")


class VectorStore:
    def __init__(self, collection_name: str = "omnicontext_rag"):
        self.collection_name = collection_name
        chroma_dir = str(rag_settings.CHROMA_DIR)
        self.client = chromadb.PersistentClient(
            path=chroma_dir,
            settings=ChromaSettings(anonymized_telemetry=False)
        )
        self.collection = self.client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"}
        )

    def add_chunks(self, chunks: List[ChunkItem], batch_size: int | None = None):
        if not chunks:
            return

        # 較小批次降低本機 embedding 與 Chroma 同時占用的峰值記憶體。
        batch_size = batch_size or rag_settings.INDEX_VECTOR_BATCH_SIZE

        for i in range(0, len(chunks), batch_size):
            batch = chunks[i:i + batch_size]
            ids = [c.chunk_id for c in batch]
            texts = [c.content for c in batch]

            metadatas = []
            for c in batch:
                meta = {
                    "file_path": c.file_path,
                    "filename": c.filename,
                    "file_type": c.file_type,
                    "chunk_index": c.chunk_index
                }
                if c.page_number:
                    meta["page"] = c.page_number
                if c.slide_number:
                    meta["slide"] = c.slide_number
                if c.sheet_name:
                    meta["sheet"] = c.sheet_name
                if c.section_title:
                    meta["title"] = c.section_title
                if c.metadata:
                    for k, v in c.metadata.items():
                        if v is not None and isinstance(v, (str, int, float, bool)):
                            meta[k] = v
                metadatas.append(meta)

            embeddings = embedding_service.embed_documents(texts)

            self.collection.upsert(
                ids=ids,
                documents=texts,
                embeddings=embeddings,
                metadatas=metadatas
            )

    def delete_by_file_path(self, file_path: str):
        self.delete_by_file_paths([file_path])

    def delete_by_file_paths(self, file_paths: List[str], batch_size: int = 100):
        """批次刪除，避免每一個檔案都建立一次 Chroma transaction。"""
        if not file_paths:
            return
        for start in range(0, len(file_paths), batch_size):
            batch = file_paths[start:start + batch_size]
            try:
                if len(batch) == 1:
                    self.collection.delete(where={"file_path": batch[0]})
                else:
                    self.collection.delete(where={"file_path": {"$in": batch}})
            except Exception as e:
                logger.warning("ChromaDB batch delete error (%s files): %s", len(batch), e)

    def clear(self):
        """Reset only this application's collection, leaving source files untouched."""
        try:
            self.client.delete_collection(self.collection_name)
        except Exception as e:
            logger.warning("ChromaDB collection reset warning: %s", e)
        self.collection = self.client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"}
        )

    def delete_by_source_domain(self, source_domain: str):
        try:
            self.collection.delete(where={"source_domain": source_domain})
        except Exception as e:
            logger.warning(f"ChromaDB delete error for domain {source_domain}: {e}")

    def query(self, query_text: str, top_k: int = 10, where: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        query_vector = embedding_service.embed_query(query_text)
        count = self.collection.count()
        if count == 0:
            return []

        actual_k = min(top_k, count)
        try:
            kwargs: Dict[str, Any] = {
                "query_embeddings": [query_vector],
                "n_results": actual_k,
                "include": ["documents", "metadatas", "distances"]
            }
            if where:
                kwargs["where"] = where
            results = self.collection.query(**kwargs)
        except Exception as e:
            logger.error(f"ChromaDB query error: {e}")
            return []

        items = []
        if results and results["ids"] and len(results["ids"][0]) > 0:
            for idx in range(len(results["ids"][0])):
                cid = results["ids"][0][idx]
                doc = results["documents"][0][idx] if results["documents"] else ""
                meta = results["metadatas"][0][idx] if results["metadatas"] else {}
                dist = results["distances"][0][idx] if results["distances"] else 1.0
                score = max(0.0, 1.0 - float(dist))  # Cosine distance to similarity

                items.append({
                    "chunk_id": cid,
                    "content": doc,
                    "metadata": meta,
                    "score": score,
                    "retrieval_type": "vector"
                })
        return items

    def count(self) -> int:
        try:
            return self.collection.count()
        except Exception:
            return 0


vector_store = VectorStore()
