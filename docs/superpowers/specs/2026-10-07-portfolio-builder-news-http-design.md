# Portfolio builder news HTTP design

## Goal

Portfolio builder reads news through `news-http` and keeps direct PostgreSQL access for portfolios and company reference data, plus QuestDB access for prices. A missing news result remains distinguishable from a failed HTTP request.

## API design

All new routes return JSON and use ISO 8601 timestamps with offsets. IDs identify domain objects and do not expose join-table structure. Existing `GET /stocks/{stock_code}/clusters` and `GET /clusters/{cluster_id}/articles` remain unchanged. A successful empty search uses `200` with an empty array; a missing cluster detail uses `404`. Invalid parameters use `422`, and a database failure uses `500`.

| Route | Input | `200` response | Ordering and bounds |
|---|---|---|---|
| `GET /clusters/search` | `q`: comma-separated words, e.g. `삼성전자,반도체` | `[{"id": 12, "title": "...", "excerpt": "...", "rank": 0.4}]` | Trim whitespace around each word; all words must match as prefixes. Rank descending, ID descending; at most 10. Empty entries, including `foo,`, and entries containing whitespace are `422`. |
| `GET /clusters/{id}` | Positive bigint ID | `{"id": 12, "title": "...", "summary": "...", "updated_at": "...", "articles": [{"title": "...", "source": "...", "published_at": "..."}], "entities": [{"id": 7, "name": "...", "type": "...", "company_id": "..."}], "relations": [{"source": "...", "type": "...", "target": "...", "description": "..."}]}` | Articles newest first, entities by ID, relations by ID. A cluster without a summary is `404`. |
| `GET /recent-news` | `days`: positive integer | `{"clusters": [{"id": 12, "title": "...", "summary": "...", "updated_at": "..."}], "companies": [{"company_id": "...", "name": "...", "stock_code": "...", "cluster_ids": [12], "themes": ["theme (main)"]}], "theme_count": 1}` | Clusters updated within the window, newest first; companies by ID; themes major first then name. Empty window returns empty arrays and zero. |
| `GET /graph/neighborhood` | `name`: nonempty entity name; `depth`: 1–3, default 2 | `{"nodes": [{"id": 7, "hop": 0, "name": "...", "type": "...", "company_id": "..."}], "edges": [{"id": 8, "source": "...", "type": "...", "target": "...", "description": "...", "cluster_id": 12, "hop": 0}], "truncated": false}` | Bidirectional traversal; nodes by hop then ID, max 100; edges by hop then ID, max 200. |
| `GET /graph/paths` | `from_name`, `to_name`: nonempty entity names; `max_depth`: 1–6, default 4 | `{"paths": [{"length": 1, "steps": [{"from": "...", "to": "...", "type": "...", "direction": "forward", "description": "...", "cluster_id": 12}]}], "truncated": false}` | Shortest simple bidirectional paths first, max 20. |

Graph lookup with no matching entity returns `404` with a candidate-name list, preserving the agent's ability to refine the name. A valid pair of entities with no connecting path returns `200` with `paths: []`. Database query timeout returns `504`, distinct from an empty graph result. The API owns full-text normalization, alias matching, and graph traversal; portfolio builder does not construct SQL or PostgreSQL search syntax for news.

The recent-news response contains mentioned companies and their themes so portfolio builder can render the same briefing. `news-http` owns queries against news tables; it may join company reference data to identify mentions and themes. Portfolio builder continues to query company reference data for its non-news work.

## Consumer

Add one synchronous HTTP client in portfolio builder, configured with a required `PORTFOLIO_BUILDER_NEWS_HTTP_BASE_URI` and a finite timeout. Wire it into the four news and graph tools and briefing loading. Keep the existing tool names and JSON result shapes where possible to avoid changing agent behavior. Remove news-table SQL, graph transactions, and the associated news-only helpers from portfolio builder. The existing PostgreSQL engine remains because portfolio storage and company reference reads still use it.

The client maps HTTP 404 on cluster detail to the current missing-cluster tool error. Empty successful responses flow through as empty data. Connection errors, timeouts, malformed responses, and server errors raise an explicit upstream-news error; they must not turn into empty analysis data or cause a portfolio to be saved from an incomplete briefing.

## Operations and verification

Configure the portfolio builder service to reach `news-http` in development and production Compose. Keep authentication and production deployment policy within issue 216's scope. Test endpoint response shapes and representative empty/missing cases, consumer HTTP error handling, and the existing portfolio tool and briefing behavior. Run formatter, lint, and affected test suites.
