# Market HTTP Theme API Design

Issue: #452. Related: #66, #317, #435.

## Scope

Themes are market reference data, so `market-http` serves them read-only from the PostgreSQL
tables `market-syncer` fills from Kiwoom `ka90001`/`ka90002`. Requests never call Kiwoom.

| Endpoint | Response | Order |
| --- | --- | --- |
| `GET /themes` | `Theme[]` (`code`, `name`) | theme `code` ascending |
| `GET /themes/{code}/stocks` | `ThemeStock[]` (`code`, `name`, `is_major`) | stock `code` ascending |
| `GET /stocks/{code}/themes` | `StockTheme[]` (`code`, `name`, `is_major`) | theme `code` ascending |

- Responses are bare JSON arrays. `theme_code`/`stock_code` columns map to `code`.
- `GET /themes` returns every theme with no search or pagination parameters.
- `GET /themes/{code}/stocks` keeps `limit` (1–1000, default 100) and `offset` (≥ 0).
  The `(theme_code, stock_code)` primary key and stock-code ordering keep pages free of
  duplicates and gaps; fewer than `limit` items means there is no next page.
- `is_major` is a property of the theme–stock relation and is identical in both directions.
- Membership covers all KOSPI stocks synced by market-syncer; it is not filtered to KOSPI 200.
- There is no `GET /themes/{code}` detail endpoint and no `synced_at` field.

## Errors

| Case | Status |
| --- | --- |
| Existing theme or stock without relations | `200 []` |
| Unknown theme code or stock code | `404` |
| Invalid path or `limit`/`offset` | `422` |
| PostgreSQL unavailable or failing | `503 theme data unavailable` |

## Time Boundary

`themes` and `theme_companies` are a current-state snapshot without membership history, and
`synced_at` cannot reconstruct past membership. These endpoints only answer "as of now".
Historical evaluation sessions (#435) must not present this snapshot as knowledge available at
a past decision time; restrict access there unless point-in-time correctness is proven.
Theme history and `as_of` queries are out of scope.
