"""WebDataSource singleton — built once from environment variables, reused across requests.

See WebDataSource.from_env() for the full list of supported environment variables.
"""
from __future__ import annotations

_source = None


def get_source():
    global _source
    if _source is None:
        from blue_data_web_source import WebDataSource
        _source = WebDataSource.from_env(name="web-api")
    return _source


def reset_source() -> None:
    global _source
    _source = None
