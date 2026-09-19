# blue-data-web-source — CLI

`blue-web` command-line interface. Provides subcommands for all ops: `ensure-index`, `ingest`, `crawl`, `feed`, `search`, `search-web`, `extract`.

## Install

```bash
pip install -e /path/to/blue-data-web-source/lib
pip install -e .
```

## Usage

```bash
blue-web ingest https://example.com/article
blue-web search "climate policy 2026" -k 5
blue-web crawl https://docs.example.com --depth 2
```

## Docs

- [CLI reference](../../docs/cli.md)
- [Installation](../../docs/installation.md)
