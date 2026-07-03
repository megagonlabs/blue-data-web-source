from __future__ import annotations

import json
import re


def safe_json_parse(text: str | None) -> dict | list | None:
    """Parse JSON from an LLM response, stripping markdown fences if present."""
    if not text:
        return None

    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
    return None


def deep_merge(base: dict, update: dict) -> dict:
    """
    Recursively merge nested dictionaries.

    Values from `update` override values in `base`.
    Nested dictionaries are merged recursively.
    """

    result = dict(base)

    for key, value in update.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value

    return result