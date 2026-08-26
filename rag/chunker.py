import re
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
from rag.parsers.base import ParsedDocument, ParsedSection
from rag.config import rag_settings


class ChunkItem(BaseModel):
    chunk_id: str
    file_path: str
    filename: str
    file_type: str
    content: str
    chunk_index: int
    page_number: Optional[int] = None
    slide_number: Optional[int] = None
    sheet_name: Optional[str] = None
    section_title: Optional[str] = None
    metadata: Dict[str, Any] = {}


class TextChunker:
    def __init__(self, chunk_size: Optional[int] = None, chunk_overlap: Optional[int] = None):
        self.chunk_size = chunk_size or rag_settings.DEFAULT_CHUNK_SIZE
        self.chunk_overlap = chunk_overlap or rag_settings.DEFAULT_CHUNK_OVERLAP
        self.separators = [
            "\n\n", "\n", "。\n", "。\t", "。", "！", "？",
            ".\n", ". ", "；", ";", "，", ",", " ", ""
        ]

    def _split_text(self, text: str) -> List[str]:
        if len(text) <= self.chunk_size:
            return [text] if text.strip() else []

        chunks = []
        current_text = text

        while len(current_text) > 0:
            if len(current_text) <= self.chunk_size:
                chunks.append(current_text)
                break

            candidate = current_text[:self.chunk_size]
            split_idx = -1

            for sep in self.separators:
                if sep == "":
                    split_idx = self.chunk_size
                    break
                idx = candidate.rfind(sep)
                if idx != -1 and idx >= self.chunk_size // 3:
                    split_idx = idx + len(sep)
                    break

            if split_idx == -1:
                split_idx = self.chunk_size

            chunk = current_text[:split_idx].strip()
            if chunk:
                chunks.append(chunk)

            next_start = max(split_idx - self.chunk_overlap, 0)
            if next_start >= len(current_text) or (next_start == 0 and split_idx >= len(current_text)):
                break
            if next_start <= 0 and split_idx < len(current_text):
                next_start = split_idx
            current_text = current_text[next_start:].strip()

        return chunks

    def chunk_document(
        self,
        doc: ParsedDocument,
        chunk_size: Optional[int] = None,
        chunk_overlap: Optional[int] = None
    ) -> List[ChunkItem]:
        c_size = chunk_size or self.chunk_size
        c_overlap = chunk_overlap or self.chunk_overlap
        self.chunk_size = c_size
        self.chunk_overlap = c_overlap

        results: List[ChunkItem] = []
        global_chunk_idx = 0

        for sec_idx, section in enumerate(doc.sections):
            text = section.content.strip()
            if not text:
                continue

            splits = self._split_text(text)
            for split in splits:
                if not split.strip():
                    continue

                meta = {
                    "file_path": doc.file_path,
                    "filename": doc.filename,
                    "file_type": doc.file_type,
                    "chunk_index": global_chunk_idx,
                    **doc.metadata,
                    **section.metadata
                }
                if section.page_number:
                    meta["page"] = section.page_number
                if section.slide_number:
                    meta["slide"] = section.slide_number
                if section.sheet_name:
                    meta["sheet"] = section.sheet_name
                if section.section_title:
                    meta["title"] = section.section_title

                chunk_id = f"{doc.file_path}#chunk_{global_chunk_idx}"
                results.append(ChunkItem(
                    chunk_id=chunk_id,
                    file_path=doc.file_path,
                    filename=doc.filename,
                    file_type=doc.file_type,
                    content=split,
                    chunk_index=global_chunk_idx,
                    page_number=section.page_number,
                    slide_number=section.slide_number,
                    sheet_name=section.sheet_name,
                    section_title=section.section_title,
                    metadata=meta
                ))
                global_chunk_idx += 1

        return results


chunker = TextChunker()
