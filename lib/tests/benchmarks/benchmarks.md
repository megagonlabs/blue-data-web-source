# Benchmarks

The benchmark suite measures throughput and behaviour of the web ingestion pipeline on synthetic HTML corpora. It covers four areas: extraction throughput, nav-fragment removal overhead, chunk-size sweep, and per-stage pipeline breakdown.

---

## Running the benchmarks

```bash
# Default: medium corpus (50 pages), 5 timed runs
python lib/tests/benchmarks/runner.py

# Larger corpus for stable extraction-rate numbers
python lib/tests/benchmarks/runner.py --size large --runs 7

# Custom chunk size
python lib/tests/benchmarks/runner.py --size medium --runs 7 --chunk-size 300 --overlap 60
```

**Options:**

| Flag | Default | Description |
|---|---|---|
| `--size` | `medium` | Corpus size: `small` (10 pages), `medium` (50 pages), `large` (200 pages) |
| `--runs` | `5` | Timed repetitions per measurement. Median is reported. |
| `--chunk-size` | `200` | Chunk size used for the sweep and stage breakdown |
| `--overlap` | `40` | Overlap used for the sweep and stage breakdown |

The runner does one untimed warm-up pass before timed runs to avoid first-call overhead from trafilatura model loading and regex compilation.

---

## Corpus

The suite generates a deterministic synthetic HTML corpus with four page types at a fixed distribution:

| Type | Share | Structure | Description |
|---|---|---|---|
| `article_structured` | 40% | Rich | `<article>` with `<h2>` section headers + full metadata (title, date, author) in `<head>` |
| `article_unstructured` | 30% | None | Dense prose in `<article>`, no headers, title only in `<head>` |
| `article_mixed` | 20% | Light | Numbered sections + prose + inline CMS nav fragments (`Related Story:`, `Read more:`, etc.) |
| `no_content` | 10% | — | JS redirect + meta-refresh — `WebExtractor` returns `None`; pipeline returns `[]` |

Articles are ~500 words each. The corpus is generated from a fixed seed so results are reproducible across machines. No external files or network access required.

---

## Benchmarks

### 1. Extraction throughput

Runs `WebExtractor` (trafilatura) on each page type independently. Reports:

| Column | Description |
|---|---|
| Pages | Total pages of this type in the corpus |
| Content | Pages where `extract()` returned non-`None` |
| Extract% | Content pages as a fraction of total |
| Pages/sec | Extraction throughput (median across runs) |
| Title/Date/Author% | Metadata field coverage on content pages |

`no_content` pages return `None` immediately — they are included to measure the short-circuit path.

### 2. Nav-fragment removal overhead

Compares raw trafilatura extraction time vs. extraction + stripping pass on mixed-content pages. The stripping pass removes lines matching the six CMS nav-fragment prefixes (`Related Story:`, `Read more:`, `See also:`, `Also read:`, `Advertisement`, `Story continues`). Reports total fragments found in raw extracted text and the extra time the stripping pass adds.

### 3. Chunk-size sweep

Runs `SectionChunker` on pre-extracted text (from a structured article) across five `chunk_size` / `overlap` configurations (50→1000 words). Isolates chunker behaviour from extraction cost. Reports chunk count, average word count, and throughput.

### 4. Pipeline stage breakdown

Times each stage of the pipeline independently on a single structured article page:

- **Extraction (trafilatura)** — `WebExtractor.extract()` — the new stage exclusive to web-source
- **Chunking** — `SectionChunker.chunk_with_titles()`
- **Enrichment** — `BasicEnricher.enrich()` on each chunk
- **Embedding** — `DummyEmbeddingModel.embed_text()` (SHA-256, no network) to isolate in-process cost

Reports time in milliseconds, percentage of total, and microseconds per chunk.

---

## Baseline results

Reference run: MacBook Pro M-series, Python 3.12, medium corpus, 7 timed repetitions.
Full raw output is in `baseline.txt`.

### Extraction throughput

```
Page type               Pages  Content  Extract%     Pages/sec    Title%     Date%    Author%
article_structured         20       20      100%         889.8      100%      100%       100%
article_unstructured       15       15      100%         243.8      100%        0%         0%
article_mixed              10       10      100%         699.4      100%      100%         0%
no_content                  5        0        0%       1,133.4        0%        0%         0%

Overall extraction rate: 90%  (45/50 pages yielded content)
Average throughput (content pages): 611.0 pages/sec
```

### Nav-fragment removal (mixed pages)

```
Pages benchmarked:       10
Total nav fragments:     23   (2.3 avg per page)
Strip overhead:          0.00 ms  (~8% at large scale, 40 pages)
```

### Chunk-size sweep (SectionChunker, structured article, ~550 words)

```
chunk_size  overlap  Chunks  Avg(wds)  Std(wds)  Throughput(Kwps)
        50       10      15      46.3      12.4        18,864.1K
       100       20       8      85.5      34.3        19,604.4K
       200       40       4     170.5      59.0        20,655.7K
       500      100       2     331.0     239.0        21,931.7K
      1000      150       1     562.0       0.0        23,335.0K
```

### Pipeline stage breakdown (structured article, ~550 words, chunk_size=200)

```
Stage                        Time(ms)    % total      µs/chunk
Extraction (trafilatura)         1.13      63.0%         281.8
Chunking                         0.03       1.7%           7.5
Enrichment                       0.63      35.0%         156.6
Embedding (dummy)                0.00       0.3%           1.2
Total                            1.79     100.0%         447.1
```

---

## Key findings

**Extraction is the dominant stage.** Trafilatura consumes 63% of total in-process pipeline time at ~282 µs/chunk. This is the primary difference from blue-data-doc-source, where text parsing is near-zero cost and enrichment dominates (94%). Disabling `BasicEnricher` when entity data is not used roughly halves per-page pipeline cost; replacing trafilatura with a faster extractor would be the more impactful optimisation.

**Page structure strongly affects extraction speed.** Structured pages (clear `<h2>` headers, `<article>` tags) extract 3.6× faster than unstructured prose (889 vs 244 pages/sec). trafilatura resolves content regions faster when the HTML tree has clear semantic structure. No-content pages (JS redirects) return `None` fastest — trafilatura short-circuits when the body is empty.

**Metadata coverage follows page structure.** Title is available on 100% of content pages — trafilatura reads `<title>` reliably. Date requires `article:published_time` in `<head>`; author requires `meta[name=author]`. In production, expect 60–90% title coverage and 30–60% date coverage depending on the site. Author coverage is typically below 20% on news and documentation sites.

**Nav-fragment removal is effectively free at page scale.** The regex stripping pass adds negligible overhead on small batches. At large scale (40+ mixed pages per call) it contributes ~8% of extraction+strip time — still dominated by trafilatura.

**Chunker throughput is unchanged from doc-source.** `SectionChunker` processes 18–23M words/sec on extracted text, identical to its performance on document text. The chunker sees plain text in both cases.

**Embedding dominates at production scale.** `DummyEmbeddingModel` (SHA-256) costs ~1 µs/chunk. Real `text-embedding-3-small` calls cost ~50–150 ms per API call — roughly 100–300× the entire in-process pipeline cost per page. Batch API calls or a local embedding model eliminate this bottleneck.

---

## Adding a new baseline

After a significant change — trafilatura version upgrade, enricher rewrite, Python version change — capture a new baseline:

```bash
python lib/tests/benchmarks/runner.py --size medium --runs 7 > lib/tests/benchmarks/baseline.txt
```

Add the machine spec, Python version, and date as a comment at the top so results are comparable across environments.
