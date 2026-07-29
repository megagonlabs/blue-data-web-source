---
marp: true
theme: default
paginate: true
style: |
  section {
    font-size: 1.45rem;
    padding: 2.5rem 3rem;
  }
  section.lead {
    text-align: center;
    justify-content: center;
  }
  section.lead h1 {
    font-size: 2.8rem;
    color: #1a1a2e;
    border: none;
  }
  section.lead h3 {
    color: #4a90d9;
    font-weight: 400;
  }
  section.lead p {
    color: #666;
    font-size: 1.1rem;
  }
  h1 {
    color: #1a1a2e;
    border-bottom: 3px solid #4a90d9;
    padding-bottom: 0.25em;
    margin-bottom: 0.8em;
  }
  ul { line-height: 2; }
  li { margin-bottom: 0.1em; }
  strong { color: #1a1a2e; }
  table { font-size: 1.1rem; width: 100%; }
  th { background: #1a1a2e; color: white; }
  tr:nth-child(even) { background: #f4f4f8; }
  blockquote {
    border-left: 4px solid #4a90d9;
    background: #f0f6ff;
    padding: 0.8em 1.2em;
    margin: 1em 0;
    font-style: normal;
    color: #1a1a2e;
  }
---

<!-- _class: lead -->

# Web Content Intelligence

### Turning the open web into a searchable knowledge asset

Engineering · July 2026

---

# The Opportunity

The web contains an enormous amount of high-value, real-time information — news, research, reports, market signals — that our systems currently cannot access or reason over.

> **Every day, relevant content is published that our AI pipelines cannot see, search, or use.**

To act on web content today, teams must manually fetch pages, clean HTML, figure out chunking, and wire up storage — one-off, every time.

**We built a reusable platform capability that solves this once, for everyone.**

---

# What We Built

**`blue-data-web-source`** is an ingestion engine that turns any URL into structured, searchable knowledge — automatically.

Give it a URL. It returns semantically chunked content, ready for vector search or LLM context.

**Three capabilities, one unified interface:**

| | |
|---|---|
| **Ingest** | Fetch any URL → extract article content → chunk → embed → store |
| **Search** | Query stored content with natural language → ranked results |
| **Deduplicate** | Re-ingest the same URL anytime — content is updated, never duplicated |

No bespoke code per use case. No rebuilding the same pipeline twice.

---

# Value Proposition

**For product teams**
Any pipeline that needs web content — news monitoring, research assistants, RAG applications — can plug in and go. No infrastructure work required.

**For AI / LLM applications**
Web content is now a first-class knowledge source alongside internal documents. LLMs can reason over real-time, domain-specific web content.

**For the platform**
A single, well-tested, maintained capability replaces ad-hoc one-off solutions. Teams ship faster and on a shared foundation.

> Ingesting a news article, chunking it, embedding it, and making it searchable now takes **one API call** instead of weeks of custom work.

---

# How It Works

At a high level, the pipeline has five stages:

```
URL  →  Fetch  →  Extract  →  Chunk  →  Embed  →  Store & Search
```

1. **Fetch** — retrieves the page, handles redirects and timeouts reliably
2. **Extract** — isolates the article body; strips ads, navigation, and editorial noise
3. **Chunk** — splits content into overlapping, section-aware segments
4. **Embed** — converts each chunk into a semantic vector (via any embedding model)
5. **Store & Search** — upserts to a vector store; supports natural language KNN search

The pipeline is **modular** — embedding model and vector store are plug-and-play. Works with OpenAI embeddings and Redis today; extensible to any provider.

---

# What's Working Today

Phase 1 is complete and production-ready.

| Capability | Status |
|---|---|
| Ingest any public URL into a vector store | ✅ Live |
| Extract clean article text — strips ads, nav, CMS noise | ✅ Live |
| Natural language search across ingested content | ✅ Live |
| Re-ingest the same URL anytime without duplicating | ✅ Live |
| Plug in any embedding model or vector store | ✅ Live |
| Organise content into named collections | ✅ Live |
| Full test coverage — unit, integration, end-to-end | ✅ Live |

**84 automated tests.** All passing. The pipeline is verified end-to-end with no external dependencies required.

---

# Early Results — Demo

We ingested two articles and ran natural language search — no manual tagging, no keyword configuration.

**Articles ingested:**
- *Climate Policy Report 2026* — climate.example.com
- *China test-launches a ballistic missile in the South Pacific* — NPR, July 2026

**Search query:** `"greenhouse gas emissions and climate targets"`

**Top result:** Climate Policy Report 2026
*"Global greenhouse gas emissions continued to rise in 2025 despite international agreements. The Paris Accord signatories convened in March to discuss revised targets…"*

The pipeline correctly identified and returned the relevant article — automatically, from a natural language query, with zero manual curation.

---

# Vision

**Near-term: From single URLs to whole sources**

- **Scheduled ingestion** — monitor a domain or RSS feed; ingest new articles automatically
- **Freshness tracking** — detect when content changes and re-ingest; keep the knowledge base current
- **Bulk crawling** — ingest an entire site or publication archive in one operation

**Medium-term: Intelligence across sources**

- **Cross-source entity resolution** — link people, organisations, and events mentioned across different publications
- **Source trust scoring** — rank results by domain credibility alongside semantic relevance
- **Duplicate detection** — identify near-identical stories from different sources before storing

**Long-term: A real-time web knowledge layer**

A continuously updated, semantically searchable layer over the open web — feeding LLM applications, research tools, monitoring pipelines, and decision support systems across the organisation.

---

# Strategic Fit

`blue-data-web-source` extends the **blue-data platform** — it builds on the same abstractions already used for document ingestion (`blue-data-doc-source`), so teams already familiar with the platform adopt it immediately.

**Shared foundation means:**
- One place to improve chunking, embeddings, or storage — all consumers benefit
- New use cases ship faster because infrastructure is already solved
- Consistent data quality and schema across document and web sources

**What teams can build on top of this today:**
- News and signals monitoring pipelines
- Research and competitive intelligence assistants
- RAG applications grounded in real-time web content
- Domain-specific knowledge bases built from curated web sources

---

<!-- _class: lead -->

# Summary

**We built a platform capability that turns any URL into searchable knowledge — one API call, no bespoke infrastructure.**

Phase 1 is complete. The foundation is solid.

The roadmap takes this from single-URL ingestion to a **continuously updated, organisation-wide web knowledge layer**.

---

<!-- _class: lead -->

# Questions?
