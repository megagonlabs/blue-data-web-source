# blue-data-web-source

Ingest web pages, RSS feeds, and live search results into a queryable vector index.

Web pages are live data — they update on a schedule, arrive through feeds, and belong in an agent's data layer alongside SQL tables, documents, and graph stores. `blue-data-web-source` handles the full path from URL to queryable vector index with built-in freshness tracking, and plugs into blue-data's `DataRegistry` so web content becomes a first-class member of the same intelligence layer as the rest of your data.

→ **[Full documentation](docs/README.md)** · **[Quick start](docs/quickstart.md)** · **[Installation](docs/installation.md)** · **[Examples](examples/README.md)** · **[CLI](docs/cli.md)** · **[MCP server](docs/mcp.md)**

---

## What it does

- **Ingest** web pages, RSS/Atom feeds, and URL batches — fetch, extract, chunk, enrich, embed, and upsert in one call.
- **Crawl** a site from a seed URL with a polite BFS crawler (robots.txt, rate limiting, retry backoff).
- **Search** your ingested corpus by semantic similarity, or search the live web and ingest the results in one op.
- **Extract** typed fields from any page using a caller-supplied JSON Schema.
- **Skip** unchanged pages on every run through built-in content-hash freshness tracking — only new or changed content is re-embedded.
- **Register** web content in blue-data's `DataRegistry` alongside SQL, document, and graph sources, with a consistent chunk schema.

Full feature list: [docs/features.md](docs/features.md). Runnable demos: [examples/](examples/).

---

## Repository layout

```
blue-data-web-source/
├── lib/                    # Core Python library (blue-data-web-source)
│   ├── src/blue_data_web_source/
│   └── tests/              # unit, integration, e2e, and repo-level tests
├── platform/
│   ├── web-cli/            # `blue-web` command-line interface
│   ├── web-mcp/            # MCP server (Claude Desktop / Claude Code)
│   └── web-api/            # FastAPI REST service (port 8002)
├── docs/                   # Documentation
├── examples/               # Runnable example scripts with captured output
├── design/                 # Design notes and decision log
└── data/
    └── fixtures/           # Saved HTML fixtures for integration tests
```

---

## Requirements

- Python 3.12 or 3.13
- Sibling repos cloned beside this one: [`blue-data`](../blue-data) and [`blue-data-doc-source`](../blue-data-doc-source)
- API keys, as needed for the features you use:

```bash
export OPENAI_API_KEY=sk-...        # embeddings and WEB.EXTRACT
export ANTHROPIC_API_KEY=sk-ant-... # natural-language queries (execute_query_nl)
export TAVILY_API_KEY=tvly-...      # live web search (WEB.SEARCH_WEB)
```

Optional extras (`[openai]`, `[redis]`, `[llm]`, `[feed]`, `[all]`) are documented in [docs/installation.md](docs/installation.md).

---

## Quick start

```bash
# Clone blue-data and blue-data-doc-source beside this repo first, then:
python -m venv .venv && source .venv/bin/activate
bash lib/scripts/install.sh          # installs blue-data, blue-data-doc-source, and lib/

# Ingest a page and inspect the extracted chunks — no API key required:
python - <<'PY'
from blue_data_web_source.source.web.pipeline import WebPipeline

for r in WebPipeline(chunk_size=500, overlap=100).ingest("https://text.npr.org/nx-s1-5884537"):
    print(r["chunk_id"], r["domain"], r["text"][:80])
PY
```

For the full end-to-end walkthrough — embeddings, a vector store, feeds, crawling, and search — see [docs/quickstart.md](docs/quickstart.md) and the [guide](docs/guide.md).

---

## Packages

| Package | Description |
|---|---|
| [`lib/`](lib/README.md) | Core library `blue-data-web-source` — `WebDataSource`, `WebPipeline`, and all ingestion components. Install: `bash lib/scripts/install.sh` |
| [`platform/web-cli/`](platform/web-cli/README.md) | `blue-web` command-line interface over the core ops. Install: `pip install -e platform/web-cli` |
| [`platform/web-mcp/`](platform/web-mcp/README.md) | MCP server exposing web ops as AI-callable tools. Install: `pip install -e platform/web-mcp` |
| [`platform/web-api/`](platform/web-api/README.md) | FastAPI REST service on port 8002. Install: `pip install -e platform/web-api` |

