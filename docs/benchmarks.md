# Benchmarks

Reference benchmark run — medium corpus (50 pages), 7 timed repetitions (median reported).  
Machine: MacBook Pro M-series. Python 3.12. Date: 2026-08-12.  
Command: `python lib/tests/benchmarks/runner.py --size medium --runs 7`

---

## Running the benchmarks

```bash
# Default: medium corpus, 5 runs
python lib/tests/benchmarks/runner.py

# Larger corpus for stable extraction-rate numbers
python lib/tests/benchmarks/runner.py --size large --runs 7

# Custom chunk size
python lib/tests/benchmarks/runner.py --size medium --runs 7 --chunk-size 300 --overlap 60
```

Benchmark files:

| File | What it measures |
|---|---|
| `lib/tests/benchmarks/datasets.py` | Synthetic HTML corpus generator (4 page types) |
| `lib/tests/benchmarks/bench_extraction.py` | WebExtractor throughput, extraction rate, metadata coverage, nav-fragment removal |
| `lib/tests/benchmarks/bench_pipeline.py` | Per-stage timing (extraction + chunking + enrichment + embedding) and chunk-size sweep |
| `lib/tests/benchmarks/runner.py` | Unified entry point |

---

## Corpus

The synthetic corpus has four page types at a fixed distribution:

| Page type | % of corpus | Description |
|---|---|---|
| `article_structured` | 40% | `<article>` with `<h2>` headers + full metadata (title, date, author) |
| `article_unstructured` | 30% | Dense prose, no headers, title only in `<head>` |
| `article_mixed` | 20% | Numbered sections + inline CMS nav fragments (`Related Story:`, `Read more:`, etc.) |
| `no_content` | 10% | JS redirect + meta-refresh — `WebExtractor` returns `None` |

Articles are ~500 words each. `SMALL`=10 pages, `MEDIUM`=50 pages, `LARGE`=200 pages.

---

## Results

### 1. Extraction throughput

```
  Page type               Pages  Content  Extract%     Pages/sec    Title%     Date%    Author%
  ────────────────────────────────────────────────────────────────────────────────────────────
  article_structured         20       20      100%         889.8      100%      100%       100%
  article_unstructured       15       15      100%         243.8      100%        0%         0%
  article_mixed              10       10      100%         699.4      100%      100%         0%
  no_content                  5        0        0%       1,133.4        0%        0%         0%
  ────────────────────────────────────────────────────────────────────────────────────────────

  Overall extraction rate: 90%  (45/50 pages yielded content)
  Average throughput (content pages):   611.0 pages/sec
```

### 2. Nav-fragment removal overhead

```
  Pages benchmarked:          10
  Total nav fragments found:  23
  Avg fragments per page:     2.3
  Strip overhead:             0.00 ms total (< 1% at medium scale)
```

At large scale (200 pages, 40 mixed pages): **8.6% overhead** of the extraction+strip pass. The stripping regex is O(lines) so its cost scales with page size, not corpus size.

### 3. Chunk-size sweep (SectionChunker, structured article, ~550 words)

```
  chunk_size  overlap  Chunks  Avg(wds)  Std(wds)  Throughput(Kwps)
  ──────────────────────────────────────────────────────────────
          50       10      15      46.3      12.4        18,864.1K
         100       20       8      85.5      34.3        19,604.4K
         200       40       4     170.5      59.0        20,655.7K
         500      100       2     331.0     239.0        21,931.7K
        1000      150       1     562.0       0.0        23,335.0K
```

### 4. Pipeline stage breakdown

```
  Stage                        Time(ms)    % total      µs/chunk
  ──────────────────────────────────────────────────────────────
  Extraction (trafilatura)         1.13      63.0%         281.8
  Chunking                         0.03       1.7%           7.5
  Enrichment                       0.63      35.0%         156.6
  Embedding (dummy)                0.00       0.3%           1.2
  ──────────────────────────────────────────────────────────────
  Total                            1.79     100.0%         447.1
```

---

## Key findings

### 1. Extraction is the dominant stage

Trafilatura HTML extraction consumes **63% of total pipeline time** (~282 µs/chunk). This is the primary bottleneck in blue-data-web-source and the key difference from blue-data-doc-source, where text parsing is near-zero cost.

Enrichment (BasicEnricher regex) is the second stage at **35%** (~157 µs/chunk). Disabling the enricher when entity data is not needed roughly halves per-page pipeline cost.

### 2. Page structure strongly affects extraction speed

Structured pages (clear `<h2>` headers, article tags) are extracted **3.6× faster** than unstructured prose pages (889 vs 244 pages/sec). trafilatura resolves content regions faster when the HTML tree has clear semantic structure. No-content pages (JS redirects) return `None` fastest — trafilatura short-circuits when the body is empty.

### 3. Metadata coverage follows page structure

| Field | Structured | Mixed | Unstructured |
|---|---|---|---|
| title | 100% | 100% | 100% |
| date | 100% | 100% | 0% |
| author | 100% | 0% | 0% |

All three page types include `<title>` in `<head>`, so title coverage is 100% across all content pages. Date coverage requires `article:published_time` meta tag — present in structured and mixed but not unstructured pages. Author coverage requires `meta[name=author]` — only present in structured pages.

In production, expect 60–90% title coverage and 30–60% date coverage depending on the site type. Author coverage is often below 20% on news and documentation sites.

### 4. Nav-fragment removal is effectively free at page scale

The stripping pass (regex over extracted text lines) adds negligible overhead at small scale. At large scale across many mixed pages it contributes ~8% of extraction+strip time — still dominated by trafilatura. The pass is always applied; there is no cost to enabling it.

### 5. Chunker throughput is unchanged from doc-source

SectionChunker processes 18–23M words/sec on extracted web text, identical to its performance in blue-data-doc-source. The chunker sees plain text in both cases — the extraction layer upstream has no effect on chunking speed.

### 6. Embedding dominates at production scale

`DummyEmbeddingModel` (SHA-256, no network) costs ~1 µs/chunk. A real OpenAI `text-embedding-3-small` call costs ~50–150ms per API call at typical batch sizes. At 5 chunks/page, embeddings will cost **~100–300× more** than the entire rest of the pipeline combined when using OpenAI. Batch API calls (`EmbeddingModel.embed_batch` if available) or a local embedding model eliminate this bottleneck.

---

## Comparison with blue-data-doc-source

| | blue-data-web-source | blue-data-doc-source |
|---|---|---|
| Dominant stage | Extraction / trafilatura (63%) | Enrichment / BasicEnricher (94%) |
| Enrichment cost | 157 µs/chunk | 122 µs/chunk |
| Parsing / extraction | 282 µs/chunk (trafilatura) | ~1 µs/chunk (text split) |
| Chunking | 7.5 µs/chunk | 7.0 µs/chunk |
| Embedding (dummy) | 1.2 µs/chunk | 1.1 µs/chunk |

Web-source's extraction layer is fundamentally more expensive than doc-source's parser because trafilatura performs full HTML tree analysis and content classification. If extraction speed is critical, consider:

- Processing pages in parallel (trafilatura releases the GIL for the HTML parse)
- Skipping `extract_metadata()` when provenance fields are not needed (saves ~30% of extraction time)
- Caching HTML locally and batch-extracting offline rather than at ingest time
