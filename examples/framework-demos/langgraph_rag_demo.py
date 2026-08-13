"""Framework demo: LangGraph RAG graph over ingested web pages.

Two-node graph:
  [retrieve]  — WEB.SEARCH query for relevant web chunks
  [generate]  — OpenAI chat completion grounded on retrieved chunks
  END

Deps: pip install langgraph langchain-openai
      OPENAI_API_KEY=sk-...
Run:  OPENAI_API_KEY=sk-... python3 examples/framework-demos/langgraph_rag_demo.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TypedDict

sys.path.insert(0, str(Path(__file__).parent.parent))

if not os.getenv("OPENAI_API_KEY"):
    print("ERROR: OPENAI_API_KEY environment variable not set.")
    sys.exit(1)

try:
    from langgraph.graph import END, StateGraph
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import HumanMessage, SystemMessage
except ImportError:
    print("ERROR: langgraph or langchain-openai not installed.")
    print("  pip install langgraph langchain-openai")
    sys.exit(1)

from blue_data.source.vector.memory_vector_source import MemoryVectorSource
from blue_doc_source.source.document.embeddings.openai_embedding import OpenAIEmbeddingModel
from blue_web_source import WebDataSource
from mock_fetcher import (
    CLIMATE_HTML, CLIMATE_URL, NPR_URL, POLICY_HTML, POLICY_URL,
    build_mock_fetcher, load_npr_html,
)

COLLECTION = "rag_web"
W = 68


class RAGState(TypedDict):
    question: str
    context: list[dict]
    answer: str


def _hr():
    print("─" * W)


def _ok(msg: str):
    print(f"  ✓  {msg}")


def _excerpt(text: str, n: int = 110) -> str:
    text = text.replace("\n", " ").strip()
    return text[:n] + ("…" if len(text) > n else "")


def main():
    npr_html = load_npr_html()
    if npr_html is None:
        print("ERROR: data/fixtures/npr_article.html not found — run from repo root.")
        sys.exit(1)

    print()
    print("═" * W)
    print("  blue-data-web-source — LangGraph RAG Demo")
    print("═" * W)

    # ── Ingest articles ──────────────────────────────────────
    print()
    print("  SETUP — Ingest articles into vector store")
    _hr()

    embedding_model = OpenAIEmbeddingModel(model="text-embedding-3-small")
    vector_store    = MemoryVectorSource()
    source          = WebDataSource(
        name="rag-web",
        embedding_model=embedding_model,
        vector_source=vector_store,
        default_collection=COLLECTION,
    )
    source._pipeline._fetcher = build_mock_fetcher({
        CLIMATE_URL: CLIMATE_HTML,
        NPR_URL: npr_html,
        POLICY_URL: POLICY_HTML,
    })
    source.execute_query({"op": "WEB.ENSURE_INDEX", "collection": COLLECTION})

    total_chunks = 0
    for url in [CLIMATE_URL, NPR_URL, POLICY_URL]:
        records = source.execute_query({"op": "WEB.INGEST", "url": url, "collection": COLLECTION})
        if records:
            total_chunks += len(records)
            _ok(f"{len(records):2d} chunks  |  {records[0].get('domain', '?')}  |  {records[0].get('title', '—')[:45]}")

    _ok(f"Total chunks in vector store: {total_chunks}")

    # ── LangGraph graph ──────────────────────────────────────
    print()
    print("  LANGGRAPH — Building RAG graph")
    _hr()

    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)

    def retrieve(state: RAGState) -> RAGState:
        results = source.execute_query({
            "op": "WEB.SEARCH",
            "query": state["question"],
            "k": 4,
            "collection": COLLECTION,
        })
        return {**state, "context": results}

    def generate(state: RAGState) -> RAGState:
        if not state["context"]:
            return {**state, "answer": "No relevant context found in the knowledge base."}

        context_text = "\n\n".join(
            f"[Source: {r.get('url', '?')} | {r.get('title', '')}]\n{r['text']}"
            for r in state["context"]
        )
        messages = [
            SystemMessage(content=(
                "You are a helpful assistant. Answer the question using ONLY the provided context. "
                "Cite the source URL when referencing specific facts. "
                "If the context does not contain the answer, say so."
            )),
            HumanMessage(content=f"Context:\n{context_text}\n\nQuestion: {state['question']}"),
        ]
        response = llm.invoke(messages)
        return {**state, "answer": response.content}

    graph = StateGraph(RAGState)
    graph.add_node("retrieve", retrieve)
    graph.add_node("generate", generate)
    graph.set_entry_point("retrieve")
    graph.add_edge("retrieve", "generate")
    graph.add_edge("generate", END)
    app = graph.compile()

    _ok("Graph: [retrieve] → [generate] → END")
    _ok("retrieve: WEB.SEARCH top-4 chunks by cosine similarity")
    _ok("generate: GPT-4o-mini grounded on retrieved context")

    # ── Run queries ──────────────────────────────────────────
    questions = [
        "What are the main national commitments on greenhouse gas emissions?",
        "What is the annual deductible for health insurance coverage?",
    ]

    for q in questions:
        print()
        print(f"  QUESTION: {q}")
        _hr()

        result = app.invoke({"question": q, "context": [], "answer": ""})

        print(f"  Retrieved {len(result['context'])} chunks:")
        for r in result["context"]:
            score = r.get("score")
            score_str = f"{score:.4f}" if isinstance(score, float) else "?"
            print(f"    score={score_str}  {r.get('domain', '?')}  {_excerpt(r['text'], 80)}")

        print()
        print("  Answer:")
        for line in result["answer"].splitlines():
            print(f"    {line}")

    # ── Summary ──────────────────────────────────────────────
    print()
    print("  SUMMARY")
    _hr()
    _ok("Retrieval: WEB.SEARCH (semantic cosine similarity on OpenAI embeddings)")
    _ok("Generation: GPT-4o-mini grounded on top-4 chunks")
    _ok("Graph state: TypedDict with question / context / answer")
    _ok("Swap vector_source → RedisVectorSource for persistent production use")
    print()
    print("  Next: add a query-rewriting node or multi-hop retrieval loop")
    print("  to improve recall on complex multi-part questions.")
    print()
    print("═" * W)
    print()


if __name__ == "__main__":
    main()
