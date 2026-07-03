"""Metadata enrichment pipeline.

Ported from ``blue.data.metadata.MetaData``.  All LLM-based enrichments require
an ``llm_fn`` callable to be injected at construction time.  Without it every
LLM-dependent method silently returns ``None`` so deterministic enrichments
(process semantics, semantic links, value-axis inference, etc.) still run.

Property taxonomy stored on attribute records after enrichment:

    value_semantics      — VSI: rich semantic type + flags
    semantic_discovery   — SDI: open-world concept discovery
    domain_concept       — DCM: taxonomy concept mapping
    interpretive_sem.    — IVS: ordinal/severity/tier ordering
    value_axis           — magnitude / ordinal axis
    quantitative_vst     — quantitative role (MEASUREMENT / COUNT / …)
    semantic_role        — IDENTIFIER / EVENT_TIME / SEGMENTATION_DRIVER / …
    process_vst_role     — PROCESS_KEY / EVENT_TIME / EVENT_MEASURE / STATE_VAR

Property taxonomy stored on entity records:

    semantic_links       — validated inter-attribute relationships
    process_semantics    — EVENT row semantics
    process_patterns     — temporal cadence
    process_trends       — trend direction per measure
    conditional_value_distribution — P(value | group)
"""
from __future__ import annotations

import json
import logging
import math
import os
from typing import Any, Callable

import numpy as np

from blue_data.registry.enrichment.prompts import AGGREGATION_PROMPT
from blue_data.core.utils.utils import safe_json_parse


