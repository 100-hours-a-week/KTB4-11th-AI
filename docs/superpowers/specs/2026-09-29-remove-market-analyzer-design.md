# Remove market-analyzer-mcp and market-analyzer

**Date:** 2026-09-29  
**Status:** Draft for review  
**Assumption:** #54, the Python/LangChain `portfolio-builder` implementation, has merged.

## Purpose

Remove two obsolete layers now that `portfolio-builder` computes and interprets its own technical
signals from QuestDB OHLCV:

- `services/market-analyzer-mcp`, the unused MCP HTTP service.
- `packages/market-analyzer`, the now-single-consumer TA-Lib library.

The resulting dependency path is `portfolio-builder -> QuestDB + TA-Lib`; there is no MCP transport
and no shared market-analysis package. Technical signal behaviour is the implementation defined by
#54, not a new or changed product decision in this work.

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Technical-analysis owner | `portfolio-builder` | Its LangChain tool is the only consumer after #54. Keeping a separate package only preserves an unnecessary boundary. |
| MCP service | Delete | It has no caller after #54 and adds an image, settings, transport, and deployment surface. |
| Shared replacement | None | No second caller exists. Extract only if one materializes. |
| Historical designs | Preserve with supersession notes | They explain prior decisions; they must not be mistaken for the current runtime contract. |

## Changes

### Remove

- Delete `services/market-analyzer-mcp/`, `docker/market-analyzer-mcp.Dockerfile`, and
  `docker/requirements/market-analyzer-mcp.txt`.
- Delete `packages/market-analyzer/` and its tests.
- Remove their references from Compose, CI image matrices and export checks, `tach.toml`, workspace
  dependency metadata, Dockerfiles, generated requirements, `uv.lock`, `README.md`, and `AGENTS.md`.

### Retain in portfolio-builder

#54's in-service measurement, interpretation, QuestDB reader, and `analyze_technicals` LangChain
tool remain the sole technical-analysis implementation. Its `pyproject.toml` must depend directly
on the packages it imports, including `ta-lib` and `numpy`, and must not declare
`ktb-market-analyzer`. The service's image installs only `ktb-core` and `portfolio-builder` as
first-party packages.

The aggregate app image likewise stops copying and installing `packages/market-analyzer`.

### Documentation

Update the architecture table and commands so neither removed member is presented as runnable or
available. Add a short supersession note to the earlier MCP-oriented portfolio-builder and service
skeleton designs. The #54 LangChain design remains the authoritative technical-analysis design;
this document records its cleanup consequences.

## Non-goals

- Changing signal formulas, thresholds, timeframes, QuestDB queries, or agent behaviour from #54.
- Moving technical analysis into `ktb-core` or creating another shared library.
- Changing market collection or adding new market-data sources.

## Verification

1. Search tracked runtime, build, CI, and documentation files for `market-analyzer-mcp`,
   `market_analyzer_mcp`, `ktb-market-analyzer`, and `ktb_market_analyzer`; only explicit
   historical supersession references may remain.
2. Run `uv lock`, then regenerate every changed Docker requirements file with the repository's
   prescribed `uv export` command.
3. Run `uv run ruff check .`, `uv run ruff format --check .`, `uv run pytest`, and `uv run tach
   check`.
4. Build `docker/portfolio-builder.Dockerfile` and `docker/app.Dockerfile` to prove neither relies
   on the deleted workspace package.
