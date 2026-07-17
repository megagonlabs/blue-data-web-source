from __future__ import annotations

from typing import Any, Dict, Optional


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
        raise ValueError(f"Unexpected tool name from model: {tool_name!r}")
