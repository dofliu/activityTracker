from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from pydantic import BaseModel


class CitationSource(BaseModel):
    index: int
    chunk_id: str
    file_path: str
    filename: str
    file_type: str
    page: Optional[int] = None
    slide: Optional[int] = None
    sheet: Optional[str] = None
    title: Optional[str] = None
    content: str
    score: float
    retrieval_type: str
    source_domain: Optional[str] = "document"  # "document" | "activity"
    source_type: Optional[str] = None          # "ai_turn" | "git_commit" | "project_state" | "open_loop" | "file_activity" | "document"
    project_key: Optional[str] = None
    source_ref: Optional[str] = None
    timestamp: Optional[str] = None
    trust_status: Optional[str] = None


class BaseRetriever(ABC):
    name: str
    display_name: str
    description: str

    @abstractmethod
    def retrieve(self, query: str, top_k: int = 6, **kwargs) -> List[CitationSource]:
        pass