---

## Code of Conduct

This project adheres to the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md).
By participating, you are expected to uphold this code. Report unacceptable behavior
to contact_oss@megagon.ai.

---

## Disclosures

This software may include, incorporate, or access open source software (OSS) components,
datasets and other third party components, including those identified below. The license terms
respectively governing the datasets and third-party components continue to govern those
portions, and you agree to those license terms may limit any distribution, use, and copying.
You may use any OSS components under the terms of their respective licenses, which may
include BSD 3, Apache 2.0, and other licenses. In the event of conflicts between Megagon Labs,
Inc. (“Megagon”) license conditions and the OSS license conditions, the applicable OSS
conditions governing the corresponding OSS components shall prevail.
You agree not to, and are not permitted to, distribute actual datasets used with the OSS
components listed below. You agree and are limited to distribute only links to datasets from
known sources by listing them in the datasets overview table below. You agree that any right to
modify datasets originating from parties other than Megagon are governed by the respective
third party’s license conditions.
You agree that Megagon grants no license as to any of its intellectual property and patent rights.
THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS (INCLUDING
MEGAGON) “AS IS” AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED
TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED
AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE. You agree to cease using,
incorporating, and distributing any part of the provided materials if you do not agree with the
terms or the lack of any warranty herein.
While Megagon makes commercially reasonable efforts to ensure that citations in this
document are complete and accurate, errors may occur. If you see any error or omission, please
help us improve this document by sending information to contact_oss@megagon.ai.

### Datasets

All datasets used within the product are listed below (including their copyright holders and the license information).

For Datasets having different portions released under different licenses, please refer to the included source link specified for each of the respective datasets for identifications of dataset files released under the identified licenses.

</br>

| ID  | Dataset Name | Modified | Copyright Holder | Upstream Link | License  |
|-----|----------------------------------|----------|------------------|-----------------------------------------------------------------------------------------------------------|--------------------|
| 1 | NPR news article (test fixture `data/fixtures/npr_article.html`) | No | NPR (National Public Radio, Inc.) | [link](https://www.npr.org/nx-s1-5884537) | © NPR — see [NPR Terms of Use](https://www.npr.org/about-npr/179876898/terms-of-use) |

### Open Source Software (OSS) Components

All OSS components used within the product are listed below (including their copyright holders and the license information).

For OSS components having different portions released under different licenses, please refer to the included Upstream link(s) specified for each of the respective OSS components for identifications of code files released under the identified licenses.

</br>

| ID  | OSS Component Name | Modified | Copyright Holder | Upstream Link | License  |
|-----|----------------------------------|----------|------------------|-----------------------------------------------------------------------------------------------------------|--------------------|
| 1 | anthropic | No | Anthropic, PBC | [link](https://github.com/anthropics/anthropic-sdk-python) | MIT License |
| 2 | click | No | Pallets | [link](https://github.com/pallets/click) | BSD License |
| 3 | fastapi | No | Sebastián Ramírez | [link](https://github.com/fastapi/fastapi) | MIT License |
| 4 | feedparser | No | Kurt McKee, Mark Pilgrim | [link](https://github.com/kurtmckee/feedparser) | BSD License |
| 5 | httpx | No | Encode OSS Ltd. | [link](https://github.com/encode/httpx) | BSD License |
| 6 | MCP | No | Anthropic, PBC | [link](https://github.com/modelcontextprotocol/python-sdk) | MIT License |
| 7 | openai | No | OpenAI | [link](https://github.com/openai/openai-python) | Apache Software License |
| 8 | pydantic | No | Pydantic Services Inc. and individual contributors | [link](https://github.com/pydantic/pydantic) | MIT License |
| 9 | redis | No | Redis, inc. | [link](https://github.com/redis/redis-py) | MIT License |
| 10 | tavily-python | No | Tavily | [link](https://github.com/tavily-ai/tavily-python) | MIT License |
| 11 | trafilatura | No | Adrien Barbaresi | [link](https://github.com/adbar/trafilatura) | Apache Software License |
| 12 | uvicorn | No | Encode OSS Ltd | [link](https://github.com/encode/uvicorn) | BSD License |
