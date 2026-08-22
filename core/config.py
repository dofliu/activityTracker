import os
from pathlib import Path
from typing import Any, Dict
import yaml

DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"


class Config:
    _instance = None
    _config_data: Dict[str, Any] = {}

    def __new__(cls, config_path: str | Path | None = None):
        if cls._instance is None:
            cls._instance = super(Config, cls).__new__(cls)
            cls._instance.load(config_path)
        return cls._instance

    def load(self, config_path: str | Path | None = None) -> None:
        path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                self._config_data = yaml.safe_load(f) or {}
        else:
            self._config_data = {}

    def get(self, key_path: str, default: Any = None) -> Any:
        keys = key_path.split(".")
        val = self._config_data
        for k in keys:
            if isinstance(val, dict) and k in val:
                val = val[k]
            else:
                return default
        return val

    @property
    def data(self) -> Dict[str, Any]:
        return self._config_data


def get_config(config_path: str | Path | None = None) -> Config:
    return Config(config_path)
