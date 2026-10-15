from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple


class NLQueryParser:
    """Translates free-text requests into WebDataSource op dicts using Claude tool use."""

    _TOOLS = [
        {
            "name": "ingest_url",
            "description": "Fetch a web page and ingest it into the knowledge base.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The fully-qualified URL to fetch and ingest.",
                    },
                    "collection": {
                        "type": "string",
                        "description": "Optional collection to store the content in.",
                    },
                },
                "required": ["url"],
            },
        },
        {
            "name": "search",
            "description": "Run a semantic search over ingested content.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language search query.",
                    },
                    "k": {
                        "type": "integer",
                        "description": "Number of results to return (default 5).",
                    },
                    "collection": {
                        "type": "string",
                        "description": "Optional collection to search within.",
                    },
                },
                "required": ["query"],
            },
        },
        {
            "name": "ensure_index",
            "description": "Create or verify the vector index for a collection.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "collection": {
                        "type": "string",
                        "description": "Collection name to create the index for.",
                    },
                },
            },
        },
        {
            "name": "crawl",
            "description": (
                "Crawl a website starting from a seed URL and ingest all reachable pages. "
                "Use this when the user wants to index an entire site, documentation, or "
                "a section of a domain rather than a single page."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Seed URL to start crawling from.",
                    },
                    "depth": {
                        "type": "integer",
                        "description": "How many link-hops to follow from the seed (default 1).",
                    },
                    "max_pages": {
                        "type": "integer",
                        "description": "Maximum number of pages to fetch (default 10).",
                    },
                    "collection": {
                        "type": "string",
                        "description": "Optional collection to store crawled content in.",
                    },
                },
                "required": ["url"],
            },
        },
        {
            "name": "search_web",
            "description": (
                "Search the live web for a query, fetch and ingest the top result pages "
                "on the fly, and return semantically ranked chunks. Use this when the user "
                "wants fresh information from the web rather than searching already-ingested content."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language search query to send to the web search API.",
                    },
                    "k": {
                        "type": "integer",
                        "description": "Number of web pages to fetch and ingest (default 5).",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of ranked chunks to return (default same as k).",
                    },
                    "collection": {
                        "type": "string",
                        "description": "Optional collection to store and search within.",
                    },
                },
                "required": ["query"],
            },
        },
        {
            "name": "ingest_feed",
            "description": (
                "Fetch an RSS or Atom feed and ingest all articles into the knowledge base. "
                "Use this when the user provides a feed URL or wants to subscribe to a news "
                "feed, blog, or podcast. If a freshness store is configured, articles whose "
                "content has not changed since the last ingest are skipped automatically."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The RSS or Atom feed URL.",
                    },
                    "since": {
                        "type": "string",
                        "description": "ISO 8601 datetime (e.g. '2026-08-01T00:00:00Z'). Only ingest entries published at or after this time. Entries without a date are always included.",
                    },
                    "max_entries": {
                        "type": "integer",
                        "description": "Maximum number of articles to ingest after date filtering. Omit to ingest all.",
                    },
                    "collection": {
                        "type": "string",
                        "description": "Optional collection to store the articles in.",
                    },
                },
                "required": ["url"],
            },
        },
    ]

    def __init__(self, model: str = "claude-opus-4-8", client=None):
        self._model = model
        self._client = client

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:
                raise ImportError(
                    "The 'anthropic' package is required for natural language query parsing. "
                    "Install it with: pip install 'blue-data-web-source[nl]'"
                ) from exc
            self._client = anthropic.Anthropic()
        return self._client

    def parse(self, text: str) -> Dict[str, Any]:
        """Return a WebDataSource op dict derived from the free-text *text*."""
        client = self._get_client()

        response = client.messages.create(
            model=self._model,
            max_tokens=512,
            tools=self._TOOLS,
            tool_choice={"type": "any"},
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Classify the following request and call the matching tool.\n\n"
                        f"Request: {text}"
                    ),
                }
            ],
        )

        for block in response.content:
            if block.type == "tool_use":
                return self._to_op(block.name, block.input)

        raise ValueError(f"Model did not return a tool call for request: {text!r}")

    def parse_with_llm_fn(self, text: str, llm_fn) -> Dict[str, Any]:
        """Return a WebDataSource op dict using a generic ``(prompt) -> str`` llm_fn.

        Used when a caller (e.g. ``DataRegistry.execute_query_nl``) supplies a
        shared LLM callable rather than relying on this parser's own Anthropic
        tool-use client. The model is asked to emit a single JSON object naming
        one of the known tools; it is then mapped to a ``WEB.*`` op dict.
        """
        raw = llm_fn(self._build_llm_fn_prompt(text))
        name, args = self._extract_tool_call(raw)
        return self._to_op(name, args)

    def _build_llm_fn_prompt(self, text: str) -> str:
        lines = [
            "Translate the user request into exactly one tool call.",
            "Available tools:",
        ]
        for tool in self._TOOLS:
            schema = tool["input_schema"]
            required = set(schema.get("required", []))
            params = ", ".join(
                f"{key}{'*' if key in required else ''}: {spec.get('description', '')}"
                for key, spec in schema.get("properties", {}).items()
            )
            lines.append(f"- {tool['name']}: {tool['description']} (params — {params})")
        lines += [
            "",
            'Respond with ONLY a JSON object of the form '
            '{"name": "<tool>", "input": {<arguments>}} and nothing else '
            "(* marks required arguments).",
            "",
            f"Request: {text}",
        ]
        return "\n".join(lines)

    def _extract_tool_call(self, raw: str) -> Tuple[str, Dict[str, Any]]:
        """Parse the first JSON object out of *raw* (tolerating prose/code fences)."""
        if not isinstance(raw, str):
            raise ValueError(f"llm_fn must return a string, got {type(raw).__name__}")
        start = raw.find("{")
        if start == -1:
            raise ValueError(f"llm_fn did not return a JSON tool call: {raw!r}")
        try:
            data, _ = json.JSONDecoder().raw_decode(raw[start:])
        except json.JSONDecodeError as exc:
            raise ValueError(f"llm_fn did not return valid JSON: {raw!r}") from exc
        name = data.get("name")
        if not name:
            raise ValueError(f"llm_fn response missing a tool 'name': {raw!r}")
        args = data.get("input") or {}
        return name, args

    def _to_op(self, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        if tool_name == "ingest_url":
            op: Dict[str, Any] = {"op": "WEB.INGEST", "url": args["url"]}
            if "collection" in args:
                op["collection"] = args["collection"]
            return op
        if tool_name == "search":
            op = {"op": "WEB.SEARCH", "query": args["query"]}
            if "k" in args:
                op["k"] = args["k"]
            if "collection" in args:
                op["collection"] = args["collection"]
            return op
        if tool_name == "ensure_index":
            op = {"op": "WEB.ENSURE_INDEX"}
            if "collection" in args:
                op["collection"] = args["collection"]
            return op
        if tool_name == "crawl":
            op = {"op": "WEB.CRAWL", "url": args["url"]}
            if "depth" in args:
                op["depth"] = args["depth"]
            if "max_pages" in args:
                op["max_pages"] = args["max_pages"]
            if "collection" in args:
                op["collection"] = args["collection"]
            return op
        if tool_name == "search_web":
            op = {"op": "WEB.SEARCH_WEB", "query": args["query"]}
            if "k" in args:
                op["k"] = args["k"]
            if "top_k" in args:
                op["top_k"] = args["top_k"]
            if "collection" in args:
                op["collection"] = args["collection"]
            return op
        if tool_name == "ingest_feed":
            op = {"op": "WEB.INGEST_FEED", "url": args["url"]}
            if "since" in args:
                op["since"] = args["since"]
            if "max_entries" in args:
                op["max_entries"] = args["max_entries"]
            if "collection" in args:
                op["collection"] = args["collection"]
            return op
        raise ValueError(f"Unexpected tool name from model: {tool_name!r}")
