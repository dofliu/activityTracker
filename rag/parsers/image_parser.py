from pathlib import Path
from typing import List
from rag.parsers.base import BaseParser, ParsedDocument, ParsedSection


class ImageParser(BaseParser):
    def __init__(self):
        self._ocr_engine = None
        self._engine_checked = False

    def _get_ocr(self):
        if not self._engine_checked:
            self._engine_checked = True
            try:
                from rapidocr_onnxruntime import RapidOCR
                self._ocr_engine = RapidOCR()
            except Exception:
                self._ocr_engine = None
        return self._ocr_engine

    def parse(self, file_path: str) -> ParsedDocument:
        path_obj = Path(file_path)
        sections: List[ParsedSection] = []
        ocr_text = ""

        ocr = self._get_ocr()
        if ocr is not None:
            try:
                result, _ = ocr(file_path)
                if result:
                    lines = [line[1] for line in result if len(line) >= 2]
                    ocr_text = "\n".join(lines)
            except Exception as e:
                ocr_text = f"[OCR Extraction Error: {str(e)}]"
        else:
            ocr_text = f"[Image File: {path_obj.name}] (OCR engine rapidocr not loaded)"

        if ocr_text.strip():
            sections.append(ParsedSection(
                content=ocr_text.strip(),
                metadata={"type": "image_ocr"}
            ))

        return ParsedDocument(
            file_path=file_path,
            filename=path_obj.name,
            file_type=path_obj.suffix.lower().replace(".", ""),
            sections=sections
        )
