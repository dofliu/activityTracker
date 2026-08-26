from pathlib import Path
from typing import List
from rag.parsers.base import BaseParser, ParsedDocument, ParsedSection


class PdfParser(BaseParser):
    def parse(self, file_path: str) -> ParsedDocument:
        path_obj = Path(file_path)
        sections: List[ParsedSection] = []
        total_pages = 0
        metadata = {}

        try:
            import fitz  # PyMuPDF
            doc = fitz.open(file_path)
            total_pages = len(doc)
            metadata = doc.metadata or {}

            for page_idx in range(total_pages):
                page = doc[page_idx]
                text = page.get_text("text")
                if text and text.strip():
                    sections.append(ParsedSection(
                        content=text.strip(),
                        page_number=page_idx + 1,
                        metadata={"page": page_idx + 1}
                    ))
            doc.close()
        except ImportError:
            try:
                import pypdf
                reader = pypdf.PdfReader(file_path)
                total_pages = len(reader.pages)
                for page_idx, page in enumerate(reader.pages):
                    text = page.extract_text()
                    if text and text.strip():
                        sections.append(ParsedSection(
                            content=text.strip(),
                            page_number=page_idx + 1,
                            metadata={"page": page_idx + 1}
                        ))
            except Exception as e:
                sections.append(ParsedSection(
                    content=f"[PDF Parse Error: {str(e)}]",
                    page_number=1
                ))
        except Exception as e:
            sections.append(ParsedSection(
                content=f"[PDF Parse Error: {str(e)}]",
                page_number=1
            ))

        return ParsedDocument(
            file_path=file_path,
            filename=path_obj.name,
            file_type="pdf",
            total_pages=total_pages,
            sections=sections,
            metadata=metadata
        )
