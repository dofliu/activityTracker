import pytest
from rag.parsers.base import ParsedDocument, ParsedSection
from rag.chunker import TextChunker, ChunkItem


def test_chunker_small_document():
    doc = ParsedDocument(
        file_path="C:/docs/guide.md",
        filename="guide.md",
        file_type="md",
        sections=[
            ParsedSection(content="Short sentence.", section_title="Intro")
        ]
    )
    chunker = TextChunker(chunk_size=100, chunk_overlap=20)
    chunks = chunker.chunk_document(doc)
    assert len(chunks) == 1
    assert chunks[0].content == "Short sentence."
    assert chunks[0].filename == "guide.md"
    assert chunks[0].section_title == "Intro"


def test_chunker_long_sliding_window():
    long_text = "This is a long sentence testing chunking. " * 30
    doc = ParsedDocument(
        file_path="C:/docs/long.txt",
        filename="long.txt",
        file_type="txt",
        sections=[
            ParsedSection(content=long_text, page_number=2)
        ]
    )
    chunker = TextChunker(chunk_size=200, chunk_overlap=50)
    chunks = chunker.chunk_document(doc)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c.content) <= 250
        assert c.page_number == 2
