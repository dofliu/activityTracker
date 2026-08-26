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

    def add_chunks(self, chunks: List[ChunkItem], batch_size: int = 64):
        if not chunks:
            return

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
                metadatas.append(meta)

            embeddings = embedding_service.embed_documents(texts)

            self.collection.upsert(
                ids=ids,
                documents=texts,
                embeddings=embeddings,
                metadatas=metadatas
            )

    def delete_by_file_path(self, file_path: str):
        try:
            self.collection.delete(where={"file_path": file_path})
        except Exception as e:
            logger.warning(f"ChromaDB delete error for {file_path}: {e}")

    def query(self, query_text: str, top_k: int = 10) -> List[Dict[str, Any]]:
        query_vector = embedding_service.embed_query(query_text)
        count = self.collection.count()
        if count == 0:
            return []

        actual_k = min(top_k, count)
        try:
            results = self.collection.query(
                query_embeddings=[query_vector],
                n_results=actual_k,
                include=["documents", "metadatas", "distances"]
            )
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
