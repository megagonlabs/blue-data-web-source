from __future__ import annotations

import re
from typing import Any


class SemanticTypeInferencer:
    """Deterministic semantic type inferencer.

    Uses Python value types first, then falls back to string-pattern
    detection when all values are strings.  The full type taxonomy
    matches the one in ``MetaData.VALUE_SEMANTIC_TYPES``.
    """

    # --- pattern-based detectors (strings only) ---
    _UUID_RE    = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
    _EMAIL_RE   = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    _URL_RE     = re.compile(r"^https?://\S+$", re.I)
    _PHONE_RE   = re.compile(r"^[\+\d][\d\s\-\(\)\.]{6,}$")
    _DATE_RE    = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    _DATETIME_RE= re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$", re.I)
    _ISO2_RE    = re.compile(r"^[A-Z]{2}$")
    _ISO3_RE    = re.compile(r"^[A-Z]{3}$")
    _ZIP_RE     = re.compile(r"^\d{5}(-\d{4})?$")
    _HASH_RE    = re.compile(r"^[0-9a-f]{32,}$", re.I)
    _ID_NUM_RE  = re.compile(r"^\d{1,20}$")

    def infer(self, values: list[Any]) -> dict:
        non_null = [v for v in values if v is not None]

        if not non_null:
            return {
                "semantic_type": "UNKNOWN",
                "confidence": 0.0,
                "rationale": "No non-null values available.",
                "examples": [],
            }

        # --- boolean ---
        if all(isinstance(v, bool) for v in non_null):
            return self._result("BOOLEAN", 0.95, "All values are Python bool.", non_null)

        # --- integer (not bool) ---
        if all(isinstance(v, int) and not isinstance(v, bool) for v in non_null):
            return self._result("INTEGER", 0.9, "All values are Python int.", non_null)

        # --- float / mixed numeric ---
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in non_null):
            return self._result("FLOAT", 0.85, "All values are Python float or int.", non_null)

        # --- strings: pattern-based detection ---
        if all(isinstance(v, str) for v in non_null):
            return self._infer_string_type(non_null)

        return self._result("UNKNOWN", 0.3, "Mixed Python value types.", non_null)

    # ------------------------------------------------------------------

    def _infer_string_type(self, values: list[str]) -> dict:
        n = len(values)

        def match_rate(pattern) -> float:
            return sum(1 for v in values if pattern.match(v.strip())) / n

        def all_match(pattern) -> bool:
            return all(pattern.match(v.strip()) for v in values)

        # datetime before date (more specific)
        if all_match(self._DATETIME_RE):
            return self._result("DATETIME", 0.95, "All values match ISO datetime pattern.", values)

        if all_match(self._DATE_RE):
            return self._result("DATE", 0.95, "All values match ISO date pattern.", values)

        if all_match(self._UUID_RE):
            return self._result("UUID", 0.98, "All values match UUID format.", values)

        if all_match(self._EMAIL_RE):
            return self._result("EMAIL", 0.97, "All values match email pattern.", values)

        if all_match(self._URL_RE):
            return self._result("URL", 0.97, "All values match URL pattern.", values)

        if all_match(self._HASH_RE):
            return self._result("HASH", 0.9, "All values match hexadecimal hash pattern.", values)

        if match_rate(self._PHONE_RE) > 0.8:
            return self._result("PHONE_NUMBER", 0.85, "Most values match phone number pattern.", values)

        if all_match(self._ZIP_RE):
            return self._result("POSTAL_CODE", 0.9, "All values match US ZIP code format.", values)

        # ISO country codes — only if short and all-caps
        if all_match(self._ISO2_RE) and n >= 3:
            return self._result("COUNTRY_CODE_ISO2", 0.85, "All values are 2-letter uppercase codes.", values)

        if all_match(self._ISO3_RE) and n >= 3:
            return self._result("COUNTRY_CODE_ISO3", 0.85, "All values are 3-letter uppercase codes.", values)

        # numeric strings (IDs stored as strings)
        if all_match(self._ID_NUM_RE):
            return self._result("ID_NUMERIC", 0.8, "All values are numeric strings.", values)

        # cardinality heuristic: low distinct → categorical
        distinct = len(set(v.strip().lower() for v in values))
        if distinct <= max(5, n * 0.1):
            return self._result("ENUM_CATEGORY", 0.75, f"Low cardinality ({distinct} distinct / {n} total).", values)

        # long free-text
        avg_len = sum(len(v) for v in values) / n
        if avg_len > 50:
            return self._result("FREE_TEXT", 0.75, f"Long average length ({avg_len:.0f} chars) suggests free text.", values)

        return self._result("STRING", 0.7, "Generic string values.", values)

    @staticmethod
    def _result(semantic_type: str, confidence: float, rationale: str, examples: list) -> dict:
        return {
            "semantic_type": semantic_type,
            "confidence": confidence,
            "rationale": rationale,
            "examples": examples[:5],
        }