class MetaData:
    """Rich metadata enrichment pipeline (LLM-augmented, deterministic fallback)."""

    # ------------------------------------------------------------------
    # Type taxonomies
    # ------------------------------------------------------------------

    VALUE_SEMANTIC_TYPES = [
        "UNKNOWN",
        # structural
        "BOOLEAN",
        "INTEGER",
        "FLOAT",
        "NUMERIC_GENERAL",
        "STRING",
        "FREE_TEXT",
        # temporal
        "DATE",
        "DATETIME",
        "TIME",
        "YEAR",
        "MONTH",
        "DAY_OF_WEEK",
        "DURATION",
        # location / codes
        "US_STATE_CODE",
        "US_STATE_NAME",
        "COUNTRY_CODE_ISO2",
        "COUNTRY_CODE_ISO3",
        "CITY_NAME",
        "POSTAL_CODE",
        "ADDRESS_FREEFORM",
        # financial / numeric semantics
        "CURRENCY_AMOUNT",
        "PERCENTAGE",
        "RATIO",
        # identity / ids
        "ID_NUMERIC",
        "ID_STRING",
        "UUID",
        "HASH",
        # contact / web
        "EMAIL",
        "PHONE_NUMBER",
        "URL",
        # categories & labels
        "ENUM_CATEGORY",
        "TEXT_CATEGORY",
        "TAG",
        "LABEL",
        # human / skills / terms
        "PERSON_NAME",
        "ORG_NAME",
        "JOB_TITLE",
        "SKILL_TERM",
        "CONTROLLED_TERM",
        # demographic
        "AGE",
        "AGE_GROUP",
        "DEMOGRAPHIC_TERM",
    ]

    SEMANTIC_ROLES = [
        "IDENTIFIER",
        "EVENT_TIME",
        "EVIDENCE",
        "SEGMENTATION_DRIVER",
        "DESCRIPTIVE",
    ]

    PROCESS_VST_ROLES = [
        "PROCESS_KEY",
        "EVENT_TIME",
        "EVENT_MEASURE",
        "STATE_VARIABLE",
    ]

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(
        self,
        llm_fn: Callable[[str], str] | None = None,
        taxonomy: list[str] | None = None,
        taxonomy_path: str | None = None,
        properties: dict | None = None,
    ):
        self.logger = logging.getLogger(self.__class__.__name__)
        self._llm_fn = llm_fn

        self._taxonomy: list[str] = taxonomy or self._load_taxonomy(taxonomy_path)

        # feature flags — all on by default; caller can disable selectively
        self.properties: dict[str, Any] = {
            "enable_entity_description_generation": True,
            "enable_attribute_description_generation": True,
            "enable_database_description_generation": True,
            "enable_collection_description_generation": True,
            "enable_value_semantics_inference": True,
            "enable_semantic_discovery_inference": True,
            "enable_domain_concept_mapping": True,
            "enable_value_axis_inference": True,
            "enable_semantic_links_inference": True,
            "enable_interpretive_semantics": True,
            "enable_process_semantics_inference": True,
            "enable_quantitative_semantics_inference": True,
        }
        if properties:
            self.properties.update(properties)

        # mutable context shared within a single entity enrichment pass
        self.current_entity_row_samples: list[dict] = []
        self.current_entity_attributes: list[dict] = []

    # ------------------------------------------------------------------
    # LLM helpers
    # ------------------------------------------------------------------

    def _call_llm(self, prompt: str) -> str | None:
        if self._llm_fn is None:
            return None
        try:
            return self._llm_fn(prompt)
        except Exception as e:
            self.logger.warning(f"LLM call failed: {e}")
            return None

    # ------------------------------------------------------------------
    # Taxonomy
    # ------------------------------------------------------------------

    def _load_taxonomy(self, path: str | None) -> list[str]:
        if not path or not os.path.exists(path):
            return []
        try:
            with open(path) as f:
                data = json.load(f)
                return data.get("concepts", [])
        except Exception as e:
            self.logger.error(f"Failed loading taxonomy: {e}")
            return []

    # ------------------------------------------------------------------
    # Math helpers
    # ------------------------------------------------------------------

    @staticmethod
    def mean_safe(vals: list) -> float | None:
        return sum(vals) / len(vals) if vals else None

    @staticmethod
    def safe_pearson(x: list, y: list) -> float:
        if len(x) < 5 or len(y) < 5:
            return 0.0
        mx, my = sum(x) / len(x), sum(y) / len(y)
        num = sum((a - mx) * (b - my) for a, b in zip(x, y))
        denx = math.sqrt(sum((a - mx) ** 2 for a in x))
        deny = math.sqrt(sum((b - my) ** 2 for b in y))
        if denx == 0 or deny == 0:
            return 0.0
        return num / (denx * deny)

    @staticmethod
    def get_numeric_samples(attr: dict) -> list[float]:
        stats = attr.get("properties", {}).get("stats", {})
        nums = []
        for v in stats.get("sample_values", []):
            try:
                nums.append(float(v))
            except Exception:
                pass
        return nums

    # ------------------------------------------------------------------
    # Signal helpers
    # ------------------------------------------------------------------

    def has_row_cooccurrence(self, a: dict, b: dict, row_samples: list, min_rows: int = 5) -> bool:
        a_name, b_name = a["name"], b["name"]
        count = 0
        for row in row_samples:
            if row.get(a_name) is not None and row.get(b_name) is not None:
                count += 1
                if count >= min_rows:
                    return True
        return False

    def functional_dependency_signal(self, a: dict, b: dict) -> bool:
        a_vals = self.get_numeric_samples(a)
        b_vals = self.get_numeric_samples(b)
        if len(a_vals) < 5 or len(b_vals) < 5:
            return False
        return abs(self.safe_pearson(a_vals, b_vals)) > 0.7

    def temporal_alignment_signal(self, a: dict, b: dict) -> bool:
        temporal = {"DATE", "DATETIME", "TIME", "DURATION"}
        a_type = a.get("properties", {}).get("value_semantics", {}).get("semantic_type")
        b_type = b.get("properties", {}).get("value_semantics", {}).get("semantic_type")
        return a_type in temporal and b_type not in temporal

    def grouped_distribution_signal(self, group_attr: dict, value_attr: dict) -> bool:
        g_vals = group_attr.get("properties", {}).get("stats", {}).get("sample_values", [])
        v_vals = value_attr.get("properties", {}).get("stats", {}).get("sample_values", [])
        if not g_vals or not v_vals:
            return False
        try:
            groups: dict[Any, list] = {}
            for g, v in zip(g_vals, v_vals):
                try:
                    v = float(v)
                except Exception:
                    continue
                groups.setdefault(g, []).append(v)

            if len(groups) < 2:
                return False
            if sum(1 for vals in groups.values() if len(vals) >= 2) < 2:
                return False

            means = [sum(vals) / len(vals) for vals in groups.values()]
            max_mean = max(abs(m) for m in means) if means else 0.0
            if max_mean == 0:
                return False

            spread = max(means) - min(means)
            avg_mean = sum(abs(m) for m in means) / len(means)
            relative_spread = spread / avg_mean if avg_mean > 0 else 0.0
            return spread > 0.12 * max_mean or relative_spread > 0.18
        except Exception:
            return False

    def grouped_distribution_signal_row_aligned(
        self, group_attr: dict, value_attr: dict, row_samples: list
    ) -> bool:
        g_name, v_name = group_attr["name"], value_attr["name"]
        g_vals, v_vals = [], []
        for row in row_samples:
            g = row.get(g_name)
            v = row.get(v_name)
            if g is None or v is None:
                continue
            try:
                v_vals.append(float(v))
                g_vals.append(g)
            except Exception:
                pass
        if len(g_vals) < 5:
            return False
        return self.grouped_distribution_signal(
            {"properties": {"stats": {"sample_values": g_vals}}},
            {"properties": {"stats": {"sample_values": v_vals}}},
        )

    def is_sparse_count_axis(self, attr: dict, values: list) -> bool:
        if not values:
            return False
        vsi = attr.get("properties", {}).get("value_semantics", {})
        if vsi.get("semantic_type") not in {"INTEGER", "NUMERIC_GENERAL"}:
            return False
        name = attr.get("name", "").lower()
        if name in {"latitude", "longitude", "lat", "lon", "lng"}:
            return False
        if not vsi.get("numeric", {}).get("is_discrete", True):
            return False
        n = len(values)
        zero_frac = sum(v == 0 for v in values) / n
        max_val = max(values)
        distinct = len(set(values))
        if zero_frac >= 0.7 and distinct <= 10:
            return True
        if zero_frac >= 0.4 and max_val >= 5:
            return True
        return False

    def infer_tail_threshold(self, values: list) -> int | None:
        values = sorted(values)
        n = len(values)
        non_zero = [v for v in values if v > 0]
        if not non_zero:
            return None
        if max(non_zero) <= 3:
            return 1
        for t in [5, 10, 15, 20]:
            if sum(v >= t for v in values) / n < 0.01:
                return t
        return max(values)

    # ------------------------------------------------------------------
    # Context builder
    # ------------------------------------------------------------------

    def build_attribute_context(self, attributes: list, target_attr_name: str) -> dict:
        context: dict = {
            "sibling_attributes": [],
            "numeric_distributions": {},
            "temporal_hints": [],
            "co_occurrence_hints": [],
            "relative_behavior": [],
        }
        for attr in attributes:
            name = attr.get("name")
            if name == target_attr_name:
                continue
            props = attr.get("properties", {}) or {}
            stats = props.get("stats", {}) or {}
            sem = props.get("value_semantics", {}) or {}
            samples = stats.get("sample_values", [])[:5]

            context["sibling_attributes"].append(name)

            min_val, max_val = stats.get("min"), stats.get("max")
            is_numeric = isinstance(min_val, (int, float)) or isinstance(max_val, (int, float))
            if is_numeric:
                context["numeric_distributions"][name] = {
                    "min": min_val,
                    "max": max_val,
                    "distinct": stats.get("distinct_count"),
                }
                if not sem.get("is_identifier") and isinstance(max_val, (int, float)):
                    context["relative_behavior"].append({
                        "attribute": name,
                        "scale_hint": (
                            "small_range" if max_val < 100
                            else "medium_range" if max_val < 10000
                            else "large_range"
                        ),
                    })

            if sem.get("semantic_type") in ("DATE", "DATETIME", "DURATION"):
                context["temporal_hints"].append(name)

            if samples:
                context["co_occurrence_hints"].append({"attribute": name, "sample_values": samples})

        if len(context["relative_behavior"]) >= 2:
            context["relative_behavior_summary"] = {
                "comparison": [(rb["attribute"], rb["scale_hint"]) for rb in context["relative_behavior"]]
            }
        return context

    # ------------------------------------------------------------------
    # Prompt builders
    # ------------------------------------------------------------------

    def build_entity_description_prompt(self, entity_obj: dict, attributes: list) -> str:
        name = entity_obj.get("name", "Unknown")
        scope = entity_obj.get("scope", "Unknown")
        etype = entity_obj.get("type", "Unknown")
        attr_lines = []
        for attr in attributes:
            props = attr.get("properties", {})
            attr_type = props.get("info", {}).get("type", "unknown")
            attr_name = attr.get("name")
            samples = props.get("stats", {}).get("sample_values", [])
            attr_lines.append(
                f"- {attr_name} ({attr_type}), samples: {', '.join(map(str, samples[:3]))}"
            )
        return f"""
You are given a database entity definition with its attributes and metadata.
Your task is to generate a structured JSON output with:
1. A concise human-readable description of what this table/entity represents.
2. Concise descriptions of each attribute.

Entity Name: {name}
Scope: {scope}
Type: {etype}

Attributes:
{chr(10).join(attr_lines)}

Output JSON format (do not include extra commentary, only valid JSON):
{{
  "table_description": "string",
  "attributes": {{
    "attr_name": "description of attribute"
  }}
}}
""".strip()

    def build_vsi_prompt(self, entity_name: str, attr_name: str, attr_properties: dict, context: dict | None = None) -> str:
        stats = attr_properties.get("stats", {}) or {}
        sample_values = stats.get("sample_values", [])[:10] or ["<NO SAMPLE VALUES AVAILABLE>"]
        attr_type = attr_properties.get("info", {}).get("type", "unknown")

        schema_json = """{
  "value_semantics": {
    "primary_type": "<ONE_OF_ALLOWED_TYPES>",
    "secondary_types": [],
    "is_categorical": false,
    "is_identifier": false,
    "is_free_text": false,
    "numeric": {
      "is_numeric": false,
      "is_continuous": false,
      "is_discrete": false,
      "min": null,
      "max": null
    },
    "temporal": {
      "is_date": false,
      "is_datetime": false,
      "is_duration": false,
      "granularity": null
    },
    "normalization": {
      "can_normalize": false,
      "normalized_examples": [],
      "normalization_strategy": null
    },
    "confidence": 0.0,
    "examples_used": [],
    "notes": []
  }
}"""

        return f"""
You are performing Value Semantics Inference (VSI).
Infer WHAT THE VALUES *ARE*, not what they represent in the domain.

Rules:
- Use ONLY value patterns + statistics.
- You MAY use other attributes ONLY for structural AND distributional disambiguation.
- Use ONLY allowed semantic types.
- DO NOT invent new types. Stay within allowed types.

ATTRIBUTE CONTEXT
Entity: {entity_name}
Attribute: {attr_name}
Declared Type: {attr_type}

ATTRIBUTE_STATS:
{json.dumps(stats, indent=2)}

SAMPLE_VALUES:
{json.dumps(sample_values, indent=2)}

OTHER_ATTRIBUTES_IN_ENTITY (contextual structural hints only):
{json.dumps(context or {}, indent=2)}

ALLOWED_SEMANTIC_TYPES:
{json.dumps(self.VALUE_SEMANTIC_TYPES, indent=2)}

OUTPUT FORMAT (STRICT JSON ONLY):
{schema_json}

Return ONLY this JSON structure, filled in appropriately.
""".strip()

    def build_sdi_prompt(self, entity_name: str, attr_name: str, attr_properties: dict, context: dict | None = None) -> str:
        stats = attr_properties.get("stats", {}) or {}
        sample_values = stats.get("sample_values", [])[:10] or ["<NO SAMPLE VALUES AVAILABLE>"]
        attr_type = attr_properties.get("info", {}).get("type", "unknown")

        return f"""
You are an advanced VALUE SEMANTICS inference engine.
Infer the intrinsic semantic meaning of values using an OPEN-WORLD model — you may generate new types.

THREE-LAYER MODEL:
LAYER 1 — STRUCTURAL SEMANTICS: shape of the values (STRING, NUMBER, DATE, etc.)
LAYER 2 — LINGUISTIC/ENTITY SEMANTICS: type of entity (person name, job title, etc.)
LAYER 3 — CONCEPTUAL/CATEGORY SEMANTICS: semantic class (demographic group, tier, etc.)

ATTRIBUTE CONTEXT
Entity: {entity_name}
Attribute: {attr_name}
Declared Type: {attr_type}

Sample Values:
{json.dumps(sample_values, indent=2)}

Cross-attribute context (optional evidence):
{json.dumps(context or {}, indent=2)}

OUTPUT FORMAT (STRICT JSON ONLY):
{{
  "semantic_type": "string",
  "subcategory": "string or null",
  "confidence": 0.0,
  "range": {{"min": null, "max": null}},
  "criteria": "string or null",
  "rationale": "short explanation",
  "examples": []
}}

Return ONLY valid JSON.
""".strip()

    def build_domain_concept_prompt(self, entity_name: str, attr_name: str, attr_properties: dict) -> str:
        taxonomy_text = (
            "\n".join(f"- {c}" for c in self._taxonomy) if self._taxonomy else "- CONCEPT.UNKNOWN"
        )
        semantics = attr_properties.get("value_semantics", {})
        semantic_type = semantics.get("semantic_type", "UNKNOWN")
        sdi = attr_properties.get("semantic_discovery", {})
        sample_values = (
            semantics.get("examples", [])
            or attr_properties.get("stats", {}).get("sample_values", [])[:5]
            or ["<NO SAMPLE VALUES AVAILABLE>"]
        )
        return f"""
You are performing domain concept mapping for a data registry.
Map the attribute to a canonical DOMAIN CONCEPT.

Available domain concepts:
{taxonomy_text}

Attribute Context:
- Entity: {entity_name}
- Attribute Name: {attr_name}
- Semantic Type: {semantic_type}

Semantic Discovery Output (SDI):
{json.dumps(sdi, indent=2)}

Sample Values:
{json.dumps(sample_values, indent=2)}

Output JSON ONLY:
{{
  "concept": "CONCEPT.X.Y",
  "confidence": 0.0,
  "rationale": "explain briefly"
}}
""".strip()

    def build_interpretive_semantics_prompt(self, entity_name: str, attr_name: str, attr_properties: dict) -> str:
        stats = attr_properties.get("stats", {})
        samples = stats.get("sample_values", [])[:10]
        sdi = attr_properties.get("semantic_discovery", {})
        vsi = attr_properties.get("value_semantics", {})

        return f"""
You are performing INTERPRETIVE VALUE SEMANTICS (IVS).

Infer WHETHER the categorical values imply an ORDERING, SEVERITY, or TIER
that an intelligent agent might use when reasoning.

SAFETY RULES:
- Do NOT assume meaning unless strongly implied.
- If ordering is ambiguous, return interpretation_type = "NONE".
- This output is OPTIONAL and MUST NOT be treated as ground truth.

ATTRIBUTE CONTEXT
Entity: {entity_name}
Attribute: {attr_name}

Sample Values:
{json.dumps(samples, indent=2)}

Value Semantics (VSI):
{json.dumps(vsi, indent=2)}

Semantic Discovery (SDI):
{json.dumps(sdi, indent=2)}

INTERPRETATION TYPES:
ORDINAL   — ordered categories (A < B < C)
SEVERITY  — worse/better progression
TIER      — product/quality levels
NONE      — no safe interpretation

OUTPUT FORMAT (STRICT JSON):
{{
  "interpretation_type": "ORDINAL | SEVERITY | TIER | NONE",
  "ordering": [],
  "polarity": {{
    "direction": "increasing | decreasing",
    "meaning": "risk | severity | quality | preference | level"
  }},
  "confidence": 0.0,
  "rationale": "short explanation",
  "evidence": {{"examples": [], "source": "labels | SDI | linguistic"}}
}}
""".strip()

    def build_semantic_link_validation_prompt(self, source: dict, target: dict, signal: Any) -> str:
        src_vs = source.get("properties", {}).get("value_semantics", {})
        tgt_vs = target.get("properties", {}).get("value_semantics", {})
        return f"""
You are validating a semantic relationship between two attributes.

Source attribute:
- Name: {source.get('name', 'UNKNOWN')}
- Semantic type: {src_vs.get('semantic_type', 'UNKNOWN')}

Target attribute:
- Name: {target.get('name', 'UNKNOWN')}
- Semantic type: {tgt_vs.get('semantic_type', 'UNKNOWN')}

Observed signal: {signal}

Choose ONE relationship: SEGMENTS / DERIVES / SUPPORTS / ASSOCIATED / UNRELATED

Return JSON ONLY:
{{
  "relationship": "...",
  "confidence": 0.0,
  "rationale": "short explanation"
}}
""".strip()

    def build_collection_description_prompt(self, collection_name: str, entity_descriptions: dict, collection_metadata: Any) -> str:
        children = [f"{n}: {d}" for n, d in entity_descriptions.items() if d] or ["No entity descriptions available"]
        return AGGREGATION_PROMPT.format(
            child_type="entity",
            parent_type="collection",
            child_descriptions="\n".join(children),
            parent_metadata=f"Collection name: {collection_name}\nMetadata: {collection_metadata}",
        )

    def build_database_description_prompt(self, database_name: str, collection_descriptions: dict, database_metadata: Any) -> str:
        children = [f"{n}: {d}" for n, d in collection_descriptions.items() if d] or ["No collection descriptions available"]
        return AGGREGATION_PROMPT.format(
            child_type="collection",
            parent_type="database",
            child_descriptions="\n".join(children),
            parent_metadata=f"Database name: {database_name}\nMetadata: {database_metadata}",
        )

    # ------------------------------------------------------------------
    # LLM-based enrichers
    # ------------------------------------------------------------------

    def enrich_entity(self, entity: dict, attributes: list) -> str | None:
        prompt = self.build_entity_description_prompt(entity, attributes)
        return self._call_llm(prompt)

    def enrich_collection_description(self, collection_name: str, entity_descriptions: dict, collection_metadata: Any) -> str | None:
        return self._call_llm(self.build_collection_description_prompt(collection_name, entity_descriptions, collection_metadata))

    def enrich_database_description(self, database_name: str, collection_descriptions: dict, database_metadata: Any) -> str | None:
        return self._call_llm(self.build_database_description_prompt(database_name, collection_descriptions, database_metadata))

    def infer_attribute_vsi(self, entity_name: str, attr: dict) -> dict | None:
        attr_name = attr.get("name")
        props = attr.get("properties", {})
        context = self.build_attribute_context(self.current_entity_attributes, attr_name)
        prompt = self.build_vsi_prompt(entity_name, attr_name, props, context=context)
        raw = self._call_llm(prompt)
        if raw is None:
            return None
        try:
            parsed = safe_json_parse(raw)
            if not parsed:
                return None
            vs = parsed.get("value_semantics", parsed)
            if "primary_type" in vs:
                vs["semantic_type"] = vs.get("primary_type")
            return vs
        except Exception:
            self.logger.warning(f"Invalid VSI JSON for {entity_name}.{attr_name}")
            return None

    def infer_attribute_sdi(self, entity_name: str, attr: dict) -> dict | None:
        attr_name = attr.get("name")
        props = attr.get("properties", {})
        context = self.build_attribute_context(self.current_entity_attributes, attr_name)
        prompt = self.build_sdi_prompt(entity_name, attr_name, props, context=context)
        raw = self._call_llm(prompt)
        if raw is None:
            return None
        try:
            return safe_json_parse(raw)
        except Exception:
            self.logger.warning(f"Invalid SDI JSON for {entity_name}.{attr_name}")
            return None

    def infer_domain_concept(self, entity_name: str, attr: dict) -> dict | None:
        if "value_semantics" not in attr.get("properties", {}):
            return None
        prompt = self.build_domain_concept_prompt(entity_name, attr.get("name"), attr.get("properties", {}))
        raw = self._call_llm(prompt)
        if raw is None:
            return None
        try:
            return safe_json_parse(raw)
        except Exception:
            self.logger.warning(f"Invalid domain concept JSON for {entity_name}.{attr.get('name')}")
            return None

    def infer_interpretive_semantics(self, entity_name: str, attr: dict) -> dict | None:
        vsi = attr.get("properties", {}).get("value_semantics", {})
        if vsi.get("is_identifier"):
            return None
        if vsi.get("semantic_type") not in ("ENUM_CATEGORY", "TEXT_CATEGORY"):
            return None
        prompt = self.build_interpretive_semantics_prompt(entity_name, attr["name"], attr.get("properties", {}))
        raw = self._call_llm(prompt)
        if raw is None:
            return None
        try:
            parsed = safe_json_parse(raw)
            if not parsed:
                return None
            if parsed.get("interpretation_type") == "NONE":
                return None
            if parsed.get("confidence", 0) < 0.6:
                return None
            return parsed
        except Exception:
            self.logger.warning(f"Invalid IVS JSON for {entity_name}.{attr['name']}")
            return None

    def validate_semantic_link(self, source: dict, target: dict, signal: Any) -> dict | None:
        prompt = self.build_semantic_link_validation_prompt(source, target, signal)
        raw = self._call_llm(prompt)
        if raw is None:
            return None
        return safe_json_parse(raw)

    # ------------------------------------------------------------------
    # Deterministic enrichers
    # ------------------------------------------------------------------

    def infer_semantic_links(self, attributes: list) -> list[dict]:
        links = []
        for i, a in enumerate(attributes):
            for j, b in enumerate(attributes):
                if a["name"] == b["name"]:
                    continue
                if self.grouped_distribution_signal(a, b):
                    links.append({"source": a["name"], "target": b["name"], "signal": "grouped_distribution", "row_support": False})
                # Pearson is symmetric — only add each pair once (lower index → higher)
                if i < j and self.functional_dependency_signal(a, b):
                    links.append({"source": a["name"], "target": b["name"], "signal": {"type": "functional_dependency", "strength": 0.7}})
        return links

    def infer_row_based_links(self, attributes: list, row_samples: list) -> list[dict]:
        links = []
        if not row_samples:
            return links
        temporal = {"DATE", "DATETIME", "TIME", "DURATION"}
        for a in attributes:
            for b in attributes:
                if a["name"] == b["name"]:
                    continue
                if self.grouped_distribution_signal_row_aligned(a, b, row_samples):
                    links.append({"source": a["name"], "target": b["name"], "signal": "grouped_distribution", "row_support": True})
                a_type = a.get("properties", {}).get("value_semantics", {}).get("semantic_type")
                b_type = b.get("properties", {}).get("value_semantics", {}).get("semantic_type")
                if (a_type in temporal and b_type not in temporal and self.has_row_cooccurrence(a, b, row_samples)):
                    links.append({"source": a["name"], "target": b["name"], "signal": {"type": "temporal_alignment", "strength": 0.6, "row_support": True}})
        return links

    def infer_semantic_role(self, target_attr: dict, all_attrs: list, semantic_links: list | None = None) -> dict | None:
        semantic_links = semantic_links or []
        attr_name = target_attr["name"]
        vsi = target_attr.get("properties", {}).get("value_semantics", {})
        primary_type = vsi.get("semantic_type", "UNKNOWN")

        if vsi.get("is_identifier"):
            return {"primary_role": "IDENTIFIER", "confidence": 0.95, "role_arguments": {}, "justification": {"rule": "identifier_flag"}}

        outgoing = [l for l in semantic_links if l["source"] == attr_name]

        segment_links = [
            l for l in outgoing
            if l.get("relationship") == "SEGMENTS"
            and not next((a for a in all_attrs if a["name"] == l["target"] and a.get("properties", {}).get("value_semantics", {}).get("is_identifier")), None)
        ]
        if segment_links:
            return {
                "primary_role": "SEGMENTATION_DRIVER",
                "confidence": min(0.95, sum(l.get("confidence", 0.7) for l in segment_links) / len(segment_links)),
                "role_arguments": {"segments": [l["target"] for l in segment_links]},
                "justification": {"signals": ["SEGMENTS"], "link_count": len(segment_links)},
            }

        supports = [l for l in outgoing if l.get("relationship") == "SUPPORTS"]
        if supports:
            return {
                "primary_role": "EVIDENCE",
                "confidence": min(0.9, sum(l.get("confidence", 0.7) for l in supports) / len(supports)),
                "role_arguments": {"supports": [l["target"] for l in supports]},
                "justification": {"signals": ["SUPPORTS"]},
            }

        if primary_type in ("DATE", "DATETIME"):
            return {"primary_role": "EVENT_TIME", "confidence": 0.55, "role_arguments": {}, "justification": {"type_prior": primary_type}}

        return {"primary_role": "DESCRIPTIVE", "confidence": 0.4, "role_arguments": {}, "justification": {"reason": "no causal or statistical signals detected"}}

    # --- value axis ---

    def is_value_axis_eligible(self, attr: dict) -> bool:
        vsi = attr.get("properties", {}).get("value_semantics", {}) or {}
        if vsi.get("is_identifier"):
            return False
        semantic_type = vsi.get("semantic_type", "UNKNOWN")
        if semantic_type in {"ID_STRING", "ID_NUMERIC", "UUID", "HASH", "CODE", "TAG", "LABEL", "ENUM_CATEGORY", "TEXT_CATEGORY"}:
            return False
        return semantic_type in {"CURRENCY_AMOUNT", "INTEGER", "FLOAT", "NUMERIC_GENERAL", "PERCENTAGE", "RATIO", "DURATION"}

    def is_ivs_promotable_to_value_axis(self, attr: dict) -> bool:
        props = attr.get("properties", {})
        ivs = props.get("interpretive_semantics", {})
        vsi = props.get("value_semantics", {})
        if not ivs:
            return False
        if ivs.get("interpretation_type") not in ("ORDINAL", "TIER", "SEVERITY"):
            return False
        if ivs.get("confidence", 0.0) < 0.7:
            return False
        if vsi.get("is_identifier"):
            return False
        return vsi.get("semantic_type") in ("ENUM_CATEGORY", "TEXT_CATEGORY", "LABEL")

    def infer_numeric_value_axis(self, attr: dict, row_samples: list) -> dict | None:
        name = attr["name"]
        values = []
        for row in row_samples:
            try:
                values.append(float(row.get(name)))
            except Exception:
                pass
        if len(values) < 20:
            return None

        if self.is_sparse_count_axis(attr, values):
            threshold = self.infer_tail_threshold(values) or 1
            return {
                "axis_type": "DISCRETE_COUNT",
                "polarity": {"direction": "increasing", "meaning": "severity"},
                "extreme_regions": {"large": f">= {threshold}"},
                "confidence": 0.85,
                "rationale": "Sparse, zero-inflated discrete counts; extremes inferred via tail-regime semantics",
            }

        p10 = float(np.percentile(values, 10))
        p90 = float(np.percentile(values, 90))
        return {
            "axis_type": "CONTINUOUS",
            "polarity": {"direction": "increasing", "meaning": "magnitude"},
            "extreme_regions": {"small": f"< {round(p10, 2)}", "large": f"> {round(p90, 2)}"},
            "confidence": 0.7,
            "rationale": "Distributional extremes inferred via quantiles",
        }

    def infer_value_axis(self, attributes: list, row_samples: list) -> dict[str, dict]:
        axes: dict[str, dict] = {}
        numeric_types = {"CURRENCY_AMOUNT", "INTEGER", "FLOAT", "NUMERIC_GENERAL", "PERCENTAGE", "RATIO", "DURATION"}

        for attr in attributes:
            if not self.is_value_axis_eligible(attr):
                continue
            if attr.get("properties", {}).get("value_semantics", {}).get("semantic_type") in numeric_types:
                axis = self.infer_numeric_value_axis(attr, row_samples)
                if axis:
                    axes[attr["name"]] = axis

        for attr in attributes:
            name = attr["name"]
            if name in axes or not self.is_ivs_promotable_to_value_axis(attr):
                continue
            ivs = attr.get("properties", {}).get("interpretive_semantics")
            if not ivs:
                continue
            axes[name] = {
                "axis_type": "ORDINAL",
                "ordering": ivs.get("ordering", []),
                "polarity": ivs.get("polarity", {}),
                "extreme_regions": {
                    "low": ivs.get("ordering", [])[:1],
                    "high": ivs.get("ordering", [])[-1:],
                },
                "confidence": ivs.get("confidence", 0.7),
                "rationale": "Promoted from interpretive semantics",
                "source": "interpretive_semantics",
            }
        return axes

    # --- quantitative VST ---

    def infer_quantitative_vst_role(self, attr: dict) -> dict | None:
        props = attr.get("properties", {}) or {}
        vsi = props.get("value_semantics", {}) or {}
        semantic_type = vsi.get("semantic_type")
        name = attr.get("name", "").lower()
        nums = self.get_numeric_samples(attr)
        if not nums:
            return None

        axis = props.get("value_axis")
        if axis:
            if axis.get("axis_type") == "DISCRETE_COUNT":
                return {"role": "COUNT", "confidence": 0.9, "rationale": "Sparse discrete count via value axis"}
            if axis.get("axis_type") == "CONTINUOUS":
                return {"role": "MEASUREMENT", "confidence": 0.85, "rationale": "Continuous magnitude via value axis"}

        if semantic_type in {"CURRENCY_AMOUNT", "DURATION"}:
            return {"role": "MEASUREMENT", "confidence": 0.9, "rationale": f"{semantic_type} represents measurable quantity"}
        if semantic_type in {"PERCENTAGE", "RATIO"}:
            return {"role": "COMPUTATIONAL", "confidence": 0.85, "rationale": "Derived or normalized metric"}

        if semantic_type == "INTEGER":
            if self.is_sparse_count_axis(attr, nums):
                return {"role": "COUNT", "confidence": 0.85, "rationale": "Sparse / zero-inflated discrete distribution"}
            min_val, max_val = min(nums), max(nums)
            if 0 <= min_val and max_val <= 150:
                return {"role": "MEASUREMENT", "subtype": "bounded_quantity", "confidence": 0.75, "rationale": "Bounded integer range"}
            return {"role": "MAGNITUDE", "confidence": 0.7, "rationale": "General integer magnitude"}

        if semantic_type in {"FLOAT", "NUMERIC_GENERAL"}:
            numeric = vsi.get("numeric", {}) or {}
            if numeric.get("is_discrete", False) and self.is_sparse_count_axis(attr, nums):
                return {"role": "COUNT", "confidence": 0.75, "rationale": "Sparse discrete float"}
            min_val, max_val = min(nums), max(nums)
            if 0 <= min_val and max_val <= 1:
                return {"role": "COMPUTATIONAL", "subtype": "normalized", "confidence": 0.75, "rationale": "Normalized [0,1] range"}
            return {"role": "MAGNITUDE", "confidence": 0.65, "rationale": "Continuous numeric magnitude"}

        if any(k in name for k in ["count", "num_", "total_", "cnt"]):
            return {"role": "COUNT", "confidence": 0.7, "rationale": "Name suggests count semantics"}
        if any(k in name for k in ["rate", "ratio", "percent", "pct"]):
            return {"role": "COMPUTATIONAL", "confidence": 0.7, "rationale": "Name suggests derived metric"}
        if any(k in name for k in ["price", "amount", "cost", "salary", "revenue"]):
            return {"role": "MEASUREMENT", "subtype": "monetary", "confidence": 0.75, "rationale": "Name suggests monetary measurement"}

        return None

    # --- process semantics ---

    def identifier_repetition_signal(self, key: str, row_samples: list) -> float:
        counts: dict = {}
        for row in row_samples:
            v = row.get(key)
            if v is None:
                continue
            counts[v] = counts.get(v, 0) + 1
        repeated = sum(1 for c in counts.values() if c >= 2)
        if repeated >= 5:
            return 0.6
        if repeated >= 2:
            return 0.4
        return 0.0

    def temporal_density_signal(self, event_time_attr: str, row_samples: list) -> float:
        times = []
        for row in row_samples:
            v = row.get(event_time_attr)
            if v is None:
                continue
            try:
                times.append(np.datetime64(v))
            except Exception:
                pass
        if len(times) < 10:
            return 0.0
        times = sorted(times)
        deltas = []
        for i in range(1, len(times)):
            delta = float((times[i] - times[i - 1]) / np.timedelta64(1, "D"))
            if delta > 0:
                deltas.append(delta)
        if not deltas:
            return 0.0
        return 0.4 if float(np.mean(deltas)) < 30 else 0.2

    def event_measure_signal(self, attributes: list) -> float:
        count = sum(
            1 for attr in attributes
            if attr.get("properties", {}).get("value_semantics", {}).get("semantic_type")
            in {"INTEGER", "FLOAT", "NUMERIC_GENERAL", "CURRENCY_AMOUNT", "PERCENTAGE", "RATIO"}
        )
        return 0.3 if count >= 2 else (0.15 if count == 1 else 0.0)

    def infer_process_semantics(self, entity_name: str, attributes: list, row_samples: list) -> dict | None:
        if not row_samples or len(row_samples) < 10:
            return None

        entity_keys, event_time_attr = [], None
        _time_keywords = {"date", "_at", "time", "timestamp", "when", "created", "updated"}
        for attr in attributes:
            name = attr["name"]
            name_lower = name.lower()
            vsi = attr.get("properties", {}).get("value_semantics", {})
            role = attr.get("properties", {}).get("semantic_role", {})
            if vsi.get("is_identifier"):
                entity_keys.append(name)
            if not entity_keys and name_lower.endswith("_id"):
                entity_keys.append(name)
            if not event_time_attr:
                # VSI-based (set by base-layer SemanticTypeInferencer)
                if role.get("primary_role") == "EVENT_TIME" or vsi.get("semantic_type") in {"DATE", "DATETIME", "TIME"}:
                    event_time_attr = name
                # name-based fallback when VSI not yet available
                elif any(kw in name_lower for kw in _time_keywords):
                    event_time_attr = name

        if not event_time_attr:
            return None

        key = entity_keys[0] if entity_keys else None
        repetition_score = self.identifier_repetition_signal(key, row_samples) if key else 0.0
        temporal_score = self.temporal_density_signal(event_time_attr, row_samples)
        measure_score = self.event_measure_signal(attributes)
        process_score = repetition_score + temporal_score + measure_score

        if process_score < 0.5:
            return None

        event_mode = "SEQUENCE_EVENT" if repetition_score >= 0.4 else "ATOMIC_EVENT"
        return {
            "row_semantics": "EVENT",
            "event_mode": event_mode,
            "entity_keys": entity_keys,
            "event_time": event_time_attr,
            "signals": {
                "identifier_repetition": repetition_score,
                "temporal_density": temporal_score,
                "event_measure_presence": measure_score,
            },
            "constraints": {
                "requires_aggregation": True,
                "requires_temporal_reasoning": True,
                "row_local_substitution_invalid": True,
            },
            "confidence": round(process_score, 2),
            "rationale": "Multiple structural signals indicate rows represent events.",
        }

    def infer_attribute_process_roles(self, attributes: list, process_semantics: dict) -> dict[str, dict]:
        roles: dict[str, dict] = {}
        event_time = process_semantics.get("event_time")
        entity_keys = process_semantics.get("entity_keys", [])
        for attr in attributes:
            name = attr["name"]
            vsi = attr.get("properties", {}).get("value_semantics", {}) or {}
            role = attr.get("properties", {}).get("semantic_role", {}) or {}
            if name == event_time:
                roles[name] = {"process_vst_role": "EVENT_TIME", "confidence": 0.95, "rationale": "Event timestamp."}
                continue
            if name in entity_keys:
                roles[name] = {"process_vst_role": "PROCESS_KEY", "confidence": 0.95, "rationale": "Entity identifier for event sequences."}
                continue
            semantic_type = vsi.get("semantic_type")
            if semantic_type in {"INTEGER", "FLOAT", "NUMERIC_GENERAL"} and role.get("primary_role") == "EVIDENCE":
                roles[name] = {"process_vst_role": "STATE_VARIABLE", "confidence": 0.6, "rationale": "Evolving state."}
            elif semantic_type in {"CURRENCY_AMOUNT", "INTEGER", "FLOAT", "NUMERIC_GENERAL", "PERCENTAGE", "RATIO"}:
                roles[name] = {"process_vst_role": "EVENT_MEASURE", "confidence": 0.7, "rationale": "Numeric measurement at event."}
        return roles

    def infer_process_patterns(self, entity_name: str, attributes: list, row_samples: list, process_semantics: dict) -> dict | None:
        if not process_semantics:
            return None
        event_time = process_semantics.get("event_time")
        entity_keys = process_semantics.get("entity_keys", [])
        if not event_time or not entity_keys:
            return None

        key = entity_keys[0]
        sequences: dict = {}
        for row in row_samples:
            k = row.get(key)
            t = row.get(event_time)
            if k is None or t is None:
                continue
            try:
                sequences.setdefault(k, []).append(np.datetime64(t))
            except Exception:
                pass

        if len(sequences) < 3 or sum(1 for s in sequences.values() if len(s) >= 3) < 3:
            return None

        intervals = []
        for seq in sequences.values():
            if len(seq) < 3:
                continue
            for i in range(1, len(sorted(seq))):
                delta = float((sorted(seq)[i] - sorted(seq)[i - 1]) / np.timedelta64(1, "D"))
                if delta > 0:
                    intervals.append(delta)

        if len(intervals) < 10:
            return None

        mean_i = float(np.mean(intervals))
        std_i = float(np.std(intervals))
        cadence = "irregular"
        if abs(mean_i - 30) < 5:
            cadence = "monthly"
        elif abs(mean_i - 7) < 2:
            cadence = "weekly"
        elif abs(mean_i - 1) < 0.5:
            cadence = "daily"

        return {
            "entity_key": key,
            "event_time": event_time,
            "expected_interval_days": round(mean_i, 2),
            "interval_variance": round(std_i, 2),
            "cadence": cadence,
            "irregularity_threshold": round(std_i * 2, 2),
            "confidence": 0.7,
            "rationale": "Stable temporal cadence detected across event sequences.",
        }

    def infer_temporal_trends(self, attributes: list, row_samples: list, process_semantics: dict) -> dict | None:
        if not process_semantics:
            return None
        event_time = process_semantics.get("event_time")
        if not event_time:
            return None

        measure_types = {"INTEGER", "FLOAT", "NUMERIC_GENERAL", "CURRENCY_AMOUNT", "PERCENTAGE", "RATIO"}
        event_measures = [
            attr["name"] for attr in attributes
            if attr.get("properties", {}).get("value_semantics", {}).get("semantic_type") in measure_types
        ]
        if not event_measures:
            return None

        trends = []
        for measure in event_measures:
            t_vals, v_vals = [], []
            for row in row_samples:
                t, v = row.get(event_time), row.get(measure)
                if t is None or v is None:
                    continue
                try:
                    t_vals.append(int(np.datetime64(t).astype("datetime64[D]").astype(int)))
                    v_vals.append(float(v))
                except Exception:
                    pass
            if len(t_vals) < 10:
                continue
            idx = np.argsort(t_vals)
            t_norm = np.array(t_vals)[idx] - min(t_vals)
            v_sorted = np.array(v_vals)[idx]
            try:
                slope = float(np.polyfit(t_norm, v_sorted, 1)[0])
            except Exception:
                continue
            direction = "stable"
            if slope > 0.01:
                direction = "increasing"
            elif slope < -0.01:
                direction = "decreasing"
            trends.append({"measure": measure, "trend_direction": direction, "slope": round(slope, 4), "confidence": 0.7, "rationale": "Temporal regression over ordered measurements"})

        return {"event_time": event_time, "trends": trends, "confidence": 0.7} if trends else None

    def infer_conditional_distributions(self, group_attr: dict, value_attr: dict, row_samples: list) -> dict | None:
        g_name, v_name = group_attr["name"], value_attr["name"]
        groups: dict = {}
        for row in row_samples:
            g, v = row.get(g_name), row.get(v_name)
            if g is None or v is None:
                continue
            try:
                groups.setdefault(g, []).append(float(v))
            except Exception:
                pass

        profiles = {}
        for g, vals in groups.items():
            if len(vals) < 10:
                continue
            profiles[str(g)] = {
                "count": len(vals),
                "p50": round(float(np.percentile(vals, 50)), 2),
                "p90": round(float(np.percentile(vals, 90)), 2),
                "p99": round(float(np.percentile(vals, 99)), 2),
                "typical_range": [round(float(np.min(vals)), 2), round(float(np.percentile(vals, 90)), 2)],
                "extreme_range": [round(float(np.percentile(vals, 90)), 2), round(float(np.max(vals)), 2)],
            }

        if len(profiles) < 2 or len(profiles) > 10:
            return None

        return {
            "group_attribute": g_name,
            "value_attribute": v_name,
            "profiles": profiles,
            "recommended_stat": "p99",
            "confidence": 0.7,
            "rationale": "Conditional distributions from row-aligned numeric behavior",
        }

    # ------------------------------------------------------------------
    # Top-level collection enrichment methods
    # ------------------------------------------------------------------

    def _write_entity_and_attribute_descriptions(self, data_registry, source, database, collection, entity_name, parsed, rebuild):
        table_desc = parsed.get("table_description", "")
        attribute_descs = parsed.get("attributes", {})

        if self.properties.get("enable_entity_description_generation", True):
            current = data_registry.get_source_database_collection_entity_description(source, database, collection, entity_name)
            if not current or current.strip() == "" or rebuild:
                data_registry.set_source_database_collection_entity_description(source, database, collection, entity_name, table_desc, rebuild=rebuild)

        if self.properties.get("enable_attribute_description_generation", True):
            for attr, desc in attribute_descs.items():
                current = data_registry.get_source_database_collection_entity_attribute_description(source, database, collection, entity_name, attr)
                if not current or current.strip() == "" or rebuild:
                    data_registry.set_source_database_collection_entity_attribute_description(source, database, collection, entity_name, attr, desc, rebuild=rebuild)

        return table_desc

    def enrich_entity_metadata(self, data_registry, source, database, collection, entity, attributes, rebuild):
        entity_name = entity.get("name")
        if not rebuild:
            existing = data_registry.get_source_database_collection_entity_description(source, database, collection, entity_name)
            if existing and existing.strip():
                return existing
        raw = self.enrich_entity(entity, attributes)
        if raw is None:
            return None
        try:
            parsed = safe_json_parse(raw)
            if not parsed:
                return None
        except Exception:
            return None
        return self._write_entity_and_attribute_descriptions(data_registry, source, database, collection, entity_name, parsed, rebuild)

    def enrich_attributes(self, data_registry, source, database, collection, entity_name, attributes, rebuild):
        # VSI + SDI
        for attr in attributes:
            attr_name = attr.get("name")
            if not attr.get("properties", {}).get("stats"):
                continue

            if self.properties.get("enable_value_semantics_inference", True):
                existing = data_registry.get_source_database_collection_entity_attribute_property(source, database, collection, entity_name, attr_name, "value_semantics")
                if not existing or rebuild:
                    vsi = self.infer_attribute_vsi(entity_name, attr)
                    if vsi:
                        data_registry.set_source_database_collection_entity_attribute_property(source, database, collection, entity_name, attr_name, "value_semantics", vsi, rebuild=rebuild)

            if self.properties.get("enable_semantic_discovery_inference", True):
                existing_sdi = data_registry.get_source_database_collection_entity_attribute_property(source, database, collection, entity_name, attr_name, "semantic_discovery")
                if not existing_sdi or rebuild:
                    sdi = self.infer_attribute_sdi(entity_name, attr)
                    if sdi:
                        data_registry.set_source_database_collection_entity_attribute_property(source, database, collection, entity_name, attr_name, "semantic_discovery", sdi, rebuild=rebuild)

        # refresh
        attributes = data_registry.list_entity_attributes(source, database, collection, entity_name)

        # DCM
        if self.properties.get("enable_domain_concept_mapping", True):
            for attr in attributes:
                attr_name = attr.get("name")
                if "value_semantics" not in attr.get("properties", {}):
                    continue
                existing = data_registry.get_source_database_collection_entity_attribute_property(source, database, collection, entity_name, attr_name, "domain_concept")
                if not existing or rebuild:
                    dcm = self.infer_domain_concept(entity_name, attr)
                    if dcm:
                        data_registry.set_source_database_collection_entity_attribute_property(source, database, collection, entity_name, attr_name, "domain_concept", dcm, rebuild=rebuild)

        # IVS
        if self.properties.get("enable_interpretive_semantics", True):
            for attr in attributes:
                ivs = self.infer_interpretive_semantics(entity_name, attr)
                if ivs:
                    data_registry.set_source_database_collection_entity_attribute_property(source, database, collection, entity_name, attr["name"], "interpretive_semantics", ivs, rebuild=rebuild)

        # Quantitative VST
        if self.properties.get("enable_quantitative_semantics_inference", True):
            for attr in attributes:
                qvst = self.infer_quantitative_vst_role(attr)
                if qvst:
                    data_registry.set_source_database_collection_entity_attribute_property(source, database, collection, entity_name, attr["name"], "quantitative_vst", qvst, rebuild=rebuild)

        return attributes

    def enrich_relations(self, data_registry, source, database, collection, entity_name, attributes, row_samples, rebuild):
        # Value Axis
        if self.properties.get("enable_value_axis_inference", True):
            axes = self.infer_value_axis(attributes, row_samples)
            for attr_name, axis in axes.items():
                data_registry.set_source_database_collection_entity_attribute_property(source, database, collection, entity_name, attr_name, "value_axis", axis, rebuild=rebuild)

        # Semantic Links + Roles + CDS
        if self.properties.get("enable_semantic_links_inference", True):
            semantic_links = self.infer_semantic_links(attributes)
            row_links = self.infer_row_based_links(attributes, row_samples)
            for l in row_links:
                l["row_support"] = True
            semantic_links += row_links

            validated_links = []
            for link in semantic_links:
                try:
                    src = next(a for a in attributes if a["name"] == link["source"])
                    tgt = next(a for a in attributes if a["name"] == link["target"])
                except StopIteration:
                    continue

                result = self.validate_semantic_link(src, tgt, link["signal"])
                relationship = result.get("relationship") if result else None

                # fall back to deterministic assignment when LLM is absent
                if relationship is None:
                    signal_type = link["signal"].get("type") if isinstance(link["signal"], dict) else link["signal"]
                    relationship = {
                        "grouped_distribution": "SEGMENTS",
                        "functional_dependency": "DERIVES",
                        "temporal_alignment": "SUPPORTS",
                    }.get(signal_type)

                if relationship and relationship != "UNRELATED":
                    confidence = result.get("confidence", 0.6) if result else 0.6
                    if link.get("row_support"):
                        confidence = max(0.7, confidence)
                    validated_links.append({
                        "source": link["source"],
                        "target": link["target"],
                        "relationship": relationship,
                        "confidence": confidence,
                        "rationale": result.get("rationale") if result else "Structure-backed semantic signal",
                    })

            if validated_links:
                data_registry.set_source_database_collection_entity_property(source, database, collection, entity_name, "semantic_links", validated_links, rebuild=rebuild)

            # Semantic roles
            for attr in attributes:
                role = self.infer_semantic_role(attr, attributes, validated_links)
                if role:
                    data_registry.set_source_database_collection_entity_attribute_property(source, database, collection, entity_name, attr["name"], "semantic_role", role, rebuild=rebuild)

            # Conditional distributions (SEGMENTS links only)
            if validated_links and row_samples:
                for link in validated_links:
                    if link.get("relationship") != "SEGMENTS":
                        continue
                    try:
                        g_attr = next(a for a in attributes if a["name"] == link["source"])
                        v_attr = next(a for a in attributes if a["name"] == link["target"])
                    except StopIteration:
                        continue

                    g_role = g_attr.get("properties", {}).get("semantic_role", {})
                    if g_role.get("primary_role") != "SEGMENTATION_DRIVER":
                        continue

                    v_type = v_attr.get("properties", {}).get("value_semantics", {}).get("semantic_type")
                    if v_type not in {"CURRENCY_AMOUNT", "INTEGER", "FLOAT", "NUMERIC_GENERAL", "PERCENTAGE", "RATIO", "DURATION"}:
                        continue

                    cds = self.infer_conditional_distributions(g_attr, v_attr, row_samples)
                    if cds:
                        existing = data_registry.get_source_database_collection_entity_attribute_property(source, database, collection, entity_name, v_attr["name"], "conditional_value_distribution") or {}
                        existing[g_attr["name"]] = cds
                        data_registry.set_source_database_collection_entity_attribute_property(source, database, collection, entity_name, v_attr["name"], "conditional_value_distribution", existing, rebuild=rebuild)

        return attributes

    def enrich_process_semantics(self, data_registry, source, database, collection, entity_name, attributes, row_samples, rebuild):
        if not self.properties.get("enable_process_semantics_inference", True):
            return
        ps = self.infer_process_semantics(entity_name, attributes, row_samples)
        if not ps:
            return

        data_registry.set_source_database_collection_entity_property(source, database, collection, entity_name, "process_semantics", ps, rebuild=rebuild)

        roles = self.infer_attribute_process_roles(attributes, ps)
        for attr_name, role in roles.items():
            data_registry.set_source_database_collection_entity_attribute_property(source, database, collection, entity_name, attr_name, "process_vst_role", role, rebuild=rebuild)

        patterns = self.infer_process_patterns(entity_name, attributes, row_samples, ps)
        if patterns:
            data_registry.set_source_database_collection_entity_property(source, database, collection, entity_name, "process_patterns", patterns, rebuild=rebuild)

        trends = self.infer_temporal_trends(attributes, row_samples, ps)
        if trends:
            data_registry.set_source_database_collection_entity_property(source, database, collection, entity_name, "process_trends", trends, rebuild=rebuild)

    def enrich_entity_full(self, data_registry, source, database, collection, entity, attributes, rebuild=False):
        """Run the full enrichment pipeline for a single entity."""
        entity_name = entity.get("name")
        entity_stats = entity.get("properties", {}).get("stats", {})
        row_samples = entity_stats.get("row_samples", [])

        self.current_entity_row_samples = row_samples
        self.current_entity_attributes = attributes

        self.enrich_entity_metadata(data_registry, source, database, collection, entity, attributes, rebuild)
        attributes = self.enrich_attributes(data_registry, source, database, collection, entity_name, attributes, rebuild)
        attributes = self.enrich_relations(data_registry, source, database, collection, entity_name, attributes, row_samples, rebuild)
        self.enrich_process_semantics(data_registry, source, database, collection, entity_name, attributes, row_samples, rebuild)

    # ------------------------------------------------------------------
    # Source-level entry points (mirror the original collect_source_* API)
    # ------------------------------------------------------------------

    def collect_source_database_collection_metadata(self, data_registry, source, database, collection, recursive=False, rebuild=False):
        entities = data_registry.list_collection_entities(source, database, collection)
        entity_descriptions = {}

        for entity in entities:
            entity_name = entity.get("name")
            attributes = data_registry.list_entity_attributes(source, database, collection, entity_name)
            entity_stats = entity.get("properties", {}).get("stats", {})
            row_samples = entity_stats.get("row_samples", [])

            self.enrich_entity_full(data_registry, source, database, collection, entity, attributes, rebuild)

            # collect entity description for collection-level aggregation
            desc = data_registry.get_source_database_collection_entity_description(source, database, collection, entity_name)
            entity_descriptions[entity_name] = desc

        if self.properties.get("enable_collection_description_generation", True):
            current = data_registry.get_source_database_collection_description(source, database, collection)
            if not current or current.strip() == "" or rebuild:
                meta = data_registry.get_source_database_collection_property(source, database, collection, "metadata") or {"name": collection, "type": "collection"}
                desc = self.enrich_collection_description(collection, entity_descriptions, meta)
                if desc:
                    data_registry.set_source_database_collection_description(source, database, collection, desc, rebuild=rebuild)

    def collect_source_database_metadata(self, data_registry, source, database, recursive=False, rebuild=False):
        collections = data_registry.list_database_collections(source, database)
        collection_descriptions = {}

        if recursive:
            for coll in collections:
                coll_name = coll.get("name")
                self.collect_source_database_collection_metadata(data_registry, source, database, coll_name, recursive=True, rebuild=rebuild)

        if self.properties.get("enable_database_description_generation", True):
            current = data_registry.get_source_database_description(source, database)
            if not current or current.strip() == "" or rebuild:
                meta = data_registry.get_source_database_property(source, database, "metadata") or {"name": database, "type": "database"}
                for coll in collections:
                    coll_name = coll.get("name")
                    collection_descriptions[coll_name] = coll.get("description")
                desc = self.enrich_database_description(database, collection_descriptions, meta)
                if desc:
                    data_registry.set_source_database_description(source, database, desc, rebuild=rebuild)

    def collect_source_metadata(self, data_registry, source, recursive=False, rebuild=False):
        """Top-level entry point. Call from DataRegistry.enrich_source_metadata()."""
        databases = data_registry.list_source_databases(source)
        for db in databases:
            self.collect_source_database_metadata(data_registry, source, db.get("name"), recursive=recursive, rebuild=rebuild)
