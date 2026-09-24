"""Profile management — named configuration presets for blue-web.

Profiles are stored in ~/.blue-web/profiles.json.  Each profile is a dict
of setting keys mapped to values.  Secret keys (API keys) are intentionally
excluded — keep those in environment variables or a secrets manager.

Precedence (highest to lowest):
    1. Environment variable already set in the shell
    2. Profile value (active profile only)
    3. blue-data-web-source built-in default
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

# Overridden in tests via monkeypatch
_PROFILES_FILE: Path = Path.home() / ".blue-web" / "profiles.json"

# Internal key that stores the name of the default profile
_DEFAULT_KEY = "_default"

# Profile field → env var name.  Secrets are excluded deliberately.
ENV_MAP: dict[str, str] = {
    "embedding":       "BLUE_WEB_EMBEDDING",
    "redis_host":      "BLUE_WEB_REDIS_HOST",
    "redis_port":      "BLUE_WEB_REDIS_PORT",
    "collection":      "BLUE_WEB_COLLECTION",
    "chunk_size":      "BLUE_WEB_CHUNK_SIZE",
    "overlap":         "BLUE_WEB_OVERLAP",
    "timeout":         "BLUE_WEB_TIMEOUT",
    "max_workers":     "BLUE_WEB_MAX_WORKERS",
    "enricher":        "BLUE_WEB_ENRICHER",
    "llm_model":       "BLUE_WEB_LLM_MODEL",
    "embedding_model": "OPENAI_EMBEDDING_MODEL",
}


# ── Storage ───────────────────────────────────────────────────────────────────

def _load() -> dict:
    if not _PROFILES_FILE.exists():
        return {}
    with open(_PROFILES_FILE) as fh:
        return json.load(fh)


def _save(data: dict) -> None:
    _PROFILES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_PROFILES_FILE, "w") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")


# ── Public CRUD ───────────────────────────────────────────────────────────────

def list_profiles() -> list[str]:
    """Return profile names in sorted order (the _default marker is excluded)."""
    return sorted(k for k in _load() if k != _DEFAULT_KEY)


def get_profile(name: str) -> Optional[dict]:
    """Return the profile dict, or None if it does not exist."""
    return _load().get(name)


def upsert_profile(name: str, settings: dict) -> None:
    """Create or update a profile, merging new keys into any existing values."""
    data = _load()
    merged = {**data.get(name, {}), **settings}
    data[name] = merged
    _save(data)


def remove_profile(name: str) -> bool:
    """Delete a profile.  Returns True if it existed, False otherwise."""
    data = _load()
    if name not in data:
        return False
    del data[name]
    if data.get(_DEFAULT_KEY) == name:
        del data[_DEFAULT_KEY]
    _save(data)
    return True


def set_default(name: str) -> None:
    """Record which profile is active by default."""
    data = _load()
    data[_DEFAULT_KEY] = name
    _save(data)


def default_profile_name() -> Optional[str]:
    """Return the stored default profile name (ignores BLUE_DATA_PROFILE env var)."""
    return _load().get(_DEFAULT_KEY)


def active_profile_name() -> Optional[str]:
    """BLUE_DATA_PROFILE env var > stored default > None."""
    return os.getenv("BLUE_DATA_PROFILE") or default_profile_name()


# ── Env injection ─────────────────────────────────────────────────────────────

def apply_profile(name: Optional[str]) -> None:
    """Inject profile values into os.environ as defaults.

    Only sets a variable when it is not already present in the environment,
    so explicit shell exports always win.
    """
    if not name:
        return
    profile = get_profile(name)
    if profile is None:
        return
    for field, env_var in ENV_MAP.items():
        value = profile.get(field)
        if value is not None and env_var not in os.environ:
            os.environ[env_var] = str(value)
