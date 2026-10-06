# Portfolio builder news HTTP design

## Goal

Portfolio builder reads news through `news-http` and keeps direct PostgreSQL access for portfolios and company reference data, plus QuestDB access for prices. A missing news result remains distinguishable from a failed HTTP request.

## Read contract

Extend `news-http` with analysis-oriented endpoints for full-text cluster search, a cluster detail with its articles and extracted graph, a recent-news briefing, graph neighborhood search, and graph path search. Keep search limits, ordering, depth bounds, and truncation behavior that the current portfolio tools expose. Responses use names and concepts meaningful to the consumer rather than database table names. A cluster without a summary returns a missing result; a valid empty search or briefing returns an empty collection. The existing stock-cluster and cluster-article endpoints remain available.

The recent-news response contains updated clusters, mentioned companies, and their themes so portfolio builder can render the same briefing. `news-http` owns queries against news tables; it may join company reference data to identify mentions and themes. Portfolio builder continues to query company reference data for its non-news work.

## Consumer

Add one synchronous HTTP client in portfolio builder, configured with a required `PORTFOLIO_BUILDER_NEWS_HTTP_BASE_URI` and a finite timeout. Wire it into the four news and graph tools and briefing loading. Keep the existing tool names and JSON result shapes where possible to avoid changing agent behavior. Remove news-table SQL, graph transactions, and the associated news-only helpers from portfolio builder. The existing PostgreSQL engine remains because portfolio storage and company reference reads still use it.

The client maps HTTP 404 on cluster detail to the current missing-cluster tool error. Empty successful responses flow through as empty data. Connection errors, timeouts, malformed responses, and server errors raise an explicit upstream-news error; they must not turn into empty analysis data or cause a portfolio to be saved from an incomplete briefing.

## Operations and verification

Configure the portfolio builder service to reach `news-http` in development and production Compose. Keep authentication and production deployment policy within issue 216's scope. Test endpoint response shapes and representative empty/missing cases, consumer HTTP error handling, and the existing portfolio tool and briefing behavior. Run formatter, lint, and affected test suites.
