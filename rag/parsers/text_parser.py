import os
from pathlib import Path
from typing import List
from rag.parsers.base import BaseParser, ParsedDocument, ParsedSection


class TextParser(BaseParser):
    ENCODINGS = ["utf-8", "utf-8-sig", "gb18030", "gbk", "big5", "cp950", "latin1"]

    def parse(self, file_path: str) -> ParsedDocument:
        path_obj = Path(file_path)
        ext = path_obj.suffix.lower()
        sections: List[ParsedSection] = []

        content = ""
        used_encoding = "unknown"

        for enc in self.ENCODINGS:
            try:
                with open(file_path, "r", encoding=enc) as f:
                    content = f.read()
                    used_encoding = enc
                    break
            except (UnicodeDecodeError, UnicodeError):
                continue

        if not content and os.path.exists(file_path) and os.path.getsize(file_path) > 0:
            try:
                with open(file_path, "rb") as f:
                    raw = f.read()
                    content = raw.decode("utf-8", errors="ignore")
                    used_encoding = "utf-8-ignore"
            except Exception as e:
                content = f"[Text Read Error: {str(e)}]"

        if content.strip():
            sections.append(ParsedSection(
                content=content.strip(),
                metadata={"encoding": used_encoding, "extension": ext}
            ))

        return ParsedDocument(
            file_path=file_path,
            filename=path_obj.name,
            file_type=ext.replace(".", "") or "txt",
            sections=sections,
            metadata={"encoding": used_encoding}
        )
