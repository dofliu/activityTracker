from pathlib import Path
from typing import Optional
from rag.parsers.base import BaseParser, ParsedDocument
from rag.parsers.pdf_parser import PdfParser
from rag.parsers.office_parser import DocxParser, PptxParser, ExcelParser
from rag.parsers.text_parser import TextParser
from rag.parsers.image_parser import ImageParser


class ParserHub:
    def __init__(self):
        self.pdf_parser = PdfParser()
        self.docx_parser = DocxParser()
        self.pptx_parser = PptxParser()
        self.excel_parser = ExcelParser()
        self.text_parser = TextParser()
        self.image_parser = ImageParser()

    def get_parser(self, file_path: str) -> Optional[BaseParser]:
        ext = Path(file_path).suffix.lower()
        if ext == ".pdf":
            return self.pdf_parser
        elif ext in [".docx", ".doc"]:
            return self.docx_parser
        elif ext in [".pptx", ".ppt"]:
            return self.pptx_parser
        elif ext in [".xlsx", ".xls"]:
            return self.excel_parser
        elif ext in [".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"]:
            return self.image_parser
        else:
            return self.text_parser

    def parse_file(self, file_path: str) -> ParsedDocument:
        parser = self.get_parser(file_path)
        if parser:
            return parser.parse(file_path)
        return self.text_parser.parse(file_path)


parser_hub = ParserHub()
