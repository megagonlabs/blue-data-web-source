# Extraction

The extraction layer converts raw HTML into clean article text and structured metadata. It sits between `WebFetcher` (which returns raw HTML) and the `Chunker` (which splits text into overlapping windows).

`WebExtractor` is the only built-in implementation. It wraps [trafilatura](https://trafilatura.readthedocs.io) — a hard dependency of this package.

---

## WebExtractor

`WebExtractor.extract(html, url)` → `ExtractResult | None`

Returns `None` when no main content body is extractable. The pipeline returns `[]` in this case — nothing is stored.

```python
from blue_web_source.source.web.extractor import WebExtractor

extractor = WebExtractor()
result = extractor.extract(html, url="https://news.example.com/article")

if result is None:
    print("No extractable content — pipeline returns []")
else:
    print(result.title)       # "Energy Policy Update"
    print(result.author)      # "Jane Smith" or None
    print(result.date)        # "2026-03-15" or None
    print(result.description) # meta description or None
    print(result.language)    # "en" or None
    print(result.text)        # cleaned article body
```

`ExtractResult` fields:

| Field | Type | Source |
|---|---|---|
| `url` | str | URL passed to `extract()` |
| `text` | str | Main article body after stripping and fragment removal |
| `title` | str \| None | `<title>` tag or `og:title` |
| `author` | str \| None | `meta[name=author]` or schema.org author |
| `date` | str \| None | `article:published_time` or heuristic date parse |
| `description` | str \| None | `meta[name=description]` |
| `language` | str \| None | `html[lang]` attribute |

All optional fields are `None` when not found — `WebChunkRecord.to_dict()` omits them.

---

## What trafilatura does

trafilatura identifies the main content region of a page by analysing the HTML tree structure, text density, and common boilerplate patterns. It removes:

- Navigation bars and menus
- Headers and footers
- Sidebars
- Ads and promotional content
- Comments
- Repetitive boilerplate blocks

It works well on article and blog-style pages — pages designed around a single main text body. It is less reliable on:
- **SPA shells** — React/Vue apps that render content client-side; httpx fetches static HTML before JS executes, so the body is empty
- **Search result pages** — content is structured as a list of snippets, not a main article
- **Login walls** — the "main content" is a form, not prose
- **Documentation sites with very short pages** — trafilatura may find no extractable body if the page has < ~50 words of prose

For these cases, `extract()` returns `None` and the pipeline returns `[]` — nothing is stored, no error is raised.

---

## CMS nav-fragment stripping

After trafilatura runs, `WebExtractor` applies a line-by-line pass to remove inline CMS navigation fragments. These are short lines that CMSes inject into article text — they appear in the extracted body because they look like prose to trafilatura's density estimator.

Stripped prefixes (case-sensitive, matched at the start of a line after stripping whitespace):

| Prefix | Example in the wild |
|---|---|
| `Related Story:` | `Related Story: How Coal Plants Are Being Repurposed` |
| `Read more:` | `Read more: The Economics of Green Hydrogen` |
| `See also:` | `See also: Offshore Wind Financing Trends` |
| `Also read:` | `Also read: Battery Costs Hit Record Low` |
| `Advertisement` | `Advertisement` |
| `Story continues` | `Story continues below advertisement` |

The stripping is applied before the text reaches the chunker, so none of these fragments appear in chunk text or section titles.

---

## Pages that return None

`WebExtractor.extract()` returns `None` in these common scenarios:

| Page type | Why |
|---|---|
| JS-redirect / meta-refresh | Body is empty; no prose for trafilatura to find |
| Login wall / authentication gate | Form elements don't produce extractable prose |
| SPA shell | JS has not executed; static HTML has no article body |
| Navigation-only page | All content is in `<nav>` / `<ul>` / link text |
| Very short or empty body | trafilatura threshold not met |

When `extract()` returns `None`, `WebPipeline.ingest()` returns `[]` immediately — the vector store is never called.

To verify why a specific URL produces no output:

```python
from blue_web_source.source.web.fetcher import WebFetcher
from blue_web_source.source.web.extractor import WebExtractor

fetcher   = WebFetcher()
extractor = WebExtractor()

result = fetcher.fetch("https://example.com/page")
print("Status :", result.status_code)
print("HTML   :", result.html[:500])

extracted = extractor.extract(result.html, result.final_url)
print("Result :", extracted)
```

---

## Metadata extraction

trafilatura calls `extract_metadata()` internally to populate the optional fields. The extraction strategy for each field:

**title** — tries `<title>`, `og:title`, `twitter:title`, `<h1>` in order. Returns the first match.

**author** — tries `meta[name=author]`, `meta[property=article:author]`, schema.org `Person`, byline patterns in the HTML. Returns a semicolon-separated string when multiple authors are found.

**date** — tries `meta[property=article:published_time]`, `<time datetime=...>`, schema.org `datePublished`, JSON-LD, and heuristic date patterns in the URL and body. Returns an ISO-8601 date string.

**description** — tries `meta[name=description]`, `og:description`. Returns the first match.

**language** — reads `html[lang]` attribute directly.

All fields can be `None` when the page provides no signals. Downstream code should always use `result.get("title")` rather than `result["title"]`.

---

## description on chunk 0 only

`WebPipeline` attaches `description` to **chunk 0 only** — not to subsequent chunks. This avoids repeating page-level metadata noise in every chunk record. If you need the description for all chunks, read it from chunk 0 and propagate manually.

```python
records = source.execute_query({"op": "WEB.INGEST", "url": url, "collection": "web"})

page_description = records[0].get("description") if records else None

for r in records:
    if r["chunk_index"] == 0:
        assert "description" not in r or r.get("description") == page_description
    else:
        assert "description" not in r   # omitted on chunks 1+
```

---

## trafilatura dependency

`trafilatura` is a hard dependency — importing `WebExtractor` without it installed raises `ImportError` with an install hint.

```bash
pip install trafilatura
# or install the full package which includes it:
pip install -e lib/
```

trafilatura in turn requires `lxml` for HTML parsing. Both are installed automatically.
