"""Resolve API keys without exposing or persisting their values.

Process environments are snapshots.  On Windows, a long-running launcher can
therefore miss User/Machine environment variables that were added later.  This
module keeps process variables as the highest-priority source and safely falls
back to the Windows registry when necessary.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import re
from typing import Iterable


_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class SecretResolution:
    value: str = ""
    source: str = "missing"
    env_var: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.value)

    def public_status(self) -> dict[str, str | bool]:
        """Return status metadata only; never serialize the secret value."""
        return {
            "configured": self.configured,
            "source": self.source,
            "env_var": self.env_var,
        }


def _read_windows_registry_env(name: str) -> tuple[str, str]:
    if os.name != "nt":
        return "", "missing"

    try:
        import winreg
    except ImportError:
        return "", "missing"

    locations = (
        (winreg.HKEY_CURRENT_USER, r"Environment", "windows_user"),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
            "windows_machine",
        ),
    )
    for hive, key_path, source in locations:
        try:
            with winreg.OpenKey(hive, key_path) as key:
                value, _ = winreg.QueryValueEx(key, name)
        except OSError:
            continue
        normalized = str(value or "").strip()
        if normalized:
            return normalized, source
    return "", "missing"


def resolve_secret_env(primary_name: str, aliases: Iterable[str] = ()) -> SecretResolution:
    """Resolve a secret by environment-variable name without logging its value."""
    names: list[str] = []
    for candidate in (primary_name, *aliases):
        name = str(candidate or "").strip()
        if _ENV_NAME.fullmatch(name) and name not in names:
            names.append(name)

    for name in names:
        value = str(os.environ.get(name, "") or "").strip()
        if value:
            return SecretResolution(value=value, source="process", env_var=name)

    for name in names:
        value, source = _read_windows_registry_env(name)
        if value:
            return SecretResolution(value=value, source=source, env_var=name)

    return SecretResolution(env_var=names[0] if names else "")
