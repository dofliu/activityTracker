import os
from pathlib import Path
from typing import Set
from core.config import get_config
from core.runtime_paths import resolve_runtime_path


class RAGSettings:
    APP_NAME: str = "DeskRAG"
    APP_VERSION: str = "1.0.0"

    # File extensions to index
    DOCUMENT_EXTS: Set[str] = {
        ".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls",
        ".csv", ".tsv", ".txt", ".md", ".rtf", ".html", ".xml", ".tex"
    }

    CODE_EXTS: Set[str] = {
        ".py", ".js", ".jsx", ".ts", ".tsx", ".vue", ".html", ".css", ".scss",
        ".json", ".yaml", ".yml", ".toml", ".ini", ".sql", ".sh", ".bash",
        ".ps1", ".bat", ".cmd", ".c", ".cpp", ".h", ".hpp", ".cs", ".java",
        ".go", ".rs", ".php", ".rb", ".swift", ".kt", ".dart", ".lua", ".r"
    }

    IMAGE_EXTS: Set[str] = {
        ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"
    }

    # Directories to strictly ignore
    IGNORE_DIRS: Set[str] = {
        # Windows & System
        "$recycle.bin", "system volume information", "windows", "program files",
        "program files (x86)", "appdata", "local settings", "recovery", "msocache",
        # Dev & Package Caches
        "node_modules", ".git", ".github", ".svn", ".hg", ".venv", "venv", "env",
        "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
        "dist", "build", "out", "target", "bin", "obj", ".idea", ".vscode",
        ".next", ".nuxt", ".cache", ".turbo", "coverage", ".nuget", ".cargo"
    }

    # Files / Extensions to strictly ignore
    IGNORE_EXTS: Set[str] = {
        ".exe", ".dll", ".so", ".dylib", ".bin", ".sys", ".iso", ".img",
        ".zip", ".tar", ".gz", ".7z", ".rar", ".bz2", ".xz",
        ".dat", ".tmp", ".temp", ".log", ".msi", ".cab", ".pdb", ".lib",
        ".a", ".o", ".class", ".pyc", ".pyd", ".pyo", ".lock"
    }

    @property
    def CHROMA_DIR(self) -> Path:
        cfg = get_config()
        path_str = str(cfg.get("rag.chroma_dir", "data/chroma"))
        p = resolve_runtime_path(path_str)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def BM25_PATH(self) -> Path:
        cfg = get_config()
        path_str = str(cfg.get("rag.bm25_path", "data/bm25_index.pkl"))
        p = resolve_runtime_path(path_str)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def DEFAULT_CHUNK_SIZE(self) -> int:
        cfg = get_config()
        return int(cfg.get("rag.chunk_size", 800))

    @property
    def DEFAULT_CHUNK_OVERLAP(self) -> int:
        cfg = get_config()
        return int(cfg.get("rag.chunk_overlap", 150))

    @property
    def DEFAULT_TOP_K(self) -> int:
        cfg = get_config()
        return int(cfg.get("rag.default_top_k", 6))

    @property
    def DEFAULT_HYBRID_ALPHA(self) -> float:
        cfg = get_config()
        return float(cfg.get("rag.default_hybrid_alpha", 0.65))

    @property
    def DEFAULT_EMBEDDING_PROVIDER(self) -> str:
        cfg = get_config()
        return str(cfg.get("rag.embedding_provider", "fastembed"))

    @property
    def DEFAULT_FASTEMBED_MODEL(self) -> str:
        cfg = get_config()
        return str(cfg.get("rag.embedding_model", "BAAI/bge-small-zh-v1.5"))

    @property
    def DEFAULT_SYSTEM_PROMPT(self) -> str:
        cfg = get_config()
        return str(cfg.get(
            "rag.system_prompt",
            "你是專業的本地知識庫與工作脈絡 AI 助手。請根據檢索到的文件內容精準回答問題，並在引用內容時標註來源。"
        ))


rag_settings = RAGSettings()
