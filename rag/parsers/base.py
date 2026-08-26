from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from pydantic import BaseModel


class ParsedSection(BaseModel):
    content: str
    page_number: Optional[int] = None
    slide_number: Optional[int] = None
    sheet_name: Optional[str] = None
    section_title: Optional[str] = None
    metadata: Dict[str, Any] = {}


class ParsedDocument(BaseModel):
    file_path: str
    filename: str
    file_type: str
    total_pages: Optional[int] = None
    sections: List[ParsedSection] = []
    metadata: Dict[str, Any] = {}


class BaseParser(ABC):
    @abstractmethod
    def parse(self, file_path: str) -> ParsedDocument:
        pass
