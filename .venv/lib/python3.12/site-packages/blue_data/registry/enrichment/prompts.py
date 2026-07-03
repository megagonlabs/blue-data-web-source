from __future__ import annotations

AGGREGATION_PROMPT = """\
You are a data catalog assistant. Generate a concise description for a {parent_type} based on the \
descriptions of its child {child_type}s.

Child {child_type} descriptions:
{child_descriptions}

{parent_type} metadata:
{parent_metadata}

Return a single clear sentence (max 3 sentences) describing what this {parent_type} contains and its purpose.
Do NOT include commentary, headers, or markdown — plain text only.
"""
