# portfolio-builder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Python `portfolio-builder` stub with a TypeScript/Bun cron service in which a Pi agent builds one versioned model portfolio from news clusters, the knowledge graph, themes and MCP-served technicals.

**Architecture:** `main.ts` loads settings, opens `Bun.sql` and an `openai-codex` `Models` backed by a file credential store, assembles a briefing from Postgres, and runs a `pi-agent-core` `Agent` with six tools. `submit_portfolio` validates in code, normalizes weights, and writes three tables in one transaction; every failure is returned to the agent until it succeeds or `MAX_TURNS` is reached. Schema changes stay in the Python Alembic migrations.

**Tech Stack:** Bun 1.4.2, TypeScript, `@earendil-works/pi-agent-core` 0.87.x, `@earendil-works/pi-ai` 0.87.x, `@modelcontextprotocol/sdk` 1.30.x, `typebox` 1.x, Biome; Python Alembic for migration `0005`.

**Spec:** `docs/superpowers/specs/2026-09-27-portfolio-builder-design.md`

## Global Constraints

- Runtime Bun `1.4.2`; Docker base `oven/bun:1.4.2-slim`; lockfile `bun.lock` (text), installed with `bun install --frozen-lockfile`.
- Env prefix `PORTFOLIO_BUILDER_`; required: `POSTGRES_DSN`, `MARKET_MCP_URL`, `LLM_MODEL`. Defaults: `THINKING_LEVEL=medium`, `CREDENTIALS_PATH=/data/auth.json`, `NEWS_WINDOW_DAYS=7`, `MAX_TURNS=150`, `LOG_LEVEL=INFO`. Seeds: `OPENAI_ACCESS_TOKEN`, `OPENAI_REFRESH_TOKEN`, `OPENAI_TOKEN_EXPIRES_EPOCH` (epoch ms).
- `PORTFOLIO_BUILDER_POSTGRES_DSN` is a plain `postgres://` URL (Bun.sql), not `postgresql+psycopg://`.
- LLM provider id `openai-codex`; model looked up with `models.getModel("openai-codex", LLM_MODEL)`.
- `company_id` is `companies.corp_code` (text). Holdings/exits reference it by FK.
- Tool names exactly: `analyze_technicals`, `get_news_cluster`, `search_news_cluster`, `search_graph`, `find_graph_paths`, `submit_portfolio`. MCP tool name `analyze_technicals` with argument `stock_code`.
- Previous portfolio = `ORDER BY created_at DESC, id DESC LIMIT 1`.
- Weights are normalized by code: each weight and cash divided by their total.
- Logs: one JSON object per line on stdout with `ts`, `level`, `run_id`, `event`. Never log tokens.
- Tests needing Postgres read `KTB_TEST_POSTGRES_DSN` (SQLAlchemy form; strip `+psycopg`) and are skipped when it is unset. They TRUNCATE tables: point them at `news_test`, never `news`.
- Repo conventions (AGENTS.md): no comments restating names; keep comments for a non-obvious *why*; no thin one-caller wrappers; commit titles use `feat`/`fix`/`refactor`/`chore`/`docs`/`style`; every commit ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Run Bun commands from `services/portfolio-builder/`; run `uv`/`alembic`/`pytest` from the repo root.

## File Structure

```
services/portfolio-builder/
  package.json  bun.lock  tsconfig.json  biome.json
  src/
    main.ts                 entry: wire settings, db, models, briefing, tools, agent; exit code
    settings.ts             env parsing
    log.ts                  JSON-lines logger
    agent.ts                Pi Agent run: turn limit, nudge, event logging, outcome
    credentials.ts          FileCredentialStore, seeding, openai-codex Models
    pg_array.ts             Postgres array literal
    portfolio/
      briefing.ts           ingestion queries, SYSTEM_PROMPT, briefing text
      validate_portfolio.ts pure validation + Submission types
      normalize_weights.ts  weight normalization
      save_portfolio.ts     transactional write, SaveError
    tools/
      normalize_company_name.ts
      analyze_technicals.ts  (+ connectMarketMcp)
      get_news_cluster.ts
      search_news_cluster.ts
      graph_seeds.ts         seed-entity lookup shared by both graph tools
      search_graph.ts
      find_graph_paths.ts
      submit_portfolio.ts
  tests/
    db.ts                   test DB handle, reset, seed fixture graph
    fixtures/normalize_cases.json
    *.test.ts
infrastructure/postgres/migrations/versions/0005_create_portfolios.py
infrastructure/postgres/tests/test_migrations.py            (add 0005 tests)
services/news-graph-builder/tests/common/test_normalize_parity.py
docker/portfolio-builder.Dockerfile                          (rewrite)
compose.dev.yaml, pyproject.toml, tach.toml, .dockerignore, .github/workflows/ci-dev.yaml,
.github/workflows/ci-main.yaml, AGENTS.md, README.md          (modify)
deleted: services/portfolio-builder/{pyproject.toml,src/portfolio_builder/**,tests/*.py},
         docker/requirements/portfolio-builder.txt
```

---

### Task 1: Replace the Python stub with a Bun package (settings + logger)

**Files:**
- Delete: `services/portfolio-builder/pyproject.toml`, `services/portfolio-builder/src/portfolio_builder/` (all), `services/portfolio-builder/tests/test_main.py`, `services/portfolio-builder/tests/test_settings.py`, `docker/requirements/portfolio-builder.txt`
- Modify: `pyproject.toml`, `tach.toml`, `.dockerignore`, `.gitignore`, `.github/workflows/ci-dev.yaml`, `.github/workflows/ci-main.yaml`, `uv.lock`
- Create: `services/portfolio-builder/{package.json,tsconfig.json,biome.json}`, `src/settings.ts`, `src/log.ts`, `tests/settings.test.ts`, `tests/log.test.ts`

**Interfaces:**
- Produces:
  - `type LogLevel = "DEBUG" | "INFO" | "WARNING" | "ERROR"`
  - `type Log = (event: string, fields?: Record<string, unknown>, level?: LogLevel) => void`
  - `createLog(runId: string, minLevel: LogLevel, write?: (line: string) => void): Log`
  - `type Settings = { postgresDsn: string; marketMcpUrl: string; llmModel: string; thinkingLevel: ThinkingLevel; openaiAccessToken?: string; openaiRefreshToken?: string; openaiTokenExpiresEpoch?: number; credentialsPath: string; newsWindowDays: number; maxTurns: number; logLevel: LogLevel }`
  - `loadSettings(env?: Record<string, string | undefined>): Settings` — throws `Error` naming every missing/invalid variable.

- [ ] **Step 1: Install Bun 1.4.2 if missing**

Run: `bun --version`
If it is not `1.4.2`: `curl -fsSL https://bun.sh/install | bash -s "bun-v1.4.2"` and reopen the shell.

- [ ] **Step 2: Remove the Python stub from the workspace**

```bash
git rm -r services/portfolio-builder/pyproject.toml services/portfolio-builder/src/portfolio_builder \
  services/portfolio-builder/tests/test_main.py services/portfolio-builder/tests/test_settings.py \
  docker/requirements/portfolio-builder.txt
```

In `pyproject.toml`, exclude the directory from the uv workspace (the `services/*` glob would otherwise demand a `pyproject.toml` there) and drop its pytest/ruff source root:

```toml
[tool.uv.workspace]
members = ["packages/*", "services/*"]
exclude = ["services/portfolio-builder"]
```

In `tach.toml` delete the `"services/portfolio-builder/src",` line from `source_roots` and delete the final block:

```toml
[[modules]]
path = "portfolio_builder"
depends_on = ["ktb_core", "ktb_market_analyzer"]
```

In `.dockerignore` and `.gitignore` add a line `node_modules/` (skip `.gitignore` if it already ignores `node_modules`; check with `grep -n node_modules .gitignore`).

In `.github/workflows/ci-dev.yaml`:
- `verify-exported-requirements`: change `for pkg in news-preprocessor news-clusterer news-graph-builder portfolio-builder; do` to `for pkg in news-preprocessor news-clusterer news-graph-builder; do`.
- `dependency-audit`: change the loop body's first line so non-Python members are skipped:

```bash
          for member in packages/* services/*; do
            [ -f "$member/pyproject.toml" ] || continue
```

In `.github/workflows/ci-main.yaml` apply the same `[ -f "$member/pyproject.toml" ] || continue` line to its `for member in packages/* services/*; do` loop.

Run: `uv lock && uv sync --all-packages --group migrations`
Expected: succeeds; `git diff --stat uv.lock` shows `portfolio-builder` removed.

- [ ] **Step 3: Create the package files**

`services/portfolio-builder/package.json`:

```json
{
  "name": "portfolio-builder",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "scripts": {
    "start": "bun src/main.ts",
    "check": "biome check . && tsc --noEmit",
    "test": "bun test"
  },
  "dependencies": {
    "@earendil-works/pi-agent-core": "^0.87.1",
    "@earendil-works/pi-ai": "^0.87.1",
    "@modelcontextprotocol/sdk": "^1.30.1",
    "typebox": "^1.3.34"
  },
  "devDependencies": {
    "@biomejs/biome": "^2.3.0",
    "@types/bun": "^1.4.2",
    "typescript": "^5.9.0"
  }
}
```

`services/portfolio-builder/tsconfig.json`:

```json
{
  "compilerOptions": {
    "lib": ["ESNext"],
    "target": "ESNext",
    "module": "Preserve",
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "verbatimModuleSyntax": true,
    "noEmit": true,
    "strict": true,
    "noUncheckedIndexedAccess": true,
    "skipLibCheck": true,
    "types": ["bun"]
  },
  "include": ["src", "tests"]
}
```

`services/portfolio-builder/biome.json`:

```json
{
  "formatter": { "indentStyle": "space", "lineWidth": 100 },
  "files": { "includes": ["src/**", "tests/**"] }
}
```

Run (in `services/portfolio-builder`): `bun install`
Expected: creates `bun.lock` and `node_modules/`. If the Biome major installed is not 2.x, run `bunx biome migrate --write` once.

- [ ] **Step 4: Write the failing tests**

`tests/log.test.ts`:

```ts
import { expect, test } from "bun:test";
import { createLog } from "../src/log.ts";

test("writes one JSON object per event with run id, level and fields", () => {
  const lines: string[] = [];
  const log = createLog("run-1", "INFO", (line) => lines.push(line));

  log("ingestion", { clusters: 3 });

  expect(lines).toHaveLength(1);
  const entry = JSON.parse(lines[0] as string);
  expect(entry).toMatchObject({ level: "INFO", run_id: "run-1", event: "ingestion", clusters: 3 });
  expect(typeof entry.ts).toBe("string");
});

test("drops events below the minimum level", () => {
  const lines: string[] = [];
  const log = createLog("run-1", "WARNING", (line) => lines.push(line));

  log("llm_request", {}, "INFO");
  log("validation_failed", {}, "WARNING");

  expect(lines.map((line) => JSON.parse(line).event)).toEqual(["validation_failed"]);
});
```

`tests/settings.test.ts`:

```ts
import { expect, test } from "bun:test";
import { loadSettings } from "../src/settings.ts";

const REQUIRED = {
  PORTFOLIO_BUILDER_POSTGRES_DSN: "postgres://ktb:ktb@localhost:5432/news",
  PORTFOLIO_BUILDER_MARKET_MCP_URL: "http://localhost:8000/mcp",
  PORTFOLIO_BUILDER_LLM_MODEL: "gpt-5.5",
};

test("applies defaults when only the required variables are set", () => {
  const settings = loadSettings(REQUIRED);

  expect(settings).toEqual({
    postgresDsn: REQUIRED.PORTFOLIO_BUILDER_POSTGRES_DSN,
    marketMcpUrl: REQUIRED.PORTFOLIO_BUILDER_MARKET_MCP_URL,
    llmModel: "gpt-5.5",
    thinkingLevel: "medium",
    openaiAccessToken: undefined,
    openaiRefreshToken: undefined,
    openaiTokenExpiresEpoch: undefined,
    credentialsPath: "/data/auth.json",
    newsWindowDays: 7,
    maxTurns: 150,
    logLevel: "INFO",
  });
});

test("reads every optional variable", () => {
  const settings = loadSettings({
    ...REQUIRED,
    PORTFOLIO_BUILDER_THINKING_LEVEL: "high",
    PORTFOLIO_BUILDER_OPENAI_ACCESS_TOKEN: "access",
    PORTFOLIO_BUILDER_OPENAI_REFRESH_TOKEN: "refresh",
    PORTFOLIO_BUILDER_OPENAI_TOKEN_EXPIRES_EPOCH: "1790000000000",
    PORTFOLIO_BUILDER_CREDENTIALS_PATH: "/tmp/auth.json",
    PORTFOLIO_BUILDER_NEWS_WINDOW_DAYS: "3",
    PORTFOLIO_BUILDER_MAX_TURNS: "20",
    PORTFOLIO_BUILDER_LOG_LEVEL: "DEBUG",
  });

  expect(settings).toMatchObject({
    thinkingLevel: "high",
    openaiAccessToken: "access",
    openaiRefreshToken: "refresh",
    openaiTokenExpiresEpoch: 1790000000000,
    credentialsPath: "/tmp/auth.json",
    newsWindowDays: 3,
    maxTurns: 20,
    logLevel: "DEBUG",
  });
});

test("names every missing required variable", () => {
  expect(() => loadSettings({})).toThrow(
    "PORTFOLIO_BUILDER_POSTGRES_DSN, PORTFOLIO_BUILDER_MARKET_MCP_URL, PORTFOLIO_BUILDER_LLM_MODEL",
  );
});

test.each([
  ["PORTFOLIO_BUILDER_MAX_TURNS", "0"],
  ["PORTFOLIO_BUILDER_NEWS_WINDOW_DAYS", "seven"],
  ["PORTFOLIO_BUILDER_OPENAI_TOKEN_EXPIRES_EPOCH", "soon"],
  ["PORTFOLIO_BUILDER_LOG_LEVEL", "LOUD"],
  ["PORTFOLIO_BUILDER_THINKING_LEVEL", "extreme"],
])("rejects an invalid %s", (name, value) => {
  expect(() => loadSettings({ ...REQUIRED, [name]: value })).toThrow(name);
});
```

- [ ] **Step 5: Run to verify they fail**

Run: `bun test`
Expected: FAIL — `Cannot find module '../src/log.ts'` / `'../src/settings.ts'`.

- [ ] **Step 6: Implement**

`src/log.ts`:

```ts
const LEVELS = { DEBUG: 10, INFO: 20, WARNING: 30, ERROR: 40 } as const;

export type LogLevel = keyof typeof LEVELS;
export type Log = (event: string, fields?: Record<string, unknown>, level?: LogLevel) => void;

export function isLogLevel(value: string): value is LogLevel {
  return value in LEVELS;
}

export function createLog(
  runId: string,
  minLevel: LogLevel,
  write: (line: string) => void = (line) => process.stdout.write(`${line}\n`),
): Log {
  return (event, fields = {}, level = "INFO") => {
    if (LEVELS[level] < LEVELS[minLevel]) return;
    write(JSON.stringify({ ts: new Date().toISOString(), level, run_id: runId, event, ...fields }));
  };
}
```

`src/settings.ts`:

```ts
import type { ThinkingLevel } from "@earendil-works/pi-agent-core";
import { isLogLevel, type LogLevel } from "./log.ts";

const PREFIX = "PORTFOLIO_BUILDER_";
const THINKING_LEVELS: readonly string[] = ["off", "minimal", "low", "medium", "high", "xhigh"];

export type Settings = {
  postgresDsn: string;
  marketMcpUrl: string;
  llmModel: string;
  thinkingLevel: ThinkingLevel;
  openaiAccessToken?: string;
  openaiRefreshToken?: string;
  openaiTokenExpiresEpoch?: number;
  credentialsPath: string;
  newsWindowDays: number;
  maxTurns: number;
  logLevel: LogLevel;
};

export function loadSettings(env: Record<string, string | undefined> = process.env): Settings {
  const errors: string[] = [];
  const get = (name: string) => env[PREFIX + name] || undefined;
  const required = (name: string) => {
    const value = get(name);
    if (value === undefined) errors.push(`${PREFIX}${name} is required`);
    return value ?? "";
  };
  const positiveInt = (name: string, fallback?: number) => {
    const raw = get(name);
    if (raw === undefined) return fallback;
    const value = Number(raw);
    if (!Number.isInteger(value) || value <= 0) errors.push(`${PREFIX}${name} must be a positive integer`);
    return value;
  };
  const oneOf = <T extends string>(name: string, allowed: (v: string) => v is T, fallback: T) => {
    const raw = get(name) ?? fallback;
    if (!allowed(raw)) errors.push(`${PREFIX}${name} has an invalid value ${JSON.stringify(raw)}`);
    return raw as T;
  };

  const postgresDsn = required("POSTGRES_DSN");
  const marketMcpUrl = required("MARKET_MCP_URL");
  const llmModel = required("LLM_MODEL");
  const missing = errors.splice(0);
  if (missing.length) {
    errors.push(`missing required settings: ${missing.map((m) => m.split(" ")[0]).join(", ")}`);
  }

  const settings: Settings = {
    postgresDsn,
    marketMcpUrl,
    llmModel,
    thinkingLevel: oneOf(
      "THINKING_LEVEL",
      (v): v is ThinkingLevel => THINKING_LEVELS.includes(v),
      "medium",
    ),
    openaiAccessToken: get("OPENAI_ACCESS_TOKEN"),
    openaiRefreshToken: get("OPENAI_REFRESH_TOKEN"),
    openaiTokenExpiresEpoch: positiveInt("OPENAI_TOKEN_EXPIRES_EPOCH"),
    credentialsPath: get("CREDENTIALS_PATH") ?? "/data/auth.json",
    newsWindowDays: positiveInt("NEWS_WINDOW_DAYS", 7) as number,
    maxTurns: positiveInt("MAX_TURNS", 150) as number,
    logLevel: oneOf("LOG_LEVEL", isLogLevel, "INFO"),
  };
  if (errors.length) throw new Error(errors.join("; "));
  return settings;
}
```

- [ ] **Step 7: Run tests and checks**

Run: `bun test && bun run check`
Expected: all tests PASS; Biome and `tsc` report no errors (run `bunx biome check --write .` first if only formatting differs).

Run (repo root): `uv run ruff check . && uv run tach check && uv run pytest -q`
Expected: PASS (nothing references `portfolio_builder` any more).

- [ ] **Step 8: Commit**

```bash
git add -A pyproject.toml tach.toml uv.lock .dockerignore .gitignore .github docker/requirements services/portfolio-builder
git commit -m "refactor: replace the Python portfolio-builder stub with a Bun package

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Migration 0005 — portfolio tables and cluster full-text index

**Files:**
- Create: `infrastructure/postgres/migrations/versions/0005_create_portfolios.py`
- Modify: `infrastructure/postgres/tests/test_migrations.py` (append tests)

**Interfaces:**
- Produces tables `portfolios(id, created_at, cash_weight, commentary, model)`, `portfolio_holdings(portfolio_id, company_id, weight, reason, cited_cluster_ids)`, `portfolio_exits(portfolio_id, company_id, reason, cited_cluster_ids)`, index `portfolios_created_at_idx`, index `cluster_summaries_fts_idx`.

- [ ] **Step 1: Write the failing tests** (append to `infrastructure/postgres/tests/test_migrations.py`)

```python
def test_portfolio_rows_reference_companies_and_cascade_from_portfolios(
    pg_dsn, pg_engine, monkeypatch
):
    monkeypatch.setenv("KTB_POSTGRES_DSN", pg_dsn)
    command.upgrade(_alembic_config(), "head")
    try:
        with pg_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO companies (corp_code, stock_code, corp_name)"
                    " VALUES ('00126380', '005930', '삼성전자')"
                )
            )
            portfolio_id = conn.execute(
                sa.text(
                    "INSERT INTO portfolios (cash_weight, commentary, model)"
                    " VALUES (0.2, '총평', 'openai-codex/gpt-5.5') RETURNING id"
                )
            ).scalar_one()
            conn.execute(
                sa.text(
                    "INSERT INTO portfolio_holdings (portfolio_id, company_id, weight, reason)"
                    " VALUES (:p, '00126380', 0.8, '편입 사유')"
                ),
                {"p": portfolio_id},
            )
            holding = conn.execute(
                sa.text("SELECT cited_cluster_ids FROM portfolio_holdings")
            ).scalar_one()
        assert holding == []

        with pytest.raises(sa.exc.IntegrityError), pg_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO portfolio_exits (portfolio_id, company_id, reason)"
                    " VALUES (:p, '99999999', '없는 회사')"
                ),
                {"p": portfolio_id},
            )

        with pg_engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM portfolios"))
            remaining = conn.execute(sa.text("SELECT count(*) FROM portfolio_holdings")).scalar_one()
        assert remaining == 0
    finally:
        with pg_engine.begin() as conn:
            conn.execute(sa.text("TRUNCATE portfolios, companies CASCADE"))


def test_cluster_summaries_have_a_full_text_index(pg_dsn, pg_engine, monkeypatch):
    monkeypatch.setenv("KTB_POSTGRES_DSN", pg_dsn)
    command.upgrade(_alembic_config(), "head")
    with pg_engine.connect() as conn:
        definition = conn.execute(
            sa.text(
                "SELECT indexdef FROM pg_indexes WHERE indexname = 'cluster_summaries_fts_idx'"
            )
        ).scalar_one()
    assert "to_tsvector('simple'" in definition


def test_downgrade_to_0004_removes_the_portfolio_tables(pg_dsn, pg_engine, monkeypatch):
    monkeypatch.setenv("KTB_POSTGRES_DSN", pg_dsn)
    config = _alembic_config()

    command.downgrade(config, "0004")
    with pg_engine.connect() as conn:
        assert conn.execute(sa.text("SELECT to_regclass('portfolios')")).scalar() is None
        assert (
            conn.execute(sa.text("SELECT to_regclass('cluster_summaries_fts_idx')")).scalar()
            is None
        )

    command.upgrade(config, "head")
    with pg_engine.connect() as conn:
        assert conn.execute(sa.text("SELECT to_regclass('portfolios')")).scalar() is not None
```

- [ ] **Step 2: Run to verify they fail**

Run: `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/news_test uv run pytest infrastructure/postgres/tests/test_migrations.py -q`
Expected: the three new tests FAIL (`relation "portfolios" does not exist` / `NoResultFound`). (Start dev Postgres first: `docker compose -f compose.dev.yaml up -d postgres` and create `news_test` as in AGENTS.md.)

- [ ] **Step 3: Write the migration**

`infrastructure/postgres/migrations/versions/0005_create_portfolios.py`:

```python
"""create model portfolio tables and full-text search on cluster summaries

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-27
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "portfolios",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("cash_weight", sa.Double, nullable=False),
        sa.Column("commentary", sa.Text, nullable=False),
        sa.Column("model", sa.Text, nullable=False),
    )
    op.create_index("portfolios_created_at_idx", "portfolios", ["created_at"])
    op.create_table(
        "portfolio_holdings",
        sa.Column(
            "portfolio_id",
            sa.BigInteger,
            sa.ForeignKey("portfolios.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "company_id", sa.Text, sa.ForeignKey("companies.corp_code"), primary_key=True
        ),
        sa.Column("weight", sa.Double, nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column(
            "cited_cluster_ids",
            postgresql.ARRAY(sa.BigInteger),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.create_table(
        "portfolio_exits",
        sa.Column(
            "portfolio_id",
            sa.BigInteger,
            sa.ForeignKey("portfolios.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "company_id", sa.Text, sa.ForeignKey("companies.corp_code"), primary_key=True
        ),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column(
            "cited_cluster_ids",
            postgresql.ARRAY(sa.BigInteger),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    # The expression must match search_news_cluster's query exactly or the planner ignores it.
    op.execute(
        "CREATE INDEX cluster_summaries_fts_idx ON cluster_summaries"
        " USING gin (to_tsvector('simple', title || ' ' || summary))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX cluster_summaries_fts_idx")
    op.drop_table("portfolio_exits")
    op.drop_table("portfolio_holdings")
    op.drop_table("portfolios")
```

- [ ] **Step 4: Run tests**

Run: `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/news_test uv run pytest infrastructure/postgres/tests/test_migrations.py -q && uv run ruff check . && uv run ruff format --check .`
Expected: PASS (all migration tests, including the existing ones).

- [ ] **Step 5: Commit**

```bash
git add infrastructure/postgres
git commit -m "feat: migration 0005 for model portfolios and cluster full-text search

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Test database fixture and `pg_array`

**Files:**
- Create: `services/portfolio-builder/src/pg_array.ts`, `tests/db.ts`, `tests/pg_array.test.ts`

**Interfaces:**
- Produces:
  - `pgArray(values: readonly (string | number)[]): string` — a Postgres array literal, used as `${pgArray(ids)}::bigint[]` or `::text[]`.
  - `tests/db.ts`: `hasDb: boolean`, `testSql(): SQL`, `resetTables(sql: SQL): Promise<void>`, `seedFixture(sql: SQL): Promise<void>`, and constants `SAMSUNG = "00126380"`, `HYNIX = "00164779"`, `LGES = "01515323"`.

Fixture graph (entity ids fixed with `OVERRIDING SYSTEM VALUE`):

```
companies: 00126380 삼성전자 005930 | 00164779 SK하이닉스 000660 | 01515323 LG에너지솔루션 373220
aliases:   삼성전자→00126380, sk하이닉스→00164779, lg에너지솔루션→01515323
clusters:  1 (updated now), 2 (updated now - 30 days)
summaries: 1 "삼성전자 HBM 공급 확대" / "삼성전자가 엔비디아에 HBM을 공급한다."
           2 "반도체 수출 둔화" / "SK하이닉스가 HBM 생산을 늘린다."
articles:  1,2 → cluster 1; 3 → cluster 2
entities:  1 삼성전자/company/00126380, 2 엔비디아/company/null, 3 SK하이닉스/company/00164779, 4 HBM/product/null
cluster_entities: (1,1) (1,2) (1,4) (2,3) (2,4)
relations: 1: 1→2 supplies (cluster 1), 2: 4→2 used_by (cluster 1), 3: 3→4 produces (cluster 2)
themes:    'T1' HBM; theme_companies (T1, 00126380, main) (T1, 00164779, not main)
```

Path 삼성전자 → SK하이닉스 is `1 -supplies→ 2 ←used_by- 4 ←produces- 3` (3 hops).

- [ ] **Step 1: Write the failing test**

`tests/pg_array.test.ts`:

```ts
import { expect, test } from "bun:test";
import { pgArray } from "../src/pg_array.ts";

test("quotes every element and escapes quotes and backslashes", () => {
  expect(pgArray([1, 2])).toBe('{"1","2"}');
  expect(pgArray(['a"b', "c\\d"])).toBe('{"a\\"b","c\\\\d"}');
  expect(pgArray([])).toBe("{}");
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `bun test tests/pg_array.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

`src/pg_array.ts`:

```ts
// Bun.sql's sql.array() does not document empty arrays; a text literal cast in SQL always works.
export function pgArray(values: readonly (string | number)[]): string {
  const quoted = values.map((v) => `"${String(v).replaceAll("\\", "\\\\").replaceAll('"', '\\"')}"`);
  return `{${quoted.join(",")}}`;
}
```

`tests/db.ts`:

```ts
import { SQL } from "bun";

const dsn = process.env.KTB_TEST_POSTGRES_DSN?.replace("+psycopg", "");

export const hasDb = Boolean(dsn);
export const SAMSUNG = "00126380";
export const HYNIX = "00164779";
export const LGES = "01515323";

let shared: SQL | undefined;

export function testSql(): SQL {
  if (!dsn) throw new Error("KTB_TEST_POSTGRES_DSN is not set");
  shared ??= new SQL(dsn);
  return shared;
}

export async function resetTables(sql: SQL): Promise<void> {
  await sql`TRUNCATE portfolios, portfolio_holdings, portfolio_exits, relations,
    cluster_entities, entities, cluster_summaries, article_clusters, clusters, articles,
    theme_companies, themes, company_aliases, companies RESTART IDENTITY CASCADE`;
}

export async function seedFixture(sql: SQL): Promise<void> {
  await resetTables(sql);
  await sql`INSERT INTO companies (corp_code, stock_code, corp_name) VALUES
    (${SAMSUNG}, '005930', '삼성전자'), (${HYNIX}, '000660', 'SK하이닉스'),
    (${LGES}, '373220', 'LG에너지솔루션')`;
  await sql`INSERT INTO company_aliases (alias, corp_code) VALUES
    ('삼성전자', ${SAMSUNG}), ('sk하이닉스', ${HYNIX}), ('lg에너지솔루션', ${LGES})`;
  await sql`INSERT INTO clusters (id, updated_at) OVERRIDING SYSTEM VALUE VALUES
    (1, now()), (2, now() - interval '30 days')`;
  await sql`INSERT INTO cluster_summaries (cluster_id, title, summary, cluster_updated_at) VALUES
    (1, '삼성전자 HBM 공급 확대', '삼성전자가 엔비디아에 HBM을 공급한다.', now()),
    (2, '반도체 수출 둔화', 'SK하이닉스가 HBM 생산을 늘린다.', now() - interval '30 days')`;
  await sql`INSERT INTO articles (id, source, external_id, url, title, body, published_at, raw_payload)
    OVERRIDING SYSTEM VALUE VALUES
    (1, 'yonhap', 'a1', 'https://example.com/1', '삼성 HBM 공급', '본문', now(), '{}'),
    (2, 'yonhap', 'a2', 'https://example.com/2', '엔비디아 HBM 조달', '본문', now(), '{}'),
    (3, 'yonhap', 'a3', 'https://example.com/3', '하이닉스 증산', '본문', now() - interval '30 days', '{}')`;
  await sql`INSERT INTO article_clusters (article_id, cluster_id) VALUES (1, 1), (2, 1), (3, 2)`;
  await sql`INSERT INTO entities (id, raw_name, name, type, corp_code) OVERRIDING SYSTEM VALUE VALUES
    (1, '삼성전자', '삼성전자', 'company', ${SAMSUNG}),
    (2, '엔비디아', '엔비디아', 'company', NULL),
    (3, 'SK하이닉스', 'sk하이닉스', 'company', ${HYNIX}),
    (4, 'HBM', 'hbm', 'product', NULL)`;
  await sql`INSERT INTO cluster_entities (cluster_id, entity_id) VALUES (1, 1), (1, 2), (1, 4), (2, 3), (2, 4)`;
  await sql`INSERT INTO relations (id, cluster_id, source_entity_id, target_entity_id, type, description)
    OVERRIDING SYSTEM VALUE VALUES
    (1, 1, 1, 2, 'supplies', '삼성전자가 엔비디아에 HBM을 공급'),
    (2, 1, 4, 2, 'used_by', 'HBM은 엔비디아 GPU에 쓰인다'),
    (3, 2, 3, 4, 'produces', 'SK하이닉스가 HBM을 생산')`;
  await sql`INSERT INTO themes (theme_code, name) VALUES ('T1', 'HBM')`;
  await sql`INSERT INTO theme_companies (theme_code, corp_code, is_main) VALUES
    ('T1', ${SAMSUNG}, true), ('T1', ${HYNIX}, false)`;
}
```

- [ ] **Step 4: Run tests**

Run: `bun test tests/pg_array.test.ts && bun run check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/portfolio-builder
git commit -m "chore: portfolio-builder test fixture graph and pg array literal

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `normalize_company_name` with a shared Python/TS case file

**Files:**
- Create: `services/portfolio-builder/src/tools/normalize_company_name.ts`, `tests/fixtures/normalize_cases.json`, `tests/normalize_company_name.test.ts`, `services/news-graph-builder/tests/common/test_normalize_parity.py`

**Interfaces:**
- Produces: `normalizeCompanyName(text: string): string`

- [ ] **Step 1: Write the case file and failing tests**

`tests/fixtures/normalize_cases.json` (Python's `casefold` and JS `toLowerCase` differ only outside Hangul/ASCII, e.g. `ß`, so cases stay within those):

```json
[
  ["삼성전자", "삼성전자"],
  ["(주)삼성전자", "삼성전자"],
  ["㈜ 삼성 전자", "삼성전자"],
  ["삼성전자 주식회사", "삼성전자"],
  ["SK하이닉스", "sk하이닉스"],
  ["ＳＫ하이닉스", "sk하이닉스"],
  ["LG Energy Solution", "lgenergysolution"],
  ["  NAVER\t", "naver"]
]
```

`tests/normalize_company_name.test.ts`:

```ts
import { expect, test } from "bun:test";
import cases from "./fixtures/normalize_cases.json";
import { normalizeCompanyName } from "../src/tools/normalize_company_name.ts";

test.each(cases as [string, string][])("normalizes %p to %p", (raw, expected) => {
  expect(normalizeCompanyName(raw)).toBe(expected);
});
```

`services/news-graph-builder/tests/common/test_normalize_parity.py`:

```python
import json
import pathlib

import pytest
from news_graph_builder.common import normalize

REPO_ROOT = next(
    parent
    for parent in pathlib.Path(__file__).resolve().parents
    if (parent / "alembic.ini").is_file()
)
# portfolio-builder normalizes names the same way to look up company_aliases.
CASES = json.loads(
    (REPO_ROOT / "services/portfolio-builder/tests/fixtures/normalize_cases.json").read_text()
)


@pytest.mark.parametrize(("raw", "expected"), CASES)
def test_matches_the_shared_cases(raw, expected):
    assert normalize(raw) == expected
```

- [ ] **Step 2: Run to verify**

Run: `bun test tests/normalize_company_name.test.ts`
Expected: FAIL — module not found.
Run (repo root): `uv run pytest services/news-graph-builder/tests/common/test_normalize_parity.py -q`
Expected: PASS already (it pins the Python behaviour). If any case fails, the case file is wrong — fix the case, not Python.

- [ ] **Step 3: Implement**

`src/tools/normalize_company_name.ts`:

```ts
const CORPORATE_MARKERS = /\(주\)|㈜|주식회사/g;

// Mirrors news-graph-builder's common/normalize.py; company_aliases stores names in this form.
export function normalizeCompanyName(text: string): string {
  return text.normalize("NFKC").replace(CORPORATE_MARKERS, "").replace(/\s+/g, "").toLowerCase();
}
```

- [ ] **Step 4: Run tests**

Run: `bun test tests/normalize_company_name.test.ts && bun run check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/portfolio-builder services/news-graph-builder/tests/common/test_normalize_parity.py
git commit -m "feat: company name normalization shared with news-graph-builder

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: `validate_portfolio` and `normalize_weights`

**Files:**
- Create: `src/portfolio/validate_portfolio.ts`, `src/portfolio/normalize_weights.ts`, `tests/validate_portfolio.test.ts`, `tests/normalize_weights.test.ts`

**Interfaces:**
- Produces:
  - `type Holding = { company_id: string; weight: number; reason?: string; cited_cluster_ids: number[] }`
  - `type Exit = { company_id: string; reason: string; cited_cluster_ids: number[] }`
  - `type Submission = { holdings: Holding[]; exits: Exit[]; cash_weight: number; commentary: string }`
  - `validatePortfolio(submission: Submission, previous: ReadonlySet<string>): string[]` — empty when valid; reads nothing.
  - `normalizeWeights(submission: Submission): Submission`

- [ ] **Step 1: Write the failing tests**

`tests/normalize_weights.test.ts`:

```ts
import { expect, test } from "bun:test";
import { normalizeWeights } from "../src/portfolio/normalize_weights.ts";

test("scales weights and cash so they sum to one", () => {
  const result = normalizeWeights({
    holdings: [
      { company_id: "a", weight: 0.1, cited_cluster_ids: [] },
      { company_id: "b", weight: 0.1, cited_cluster_ids: [] },
    ],
    exits: [],
    cash_weight: 0.05,
    commentary: "c",
  });

  expect(result.holdings.map((h) => h.weight)).toEqual([0.4, 0.4]);
  expect(result.cash_weight).toBeCloseTo(0.2);
});
```

`tests/validate_portfolio.test.ts`:

```ts
import { expect, test } from "bun:test";
import { type Submission, validatePortfolio } from "../src/portfolio/validate_portfolio.ts";

const base: Submission = {
  holdings: [{ company_id: "A", weight: 1, reason: "news", cited_cluster_ids: [1] }],
  exits: [],
  cash_weight: 0,
  commentary: "overall",
};

test("accepts a first portfolio whose entries all have reasons", () => {
  expect(validatePortfolio(base, new Set())).toEqual([]);
});

test("a held company keeps its place without a new reason", () => {
  const submission = { ...base, holdings: [{ company_id: "A", weight: 1, cited_cluster_ids: [] }] };
  expect(validatePortfolio(submission, new Set(["A"]))).toEqual([]);
});

test("reports every problem at once", () => {
  const submission: Submission = {
    holdings: [
      { company_id: "A", weight: -1, cited_cluster_ids: [] },
      { company_id: "A", weight: 0, reason: "dup", cited_cluster_ids: [] },
      { company_id: "C", weight: 0, reason: "  ", cited_cluster_ids: [] },
    ],
    exits: [
      { company_id: "A", reason: "both", cited_cluster_ids: [] },
      { company_id: "Z", reason: "", cited_cluster_ids: [] },
    ],
    cash_weight: -0.5,
    commentary: " ",
  };

  expect(validatePortfolio(submission, new Set(["B"]))).toEqual([
    "holdings: A weight must be >= 0",
    "holdings: A is entering the portfolio and needs a reason",
    "holdings: A appears more than once",
    "holdings: C is entering the portfolio and needs a reason",
    "exits: A is both held and exited",
    "exits: A was not in the previous portfolio",
    "exits: Z was not in the previous portfolio",
    "exits: Z needs a reason",
    "exits: B was held and is dropped, so it needs an exit with a reason",
    "cash_weight must be >= 0",
    "weights and cash_weight sum to 0 or less; at least one must be positive",
    "commentary must not be empty",
  ]);
});

test("an exited previous holding needs only its exit", () => {
  const submission: Submission = {
    ...base,
    exits: [{ company_id: "B", reason: "guidance cut", cited_cluster_ids: [2] }],
  };
  expect(validatePortfolio(submission, new Set(["B"]))).toEqual([]);
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `bun test tests/validate_portfolio.test.ts tests/normalize_weights.test.ts`
Expected: FAIL — modules not found.

- [ ] **Step 3: Implement**

`src/portfolio/validate_portfolio.ts`:

```ts
export type Holding = {
  company_id: string;
  weight: number;
  reason?: string;
  cited_cluster_ids: number[];
};
export type Exit = { company_id: string; reason: string; cited_cluster_ids: number[] };
export type Submission = {
  holdings: Holding[];
  exits: Exit[];
  cash_weight: number;
  commentary: string;
};

export function validatePortfolio(submission: Submission, previous: ReadonlySet<string>): string[] {
  const errors: string[] = [];
  const held = new Set<string>();
  for (const holding of submission.holdings) {
    const id = holding.company_id;
    if (!(holding.weight >= 0)) errors.push(`holdings: ${id} weight must be >= 0`);
    if (!previous.has(id) && !holding.reason?.trim()) {
      errors.push(`holdings: ${id} is entering the portfolio and needs a reason`);
    }
    if (held.has(id)) errors.push(`holdings: ${id} appears more than once`);
    held.add(id);
  }

  const exited = new Set<string>();
  for (const exit of submission.exits) {
    const id = exit.company_id;
    if (exited.has(id)) errors.push(`exits: ${id} appears more than once`);
    exited.add(id);
    if (held.has(id)) errors.push(`exits: ${id} is both held and exited`);
    if (!previous.has(id)) errors.push(`exits: ${id} was not in the previous portfolio`);
    if (!exit.reason.trim()) errors.push(`exits: ${id} needs a reason`);
  }
  for (const id of previous) {
    if (!held.has(id) && !exited.has(id)) {
      errors.push(`exits: ${id} was held and is dropped, so it needs an exit with a reason`);
    }
  }

  if (!(submission.cash_weight >= 0)) errors.push("cash_weight must be >= 0");
  const total =
    submission.holdings.reduce((sum, h) => sum + h.weight, 0) + submission.cash_weight;
  if (!(total > 0)) {
    errors.push("weights and cash_weight sum to 0 or less; at least one must be positive");
  }
  if (!submission.commentary.trim()) errors.push("commentary must not be empty");
  return errors;
}
```

`src/portfolio/normalize_weights.ts`:

```ts
import type { Submission } from "./validate_portfolio.ts";

export function normalizeWeights(submission: Submission): Submission {
  const total =
    submission.holdings.reduce((sum, h) => sum + h.weight, 0) + submission.cash_weight;
  return {
    ...submission,
    holdings: submission.holdings.map((h) => ({ ...h, weight: h.weight / total })),
    cash_weight: submission.cash_weight / total,
  };
}
```

- [ ] **Step 4: Run tests**

Run: `bun test tests/validate_portfolio.test.ts tests/normalize_weights.test.ts && bun run check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/portfolio-builder
git commit -m "feat: validate and normalize submitted portfolios

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: `save_portfolio` — one transaction, checks at write time

**Files:**
- Create: `src/portfolio/save_portfolio.ts`, `tests/save_portfolio.test.ts`

**Interfaces:**
- Consumes: `Submission` (Task 5), `pgArray` (Task 3), `tests/db.ts` (Task 3)
- Produces: `class SaveError extends Error { errors: string[] }`, `savePortfolio(sql: SQL, submission: Submission, model: string): Promise<number>` — returns the new portfolio id; throws `SaveError` with agent-readable messages for an unknown `company_id` (FK) or unknown cited cluster ids; other errors propagate.

- [ ] **Step 1: Write the failing tests**

`tests/save_portfolio.test.ts`:

```ts
import { beforeEach, describe, expect, test } from "bun:test";
import { SaveError, savePortfolio } from "../src/portfolio/save_portfolio.ts";
import type { Submission } from "../src/portfolio/validate_portfolio.ts";
import { hasDb, HYNIX, SAMSUNG, seedFixture, testSql } from "./db.ts";

describe.skipIf(!hasDb)("savePortfolio", () => {
  const sql = hasDb ? testSql() : (undefined as never);
  beforeEach(() => seedFixture(sql));

  const submission: Submission = {
    holdings: [
      { company_id: SAMSUNG, weight: 0.6, reason: "HBM 공급", cited_cluster_ids: [1] },
      { company_id: HYNIX, weight: 0.2, cited_cluster_ids: [] },
    ],
    exits: [],
    cash_weight: 0.2,
    commentary: "총평",
  };

  test("writes the portfolio, its holdings and citations", async () => {
    const id = await savePortfolio(sql, submission, "openai-codex/gpt-5.5");

    const [portfolio] = await sql`SELECT cash_weight, commentary, model FROM portfolios WHERE id = ${id}`;
    expect(portfolio).toEqual({ cash_weight: 0.2, commentary: "총평", model: "openai-codex/gpt-5.5" });
    const holdings = await sql`SELECT company_id, weight, reason, cited_cluster_ids
      FROM portfolio_holdings WHERE portfolio_id = ${id} ORDER BY company_id`;
    expect(holdings.map((h: { cited_cluster_ids: unknown[] }) => ({
      ...h,
      cited_cluster_ids: h.cited_cluster_ids.map(Number),
    }))).toEqual([
      { company_id: SAMSUNG, weight: 0.6, reason: "HBM 공급", cited_cluster_ids: [1] },
      { company_id: HYNIX, weight: 0.2, reason: null, cited_cluster_ids: [] },
    ]);
  });

  test("an unknown company rolls everything back with a readable error", async () => {
    const bad = { ...submission, holdings: [{ company_id: "99999999", weight: 1, reason: "x", cited_cluster_ids: [] }] };

    const error = await savePortfolio(sql, bad, "m").catch((e) => e);

    expect(error).toBeInstanceOf(SaveError);
    expect((error as SaveError).errors.join()).toContain("99999999");
    const [{ count }] = await sql`SELECT count(*)::int AS count FROM portfolios`;
    expect(count).toBe(0);
  });

  test("an unknown cited cluster rolls everything back", async () => {
    const bad = { ...submission, exits: [], holdings: [{ company_id: SAMSUNG, weight: 1, reason: "x", cited_cluster_ids: [1, 404] }] };

    const error = await savePortfolio(sql, bad, "m").catch((e) => e);

    expect((error as SaveError).errors).toEqual(["cited_cluster_ids not found: 404"]);
    const [{ count }] = await sql`SELECT count(*)::int AS count FROM portfolios`;
    expect(count).toBe(0);
  });
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/news_test bun test tests/save_portfolio.test.ts`
Expected: FAIL — module not found. (`news_test` must be migrated to head: `KTB_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/news_test uv run alembic upgrade head` from the repo root.)

- [ ] **Step 3: Implement**

`src/portfolio/save_portfolio.ts`:

```ts
import { SQL } from "bun";
import { pgArray } from "../pg_array.ts";
import type { Submission } from "./validate_portfolio.ts";

export class SaveError extends Error {
  constructor(readonly errors: string[]) {
    super(errors.join("; "));
  }
}

export async function savePortfolio(sql: SQL, submission: Submission, model: string): Promise<number> {
  const cited = [
    ...new Set([...submission.holdings, ...submission.exits].flatMap((x) => x.cited_cluster_ids)),
  ];
  try {
    return await sql.begin(async (tx) => {
      const [portfolio] = await tx`INSERT INTO portfolios (cash_weight, commentary, model)
        VALUES (${submission.cash_weight}, ${submission.commentary}, ${model}) RETURNING id`;
      const id = Number(portfolio.id);
      for (const h of submission.holdings) {
        await tx`INSERT INTO portfolio_holdings (portfolio_id, company_id, weight, reason, cited_cluster_ids)
          VALUES (${id}, ${h.company_id}, ${h.weight}, ${h.reason ?? null},
                  ${pgArray(h.cited_cluster_ids)}::bigint[])`;
      }
      for (const e of submission.exits) {
        await tx`INSERT INTO portfolio_exits (portfolio_id, company_id, reason, cited_cluster_ids)
          VALUES (${id}, ${e.company_id}, ${e.reason}, ${pgArray(e.cited_cluster_ids)}::bigint[])`;
      }
      // An array column cannot carry a foreign key, so citations are checked here instead.
      const found = await tx`SELECT id FROM clusters WHERE id = ANY(${pgArray(cited)}::bigint[])`;
      const foundIds = new Set(found.map((row: { id: unknown }) => Number(row.id)));
      const missing = cited.filter((c) => !foundIds.has(c));
      if (missing.length) throw new SaveError([`cited_cluster_ids not found: ${missing.join(", ")}`]);
      return id;
    });
  } catch (error) {
    if (error instanceof SaveError) throw error;
    if (error instanceof SQL.PostgresError) throw new SaveError([error.detail ?? error.message]);
    throw error;
  }
}
```

- [ ] **Step 4: Run tests**

Run: `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/news_test bun test tests/save_portfolio.test.ts && bun run check`
Expected: PASS. If `portfolio.id` or `cash_weight` come back as strings, keep the `Number(...)` conversions and adjust only the test's expected shapes — do not change the schema.

- [ ] **Step 5: Commit**

```bash
git add services/portfolio-builder
git commit -m "feat: save portfolios atomically with write-time company and cluster checks

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Briefing and system prompt

**Files:**
- Create: `src/portfolio/briefing.ts`, `tests/briefing.test.ts`

**Interfaces:**
- Consumes: `pgArray`, `tests/db.ts`
- Produces:
  - `SYSTEM_PROMPT: string`
  - `type Briefing = { previousPortfolioId: number | null; previousCompanyIds: Set<string>; previousHoldings: number; previousExits: number; clusterIds: number[]; companyCount: number; themeCount: number; text: string }`
  - `loadBriefing(sql: SQL, newsWindowDays: number): Promise<Briefing>`

- [ ] **Step 1: Write the failing tests**

`tests/briefing.test.ts`:

```ts
import { beforeEach, describe, expect, test } from "bun:test";
import { loadBriefing, SYSTEM_PROMPT } from "../src/portfolio/briefing.ts";
import { savePortfolio } from "../src/portfolio/save_portfolio.ts";
import { hasDb, HYNIX, SAMSUNG, seedFixture, testSql } from "./db.ts";

test("the system prompt states the goal and the grounding rule", () => {
  expect(SYSTEM_PROMPT).toContain("model portfolio");
  expect(SYSTEM_PROMPT).toContain("pre-trained knowledge");
  expect(SYSTEM_PROMPT).toContain("submit_portfolio");
});

describe.skipIf(!hasDb)("loadBriefing", () => {
  const sql = hasDb ? testSql() : (undefined as never);
  beforeEach(() => seedFixture(sql));

  test("first run: only clusters inside the window, with their companies and themes", async () => {
    const briefing = await loadBriefing(sql, 7);

    expect(briefing.previousPortfolioId).toBeNull();
    expect(briefing.previousCompanyIds.size).toBe(0);
    expect(briefing.clusterIds).toEqual([1]);
    expect(briefing.companyCount).toBe(1);
    expect(briefing.themeCount).toBe(1);
    expect(briefing.text).toContain("first portfolio");
    expect(briefing.text).toContain("[cluster 1] 삼성전자 HBM 공급 확대");
    expect(briefing.text).toContain(`삼성전자 (company_id ${SAMSUNG}, stock_code 005930)`);
    expect(briefing.text).toContain("HBM (main)");
    expect(briefing.text).not.toContain("반도체 수출 둔화");
  });

  test("uses the most recently created portfolio as the previous one", async () => {
    await savePortfolio(sql, { holdings: [{ company_id: HYNIX, weight: 1, reason: "old", cited_cluster_ids: [] }], exits: [], cash_weight: 0, commentary: "older" }, "m");
    const latest = await savePortfolio(sql, { holdings: [{ company_id: SAMSUNG, weight: 1, reason: "new", cited_cluster_ids: [1] }], exits: [], cash_weight: 0, commentary: "newer" }, "m");

    const briefing = await loadBriefing(sql, 7);

    expect(briefing.previousPortfolioId).toBe(latest);
    expect([...briefing.previousCompanyIds]).toEqual([SAMSUNG]);
    expect(briefing.text).toContain("newer");
    expect(briefing.text).not.toContain("older");
  });
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/news_test bun test tests/briefing.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

`src/portfolio/briefing.ts`:

```ts
import type { SQL } from "bun";
import { pgArray } from "../pg_array.ts";

export const SYSTEM_PROMPT = `You are the portfolio manager of one model portfolio of KOSPI stocks that every user of this service follows.

Goal: each run, review the previous portfolio against what has happened in the news since, and decide what to hold, at what relative weight, what to drop and how much to keep in cash. Then call submit_portfolio with the new portfolio and its reasons.

Grounding: the portfolio must carry reasons, and every stored reason must originate in the briefing or in a tool result from this run: a news cluster (cite its cluster_id), a graph relation, or a technical analysis. You may use your pre-trained knowledge while thinking (to interpret events, relate industries, decide what to look up), but a fact you know only from memory cannot be the basis of a stored reason; find it in the data with a tool first.

Rules:
- Identify companies by company_id as shown in the briefing and tool results.
- Weights are relative and non-negative; the system scales holdings and cash_weight so they sum to 1.
- Every company not in the previous portfolio needs a reason.
- Every previous holding you drop needs an entry in exits with a reason.
- Cite the cluster_ids each decision relies on in cited_cluster_ids.
- Write a commentary covering the portfolio as a whole and this run's decisions.
- If submit_portfolio returns errors, fix every one and call it again.

Tools: get_news_cluster and search_news_cluster read news clusters; search_graph and find_graph_paths explore the knowledge graph of entities and relations; analyze_technicals reads a company's technical indicators by name.`;

export type Briefing = {
  previousPortfolioId: number | null;
  previousCompanyIds: Set<string>;
  previousHoldings: number;
  previousExits: number;
  clusterIds: number[];
  companyCount: number;
  themeCount: number;
  text: string;
};

type Company = { corp_code: string; corp_name: string; stock_code: string };

const label = (c: Company) => `${c.corp_name} (company_id ${c.corp_code}, stock_code ${c.stock_code})`;

export async function loadBriefing(sql: SQL, newsWindowDays: number): Promise<Briefing> {
  const [previous] = await sql`SELECT id, created_at, cash_weight, commentary FROM portfolios
    ORDER BY created_at DESC, id DESC LIMIT 1`;
  const holdings = previous
    ? await sql`SELECT c.corp_code, c.corp_name, c.stock_code, h.weight, h.reason
        FROM portfolio_holdings h JOIN companies c ON c.corp_code = h.company_id
        WHERE h.portfolio_id = ${previous.id} ORDER BY h.weight DESC`
    : [];
  const exits = previous
    ? await sql`SELECT c.corp_code, c.corp_name, c.stock_code, e.reason
        FROM portfolio_exits e JOIN companies c ON c.corp_code = e.company_id
        WHERE e.portfolio_id = ${previous.id}`
    : [];

  const clusters = await sql`SELECT s.cluster_id, s.title, s.summary, c.updated_at
    FROM cluster_summaries s JOIN clusters c ON c.id = s.cluster_id
    WHERE c.updated_at >= now() - make_interval(days => ${newsWindowDays})
    ORDER BY c.updated_at DESC`;
  const clusterIds = clusters.map((c: { cluster_id: unknown }) => Number(c.cluster_id));
  const mentions = await sql`SELECT DISTINCT ce.cluster_id, co.corp_code, co.corp_name, co.stock_code
    FROM cluster_entities ce
    JOIN entities e ON e.id = ce.entity_id
    JOIN companies co ON co.corp_code = e.corp_code
    WHERE ce.cluster_id = ANY(${pgArray(clusterIds)}::bigint[])
    ORDER BY co.corp_code`;
  const companies = new Map<string, Company & { clusters: number[] }>();
  for (const m of mentions) {
    const entry = companies.get(m.corp_code) ?? { ...m, clusters: [] };
    entry.clusters.push(Number(m.cluster_id));
    companies.set(m.corp_code, entry);
  }
  const themes = await sql`SELECT tc.corp_code, t.name, tc.is_main
    FROM theme_companies tc JOIN themes t ON t.theme_code = tc.theme_code
    WHERE tc.corp_code = ANY(${pgArray([...companies.keys()])}::text[])
    ORDER BY tc.is_main DESC, t.name`;
  const themesByCompany = new Map<string, string[]>();
  for (const t of themes) {
    const names = themesByCompany.get(t.corp_code) ?? [];
    names.push(t.is_main ? `${t.name} (main)` : t.name);
    themesByCompany.set(t.corp_code, names);
  }

  const lines: string[] = ["# Previous portfolio"];
  if (!previous) {
    lines.push("None: this is the first portfolio, so every holding is an entry.");
  } else {
    lines.push(`Portfolio ${previous.id}, created ${new Date(previous.created_at).toISOString()}, cash_weight ${previous.cash_weight}`);
    for (const h of holdings) lines.push(`- ${label(h)}: weight ${h.weight}. Reason: ${h.reason ?? "(kept, no new reason)"}`);
    if (exits.length) lines.push("Exited last time:");
    for (const e of exits) lines.push(`- ${label(e)}. Reason: ${e.reason}`);
    lines.push(`Commentary: ${previous.commentary}`);
  }
  lines.push("", `# News clusters updated in the last ${newsWindowDays} days`);
  if (!clusters.length) lines.push("None.");
  for (const c of clusters) {
    lines.push(`## [cluster ${c.cluster_id}] ${c.title}`, `Updated ${new Date(c.updated_at).toISOString()}`, c.summary, "");
  }
  lines.push("# Companies mentioned in these clusters");
  if (!companies.size) lines.push("None.");
  for (const c of companies.values()) {
    const themeText = themesByCompany.get(c.corp_code)?.join(", ") ?? "none";
    lines.push(`- ${label(c)}: clusters ${c.clusters.join(", ")}; themes: ${themeText}`);
  }

  return {
    previousPortfolioId: previous ? Number(previous.id) : null,
    previousCompanyIds: new Set(holdings.map((h: Company) => h.corp_code)),
    previousHoldings: holdings.length,
    previousExits: exits.length,
    clusterIds,
    companyCount: companies.size,
    themeCount: themes.length,
    text: lines.join("\n"),
  };
}
```

- [ ] **Step 4: Run tests**

Run: `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/news_test bun test tests/briefing.test.ts && bun run check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/portfolio-builder
git commit -m "feat: briefing of the previous portfolio, recent clusters, companies and themes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: News tools — `get_news_cluster` and `search_news_cluster`

**Files:**
- Create: `src/tools/get_news_cluster.ts`, `src/tools/search_news_cluster.ts`, `tests/news_tools.test.ts`

**Interfaces:**
- Consumes: `tests/db.ts`
- Produces:
  - `getNewsClusterTool(sql: SQL): AgentTool` — params `{ id: integer }`
  - `searchNewsClusterTool(sql: SQL): AgentTool` — params `{ query: string }`
  - `toPrefixQuery(query: string): string`
- Tool convention used by every tool file: return `{ content: [{ type: "text", text: JSON.stringify(result) }], details: result }`; throw `Error` for anything the agent should correct (Pi turns it into an error tool result).

- [ ] **Step 1: Write the failing tests**

`tests/news_tools.test.ts`:

```ts
import { beforeAll, describe, expect, test } from "bun:test";
import { getNewsClusterTool } from "../src/tools/get_news_cluster.ts";
import { searchNewsClusterTool, toPrefixQuery } from "../src/tools/search_news_cluster.ts";
import { hasDb, seedFixture, testSql } from "./db.ts";

test("builds a prefix tsquery and drops tsquery operators", () => {
  expect(toPrefixQuery(" 삼성 HBM ")).toBe("삼성:* & HBM:*");
  expect(toPrefixQuery("a&b | !c:*")).toBe("ab:* & c:*");
  expect(toPrefixQuery("&&")).toBe("");
});

describe.skipIf(!hasDb)("news tools", () => {
  const sql = hasDb ? testSql() : (undefined as never);
  beforeAll(() => seedFixture(sql));

  test("get_news_cluster returns summary, articles, entities and relations", async () => {
    const result = await getNewsClusterTool(sql).execute("call-1", { id: 1 });

    expect(result.details).toMatchObject({
      cluster_id: 1,
      title: "삼성전자 HBM 공급 확대",
      articles: [{ title: expect.any(String) }, { title: expect.any(String) }],
      relations: [
        { source: "삼성전자", type: "supplies", target: "엔비디아" },
        { source: "HBM", type: "used_by", target: "엔비디아" },
      ],
    });
    expect(result.details.entities).toContainEqual({ id: 1, name: "삼성전자", type: "company", company_id: "00126380" });
  });

  test("get_news_cluster rejects an unknown id", async () => {
    await expect(getNewsClusterTool(sql).execute("call-1", { id: 999 })).rejects.toThrow("999");
  });

  test("search_news_cluster matches a word with a particle attached", async () => {
    const result = await searchNewsClusterTool(sql).execute("call-1", { query: "삼성전자" });

    expect(result.details.map((r: { cluster_id: number }) => r.cluster_id)).toEqual([1]);
  });

  test("search_news_cluster rejects a query with no searchable terms", async () => {
    await expect(searchNewsClusterTool(sql).execute("call-1", { query: "&|" })).rejects.toThrow();
  });
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/news_test bun test tests/news_tools.test.ts`
Expected: FAIL — modules not found.

- [ ] **Step 3: Implement**

`src/tools/get_news_cluster.ts`:

```ts
import type { AgentTool } from "@earendil-works/pi-agent-core";
import type { SQL } from "bun";
import { Type } from "typebox";

export function getNewsClusterTool(sql: SQL): AgentTool {
  return {
    name: "get_news_cluster",
    label: "Get news cluster",
    description:
      "One news cluster by cluster_id: title, summary, member articles, entities and the relations extracted from it.",
    parameters: Type.Object({ id: Type.Integer({ description: "cluster_id" }) }),
    execute: async (_toolCallId, { id }) => {
      const [summary] = await sql`SELECT s.cluster_id, s.title, s.summary, c.updated_at
        FROM cluster_summaries s JOIN clusters c ON c.id = s.cluster_id WHERE s.cluster_id = ${id}`;
      if (!summary) throw new Error(`news cluster ${id} does not exist or has no summary yet`);
      const articles = await sql`SELECT a.title, a.source, a.published_at
        FROM article_clusters ac JOIN articles a ON a.id = ac.article_id
        WHERE ac.cluster_id = ${id} ORDER BY a.published_at DESC`;
      const entities = await sql`SELECT e.id, e.raw_name AS name, e.type, e.corp_code AS company_id
        FROM cluster_entities ce JOIN entities e ON e.id = ce.entity_id
        WHERE ce.cluster_id = ${id} ORDER BY e.id`;
      const relations = await sql`SELECT s.raw_name AS source, r.type, t.raw_name AS target, r.description
        FROM relations r
        JOIN entities s ON s.id = r.source_entity_id
        JOIN entities t ON t.id = r.target_entity_id
        WHERE r.cluster_id = ${id} ORDER BY r.id`;
      const result = {
        cluster_id: Number(summary.cluster_id),
        title: summary.title,
        summary: summary.summary,
        updated_at: new Date(summary.updated_at).toISOString(),
        articles: articles.map((a: { title: string; source: string; published_at: Date }) => ({
          title: a.title,
          source: a.source,
          published_at: new Date(a.published_at).toISOString(),
        })),
        entities: entities.map((e: { id: unknown }) => ({ ...e, id: Number(e.id) })),
        relations: [...relations],
      };
      return { content: [{ type: "text", text: JSON.stringify(result) }], details: result };
    },
  };
}
```

`src/tools/search_news_cluster.ts`:

```ts
import type { AgentTool } from "@earendil-works/pi-agent-core";
import type { SQL } from "bun";
import { Type } from "typebox";

export function toPrefixQuery(query: string): string {
  return query
    .split(/\s+/)
    .map((term) => term.replace(/[&|!():*<>'\\]/g, ""))
    .filter(Boolean)
    .map((term) => `${term}:*`)
    .join(" & ");
}

export function searchNewsClusterTool(sql: SQL): AgentTool {
  return {
    name: "search_news_cluster",
    label: "Search news clusters",
    description:
      "Full-text search over every news cluster's title and summary (not only the briefing window). Every word must match as a prefix. Returns up to 10 clusters, best first.",
    parameters: Type.Object({ query: Type.String({ description: "space-separated words" }) }),
    execute: async (_toolCallId, { query }) => {
      const tsquery = toPrefixQuery(query);
      if (!tsquery) throw new Error("query has no searchable words");
      const rows = await sql`SELECT s.cluster_id, s.title, left(s.summary, 200) AS excerpt,
          ts_rank(to_tsvector('simple', s.title || ' ' || s.summary), q) AS rank
        FROM cluster_summaries s, to_tsquery('simple', ${tsquery}) AS q
        WHERE to_tsvector('simple', s.title || ' ' || s.summary) @@ q
        ORDER BY rank DESC, s.cluster_id DESC
        LIMIT 10`;
      const result = rows.map((r: { cluster_id: unknown; rank: unknown }) => ({
        ...r,
        cluster_id: Number(r.cluster_id),
        rank: Number(r.rank),
      }));
      return { content: [{ type: "text", text: JSON.stringify(result) }], details: result };
    },
  };
}
```

- [ ] **Step 4: Run tests**

Run: `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/news_test bun test tests/news_tools.test.ts && bun run check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/portfolio-builder
git commit -m "feat: get_news_cluster and search_news_cluster tools

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Graph tools — `search_graph` and `find_graph_paths`

**Files:**
- Create: `src/tools/graph_seeds.ts`, `src/tools/search_graph.ts`, `src/tools/find_graph_paths.ts`, `tests/graph_tools.test.ts`

**Interfaces:**
- Consumes: `normalizeCompanyName` (Task 4), `pgArray` (Task 3), `tests/db.ts`
- Produces:
  - `findSeedEntities(sql: SQL, name: string): Promise<number[]>` — throws with up to 5 candidate names when nothing matches.
  - `withGraphTimeout<T>(sql: SQL, run: (tx: SQL) => Promise<T>): Promise<T>` — runs in a transaction with a 10 s statement timeout; a timeout becomes `Error("graph query timed out; ...")`.
  - `searchGraphTool(sql: SQL): AgentTool` — params `{ name: string; depth?: integer 1–3 (default 2) }`; details `{ nodes: {id,name,type,company_id,hop}[]; edges: {id,source,type,target,description,cluster_id,hop}[] }`
  - `findGraphPathsTool(sql: SQL): AgentTool` — params `{ from_name: string; to_name: string; max_depth?: integer 1–6 (default 4) }`; details `{ paths: { length: number; steps: {from,to,type,direction,description,cluster_id}[] }[] }`

- [ ] **Step 1: Write the failing tests**

`tests/graph_tools.test.ts`:

```ts
import { beforeAll, describe, expect, test } from "bun:test";
import { findGraphPathsTool } from "../src/tools/find_graph_paths.ts";
import { findSeedEntities } from "../src/tools/graph_seeds.ts";
import { searchGraphTool } from "../src/tools/search_graph.ts";
import { hasDb, seedFixture, testSql } from "./db.ts";

describe.skipIf(!hasDb)("graph tools", () => {
  const sql = hasDb ? testSql() : (undefined as never);
  beforeAll(() => seedFixture(sql));

  test("seeds match normalized names and company aliases", async () => {
    expect(await findSeedEntities(sql, "㈜ 삼성전자")).toEqual([1]);
    expect(await findSeedEntities(sql, "hbm")).toEqual([4]);
  });

  test("an unknown name lists candidates", async () => {
    await expect(findSeedEntities(sql, "삼성바이오")).rejects.toThrow("삼성전자");
  });

  test.each([
    [1, [1, 2]],
    [2, [1, 2, 4]],
    [3, [1, 2, 4, 3]],
  ])("search_graph at depth %i reaches %p, nearest first", async (depth, expected) => {
    const result = await searchGraphTool(sql).execute("c", { name: "삼성전자", depth });

    expect(result.details.nodes.map((n: { id: number }) => n.id)).toEqual(expected);
  });

  test("search_graph returns edges between reached nodes with their cluster", async () => {
    const result = await searchGraphTool(sql).execute("c", { name: "삼성전자", depth: 2 });

    expect(result.details.edges).toEqual([
      expect.objectContaining({ source: "삼성전자", type: "supplies", target: "엔비디아", cluster_id: 1, hop: 0 }),
      expect.objectContaining({ source: "HBM", type: "used_by", target: "엔비디아", cluster_id: 1, hop: 1 }),
    ]);
  });

  test("find_graph_paths walks edges in both directions without revisiting nodes", async () => {
    const result = await findGraphPathsTool(sql).execute("c", { from_name: "삼성전자", to_name: "SK하이닉스", max_depth: 4 });

    expect(result.details.paths).toHaveLength(1);
    expect(result.details.paths[0].steps).toEqual([
      expect.objectContaining({ from: "삼성전자", to: "엔비디아", type: "supplies", direction: "forward", cluster_id: 1 }),
      expect.objectContaining({ from: "엔비디아", to: "HBM", type: "used_by", direction: "backward", cluster_id: 1 }),
      expect.objectContaining({ from: "HBM", to: "SK하이닉스", type: "produces", direction: "backward", cluster_id: 2 }),
    ]);
  });

  test("find_graph_paths respects max_depth", async () => {
    const result = await findGraphPathsTool(sql).execute("c", { from_name: "삼성전자", to_name: "SK하이닉스", max_depth: 2 });

    expect(result.details.paths).toEqual([]);
  });
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/news_test bun test tests/graph_tools.test.ts`
Expected: FAIL — modules not found.

- [ ] **Step 3: Implement**

`src/tools/graph_seeds.ts`:

```ts
import { SQL } from "bun";
import { normalizeCompanyName } from "./normalize_company_name.ts";

const likeEscape = (text: string) => text.replace(/[\\%_]/g, "\\$&");

export async function findSeedEntities(sql: SQL, name: string): Promise<number[]> {
  const normalized = normalizeCompanyName(name);
  if (!normalized) throw new Error("name must not be empty");
  const rows = await sql`SELECT e.id FROM entities e WHERE e.name LIKE ${`%${likeEscape(normalized)}%`}
    UNION
    SELECT e.id FROM company_aliases a JOIN entities e ON e.corp_code = a.corp_code
    WHERE a.alias = ${normalized}
    ORDER BY id`;
  if (rows.length) return rows.map((r: { id: unknown }) => Number(r.id));
  const candidates = await sql`SELECT DISTINCT raw_name FROM entities
    WHERE name LIKE ${`%${likeEscape(normalized.slice(0, 2))}%`} ORDER BY raw_name LIMIT 5`;
  const names = candidates.map((c: { raw_name: string }) => c.raw_name);
  throw new Error(`no entity matches "${name}". Candidates: ${names.length ? names.join(", ") : "none"}`);
}

// ponytail: fixed 10 s cap, not a setting; a hub entity at high depth is the only slow case.
export async function withGraphTimeout<T>(sql: SQL, run: (tx: SQL) => Promise<T>): Promise<T> {
  try {
    return await sql.begin(async (tx) => {
      await tx`SET LOCAL statement_timeout = '10s'`;
      return run(tx as unknown as SQL);
    });
  } catch (error) {
    if (error instanceof SQL.PostgresError && error.code === "57014") {
      throw new Error("graph query timed out; use a smaller depth or a more specific name");
    }
    throw error;
  }
}
```

`src/tools/search_graph.ts`:

```ts
import type { AgentTool } from "@earendil-works/pi-agent-core";
import type { SQL } from "bun";
import { Type } from "typebox";
import { pgArray } from "../pg_array.ts";
import { findSeedEntities, withGraphTimeout } from "./graph_seeds.ts";

export function searchGraphTool(sql: SQL): AgentTool {
  return {
    name: "search_graph",
    label: "Search knowledge graph",
    description:
      "The knowledge-graph neighbourhood of an entity (company, product, person, ...) found by name: every entity within `depth` hops over relations in either direction, and every relation among them with the cluster_id it came from. Nearest first.",
    parameters: Type.Object({
      name: Type.String(),
      depth: Type.Optional(Type.Integer({ minimum: 1, maximum: 3, default: 2 })),
    }),
    execute: async (_toolCallId, { name, depth = 2 }) => {
      const seeds = await findSeedEntities(sql, name);
      const result = await withGraphTimeout(sql, async (tx) => {
        const nodes = await tx`WITH RECURSIVE edges AS (
            SELECT source_entity_id AS a, target_entity_id AS b FROM relations
            UNION ALL
            SELECT target_entity_id, source_entity_id FROM relations
          ), walk(entity_id, hop) AS (
            SELECT id, 0 FROM unnest(${pgArray(seeds)}::bigint[]) AS s(id)
            UNION
            SELECT e.b, w.hop + 1 FROM walk w JOIN edges e ON e.a = w.entity_id WHERE w.hop < ${depth}
          )
          SELECT w.entity_id AS id, min(w.hop) AS hop, e.raw_name AS name, e.type, e.corp_code AS company_id
          FROM walk w JOIN entities e ON e.id = w.entity_id
          GROUP BY w.entity_id, e.raw_name, e.type, e.corp_code
          ORDER BY hop, id`;
        const hops = new Map<number, number>(
          nodes.map((n: { id: unknown; hop: unknown }) => [Number(n.id), Number(n.hop)]),
        );
        const ids = pgArray([...hops.keys()]);
        const edges = await tx`SELECT r.id, r.source_entity_id, r.target_entity_id, s.raw_name AS source,
            r.type, t.raw_name AS target, r.description, r.cluster_id
          FROM relations r
          JOIN entities s ON s.id = r.source_entity_id
          JOIN entities t ON t.id = r.target_entity_id
          WHERE r.source_entity_id = ANY(${ids}::bigint[]) AND r.target_entity_id = ANY(${ids}::bigint[])
          ORDER BY r.id`;
        return {
          nodes: nodes.map((n: { id: unknown; hop: unknown }) => ({ ...n, id: Number(n.id), hop: Number(n.hop) })),
          edges: edges
            .map((e: { id: unknown; source_entity_id: unknown; target_entity_id: unknown; cluster_id: unknown }) => {
              const { source_entity_id, target_entity_id, ...edge } = e;
              return {
                ...edge,
                id: Number(e.id),
                cluster_id: Number(e.cluster_id),
                hop: Math.min(hops.get(Number(source_entity_id)) ?? 0, hops.get(Number(target_entity_id)) ?? 0),
              };
            })
            .sort((x: { hop: number; id: number }, y: { hop: number; id: number }) => x.hop - y.hop || x.id - y.id),
        };
      });
      return { content: [{ type: "text", text: JSON.stringify(result) }], details: result };
    },
  };
}
```

`src/tools/find_graph_paths.ts`:

```ts
import type { AgentTool } from "@earendil-works/pi-agent-core";
import type { SQL } from "bun";
import { Type } from "typebox";
import { pgArray } from "../pg_array.ts";
import { findSeedEntities, withGraphTimeout } from "./graph_seeds.ts";

export function findGraphPathsTool(sql: SQL): AgentTool {
  return {
    name: "find_graph_paths",
    label: "Find graph paths",
    description:
      "Every simple path of at most max_depth relations between two entities in the knowledge graph, following relations in either direction, shortest first. Use it to see how an event or company reaches another company.",
    parameters: Type.Object({
      from_name: Type.String(),
      to_name: Type.String(),
      max_depth: Type.Optional(Type.Integer({ minimum: 1, maximum: 6, default: 4 })),
    }),
    execute: async (_toolCallId, { from_name, to_name, max_depth = 4 }) => {
      const from = await findSeedEntities(sql, from_name);
      const to = await findSeedEntities(sql, to_name);
      const result = await withGraphTimeout(sql, async (tx) => {
        const paths = await tx`WITH RECURSIVE edges AS (
            SELECT id AS relation_id, source_entity_id AS a, target_entity_id AS b, 'forward'::text AS direction FROM relations
            UNION ALL
            SELECT id, target_entity_id, source_entity_id, 'backward'::text FROM relations
          ), paths(node, nodes, relation_ids, directions) AS (
            SELECT id, ARRAY[id], ARRAY[]::bigint[], ARRAY[]::text[]
            FROM unnest(${pgArray(from)}::bigint[]) AS s(id)
            UNION ALL
            SELECT e.b, p.nodes || e.b, p.relation_ids || e.relation_id, p.directions || e.direction
            FROM paths p JOIN edges e ON e.a = p.node
            WHERE cardinality(p.relation_ids) < ${max_depth}
              AND e.b <> ALL(p.nodes)
              AND p.node <> ALL(${pgArray(to)}::bigint[])
          )
          SELECT nodes, relation_ids, directions FROM paths
          WHERE node = ANY(${pgArray(to)}::bigint[]) AND cardinality(relation_ids) > 0
          ORDER BY cardinality(relation_ids), nodes`;
        const nodeIds = [...new Set(paths.flatMap((p: { nodes: unknown[] }) => p.nodes.map(Number)))];
        const relationIds = [...new Set(paths.flatMap((p: { relation_ids: unknown[] }) => p.relation_ids.map(Number)))];
        const names = new Map<number, string>(
          (await tx`SELECT id, raw_name FROM entities WHERE id = ANY(${pgArray(nodeIds)}::bigint[])`)
            .map((e: { id: unknown; raw_name: string }) => [Number(e.id), e.raw_name]),
        );
        const relations = new Map<number, { type: string; description: string; cluster_id: unknown }>(
          (await tx`SELECT id, type, description, cluster_id FROM relations WHERE id = ANY(${pgArray(relationIds)}::bigint[])`)
            .map((r: { id: unknown; type: string; description: string; cluster_id: unknown }) => [Number(r.id), r]),
        );
        return {
          paths: paths.map((p: { nodes: unknown[]; relation_ids: unknown[]; directions: string[] }) => ({
            length: p.relation_ids.length,
            steps: p.relation_ids.map((rid, i) => {
              const relation = relations.get(Number(rid));
              return {
                from: names.get(Number(p.nodes[i])),
                to: names.get(Number(p.nodes[i + 1])),
                type: relation?.type,
                direction: p.directions[i],
                description: relation?.description,
                cluster_id: Number(relation?.cluster_id),
              };
            }),
          })),
        };
      });
      return { content: [{ type: "text", text: JSON.stringify(result) }], details: result };
    },
  };
}
```

- [ ] **Step 4: Run tests**

Run: `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/news_test bun test tests/graph_tools.test.ts && bun run check`
Expected: PASS. If Bun returns array columns as strings instead of arrays, select `to_jsonb(nodes) AS nodes` (and likewise for `relation_ids`, `directions`) rather than parsing strings by hand.

- [ ] **Step 5: Commit**

```bash
git add services/portfolio-builder
git commit -m "feat: multi-hop search_graph and find_graph_paths tools

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: `analyze_technicals` over MCP

**Files:**
- Create: `src/tools/analyze_technicals.ts`, `tests/analyze_technicals.test.ts`

**Interfaces:**
- Consumes: `normalizeCompanyName`, `tests/db.ts`
- Produces:
  - `type TechnicalsClient = Pick<Client, "callTool">`
  - `connectMarketMcp(url: string): () => Promise<TechnicalsClient>` — lazy, retried on the next call after a failed connect.
  - `analyzeTechnicalsTool(sql: SQL, getClient: () => Promise<TechnicalsClient>): AgentTool` — params `{ name: string }`; details `{ company_id, stock_code }`.

- [ ] **Step 1: Write the failing tests**

`tests/analyze_technicals.test.ts`:

```ts
import { beforeAll, describe, expect, test } from "bun:test";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { CallToolRequestSchema } from "@modelcontextprotocol/sdk/types.js";
import { analyzeTechnicalsTool } from "../src/tools/analyze_technicals.ts";
import { hasDb, SAMSUNG, seedFixture, testSql } from "./db.ts";

async function fakeMarketMcp() {
  const calls: unknown[] = [];
  const server = new Server({ name: "fake-market", version: "0" }, { capabilities: { tools: {} } });
  server.setRequestHandler(CallToolRequestSchema, async (request) => {
    calls.push({ name: request.params.name, arguments: request.params.arguments });
    if (request.params.arguments?.stock_code === "000660") {
      return { content: [{ type: "text", text: "no bars for 000660" }], isError: true };
    }
    return { content: [{ type: "text", text: `RSI 71 overbought for ${request.params.arguments?.stock_code}` }] };
  });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  await server.connect(serverTransport);
  const client = new Client({ name: "test", version: "0" });
  await client.connect(clientTransport);
  return { client, calls };
}

describe.skipIf(!hasDb)("analyze_technicals", () => {
  const sql = hasDb ? testSql() : (undefined as never);
  beforeAll(() => seedFixture(sql));

  test("resolves a name through company_aliases and calls MCP with the stock code", async () => {
    const { client, calls } = await fakeMarketMcp();

    const result = await analyzeTechnicalsTool(sql, async () => client).execute("c", { name: "㈜삼성 전자" });

    expect(calls).toEqual([{ name: "analyze_technicals", arguments: { stock_code: "005930" } }]);
    expect(result.details).toEqual({ company_id: SAMSUNG, stock_code: "005930" });
    const text = (result.content[0] as { text: string }).text;
    expect(text).toContain(`삼성전자 (company_id ${SAMSUNG}, stock_code 005930)`);
    expect(text).toContain("RSI 71 overbought for 005930");
  });

  test("accepts a company_id directly", async () => {
    const { client, calls } = await fakeMarketMcp();

    await analyzeTechnicalsTool(sql, async () => client).execute("c", { name: SAMSUNG });

    expect(calls).toEqual([{ name: "analyze_technicals", arguments: { stock_code: "005930" } }]);
  });

  test("an unknown name lists candidates", async () => {
    const { client } = await fakeMarketMcp();

    await expect(analyzeTechnicalsTool(sql, async () => client).execute("c", { name: "삼성" })).rejects.toThrow("삼성전자");
  });

  test("an MCP tool error is passed to the agent", async () => {
    const { client } = await fakeMarketMcp();

    await expect(analyzeTechnicalsTool(sql, async () => client).execute("c", { name: "SK하이닉스" })).rejects.toThrow("no bars for 000660");
  });

  test("an unreachable MCP server becomes a tool error", async () => {
    const tool = analyzeTechnicalsTool(sql, async () => {
      throw new Error("connect ECONNREFUSED");
    });

    await expect(tool.execute("c", { name: "삼성전자" })).rejects.toThrow("market-analyzer-mcp is unavailable");
  });
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/news_test bun test tests/analyze_technicals.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

`src/tools/analyze_technicals.ts`:

```ts
import type { AgentTool } from "@earendil-works/pi-agent-core";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import type { SQL } from "bun";
import { Type } from "typebox";
import { normalizeCompanyName } from "./normalize_company_name.ts";

export type TechnicalsClient = Pick<Client, "callTool">;

// Connect on first use so a run whose agent never asks for technicals does not need the server.
export function connectMarketMcp(url: string): () => Promise<TechnicalsClient> {
  let pending: Promise<TechnicalsClient> | undefined;
  return () => {
    pending ??= (async () => {
      const client = new Client({ name: "portfolio-builder", version: "0.1.0" });
      await client.connect(new StreamableHTTPClientTransport(new URL(url)));
      return client;
    })().catch((error) => {
      pending = undefined;
      throw error;
    });
    return pending;
  };
}

export function analyzeTechnicalsTool(
  sql: SQL,
  getClient: () => Promise<TechnicalsClient>,
): AgentTool {
  return {
    name: "analyze_technicals",
    label: "Analyze technicals",
    description:
      "Technical indicators and their verdicts for one listed company, looked up by company name (or company_id).",
    parameters: Type.Object({ name: Type.String({ description: "company name or company_id" }) }),
    execute: async (_toolCallId, { name }) => {
      const [company] = await sql`SELECT corp_code, corp_name, stock_code FROM (
          SELECT c.*, 0 AS priority FROM companies c WHERE c.corp_code = ${name.trim()}
          UNION ALL
          SELECT c.*, 1 FROM company_aliases a JOIN companies c ON c.corp_code = a.corp_code
          WHERE a.alias = ${normalizeCompanyName(name)}
        ) AS matches ORDER BY priority LIMIT 1`;
      if (!company) {
        const candidates = await sql`SELECT corp_name FROM companies
          WHERE corp_name ILIKE ${`%${name.trim()}%`} ORDER BY corp_name LIMIT 5`;
        const names = candidates.map((c: { corp_name: string }) => c.corp_name);
        throw new Error(`no company matches "${name}". Candidates: ${names.length ? names.join(", ") : "none"}`);
      }

      let client: TechnicalsClient;
      try {
        client = await getClient();
      } catch (error) {
        throw new Error(`market-analyzer-mcp is unavailable: ${error instanceof Error ? error.message : error}`);
      }
      const response = await client.callTool({
        name: "analyze_technicals",
        arguments: { stock_code: company.stock_code },
      });
      const text = (response.content as { type: string; text?: string }[])
        .filter((c) => c.type === "text")
        .map((c) => c.text)
        .join("\n");
      if (response.isError) throw new Error(text || "analyze_technicals failed");

      return {
        content: [
          {
            type: "text",
            text: `${company.corp_name} (company_id ${company.corp_code}, stock_code ${company.stock_code})\n${text}`,
          },
        ],
        details: { company_id: company.corp_code, stock_code: company.stock_code },
      };
    },
  };
}
```

- [ ] **Step 4: Run tests**

Run: `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/news_test bun test tests/analyze_technicals.test.ts && bun run check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/portfolio-builder
git commit -m "feat: analyze_technicals tool resolving names and calling market-analyzer-mcp

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: `submit_portfolio` tool and the agent run

**Files:**
- Create: `src/tools/submit_portfolio.ts`, `src/agent.ts`, `tests/agent.test.ts`

**Interfaces:**
- Consumes: `validatePortfolio`, `normalizeWeights`, `savePortfolio`/`SaveError`, `Log`, `tests/db.ts`
- Produces:
  - `submitPortfolioTool(deps: { sql: SQL; previous: ReadonlySet<string>; model: string; log: Log }): AgentTool` — success returns `terminate: true` with `details: { portfolio_id }`; every failure is thrown as one error listing all problems.
  - `type RunResult = { outcome: "saved" | "max_turns" | "error"; portfolioId: number | null; turns: number; usage: UsageTotals; error?: string }`
  - `type UsageTotals = { input: number; output: number; cache_read: number; cache_write: number; cost: number }`
  - `runAgent(options: { model: Model<Api>; streamFn: StreamFn; thinkingLevel: ThinkingLevel; systemPrompt: string; briefing: string; tools: AgentTool[]; maxTurns: number; log: Log }): Promise<RunResult>`

- [ ] **Step 1: Write the failing tests**

`tests/agent.test.ts`:

```ts
import { beforeEach, describe, expect, test } from "bun:test";
import {
  createModels,
  fauxAssistantMessage,
  fauxProvider,
  fauxText,
  fauxToolCall,
} from "@earendil-works/pi-ai";
import { runAgent } from "../src/agent.ts";
import { createLog } from "../src/log.ts";
import { submitPortfolioTool } from "../src/tools/submit_portfolio.ts";
import { hasDb, SAMSUNG, seedFixture, testSql } from "./db.ts";

function scriptedModel(...steps: Parameters<ReturnType<typeof fauxProvider>["setResponses"]>[0]) {
  const faux = fauxProvider();
  faux.setResponses(steps);
  const models = createModels();
  models.setProvider(faux.provider);
  return { model: faux.getModel(), streamFn: models.streamSimple.bind(models) };
}

const valid = {
  holdings: [{ company_id: SAMSUNG, weight: 3, reason: "HBM 공급 확대", cited_cluster_ids: [1] }],
  exits: [],
  cash_weight: 1,
  commentary: "HBM 수요에 집중",
};

describe.skipIf(!hasDb)("runAgent", () => {
  const sql = hasDb ? testSql() : (undefined as never);
  beforeEach(() => seedFixture(sql));

  function setup(maxTurns: number, ...steps: Parameters<ReturnType<typeof fauxProvider>["setResponses"]>[0]) {
    const lines: string[] = [];
    const log = createLog("run-test", "DEBUG", (line) => lines.push(line));
    const { model, streamFn } = scriptedModel(...steps);
    const tools = [submitPortfolioTool({ sql, previous: new Set(), model: "faux", log })];
    const run = () =>
      runAgent({ model, streamFn, thinkingLevel: "off", systemPrompt: "sys", briefing: "brief", tools, maxTurns, log });
    return { run, events: () => lines.map((l) => JSON.parse(l)) };
  }

  test("an invalid submission is fed back, the corrected one is saved", async () => {
    const { run, events } = setup(
      10,
      fauxAssistantMessage(fauxToolCall("submit_portfolio", { ...valid, holdings: [{ ...valid.holdings[0], reason: "" }] }), { stopReason: "toolUse" }),
      fauxAssistantMessage(fauxToolCall("submit_portfolio", valid), { stopReason: "toolUse" }),
    );

    const result = await run();

    expect(result.outcome).toBe("saved");
    expect(result.turns).toBe(2);
    const [row] = await sql`SELECT id, cash_weight FROM portfolios`;
    expect(Number(row.id)).toBe(result.portfolioId as number);
    expect(row.cash_weight).toBeCloseTo(0.25);
    const names = events().map((e) => e.event);
    expect(names).toContain("validation_failed");
    expect(names).toContain("llm_response");
    expect(names).toContain("tool_call");
  });

  test("an agent that stops talking is told to submit, and hits the turn limit", async () => {
    const { run } = setup(
      3,
      fauxAssistantMessage(fauxText("thinking about it"), { stopReason: "stop" }),
      fauxAssistantMessage(fauxText("still thinking"), { stopReason: "stop" }),
      fauxAssistantMessage(fauxText("almost"), { stopReason: "stop" }),
      fauxAssistantMessage(fauxText("never reached"), { stopReason: "stop" }),
    );

    const result = await run();

    expect(result).toMatchObject({ outcome: "max_turns", portfolioId: null, turns: 3 });
    const [{ count }] = await sql`SELECT count(*)::int AS count FROM portfolios`;
    expect(count).toBe(0);
  });

  test("an LLM error ends the run", async () => {
    const { run } = setup(
      10,
      fauxAssistantMessage([], { stopReason: "error", errorMessage: "upstream 500" }),
    );

    const result = await run();

    expect(result).toMatchObject({ outcome: "error", portfolioId: null, error: "upstream 500" });
  });
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/news_test bun test tests/agent.test.ts`
Expected: FAIL — modules not found.

- [ ] **Step 3: Implement**

`src/tools/submit_portfolio.ts`:

```ts
import type { AgentTool } from "@earendil-works/pi-agent-core";
import type { SQL } from "bun";
import { Type } from "typebox";
import type { Log } from "../log.ts";
import { normalizeWeights } from "../portfolio/normalize_weights.ts";
import { SaveError, savePortfolio } from "../portfolio/save_portfolio.ts";
import { type Submission, validatePortfolio } from "../portfolio/validate_portfolio.ts";

const CitedClusters = Type.Array(Type.Integer({ minimum: 1 }), {
  description: "cluster_ids this decision relies on",
});

export function submitPortfolioTool(deps: {
  sql: SQL;
  previous: ReadonlySet<string>;
  model: string;
  log: Log;
}): AgentTool {
  return {
    name: "submit_portfolio",
    label: "Submit portfolio",
    description:
      "Submit the new model portfolio. Weights are relative; the system scales holdings and cash_weight to sum to 1. On errors, fix every one and submit again.",
    parameters: Type.Object({
      holdings: Type.Array(
        Type.Object({
          company_id: Type.String(),
          weight: Type.Number({ minimum: 0 }),
          reason: Type.Optional(Type.String({ description: "required for a company entering the portfolio" })),
          cited_cluster_ids: CitedClusters,
        }),
      ),
      exits: Type.Array(
        Type.Object({
          company_id: Type.String(),
          reason: Type.String(),
          cited_cluster_ids: CitedClusters,
        }),
        { description: "every previous holding that is dropped" },
      ),
      cash_weight: Type.Number({ minimum: 0 }),
      commentary: Type.String({ description: "overall assessment of the portfolio and this run's decisions" }),
    }),
    execute: async (_toolCallId, submission: Submission) => {
      const errors = validatePortfolio(submission, deps.previous);
      if (!errors.length) {
        try {
          const id = await savePortfolio(deps.sql, normalizeWeights(submission), deps.model);
          return {
            content: [{ type: "text", text: `Saved portfolio ${id}.` }],
            details: { portfolio_id: id },
            terminate: true,
          };
        } catch (error) {
          if (!(error instanceof SaveError)) throw error;
          errors.push(...error.errors);
        }
      }
      deps.log("validation_failed", { errors }, "WARNING");
      throw new Error(
        `The portfolio was not saved. Fix every error and call submit_portfolio again:\n- ${errors.join("\n- ")}`,
      );
    },
  };
}
```

`src/agent.ts`:

```ts
import { Agent, type AgentTool, type StreamFn, type ThinkingLevel } from "@earendil-works/pi-agent-core";
import type { Api, AssistantMessage, Model } from "@earendil-works/pi-ai";
import type { Log } from "./log.ts";

export type UsageTotals = { input: number; output: number; cache_read: number; cache_write: number; cost: number };
export type RunResult = {
  outcome: "saved" | "max_turns" | "error";
  portfolioId: number | null;
  turns: number;
  usage: UsageTotals;
  error?: string;
};

const NUDGE =
  "You stopped without a saved portfolio. Keep investigating with the tools if you need to, then call submit_portfolio.";

// Each request resends the whole transcript; the transcript is logged once per message instead.
function summarizePayload(payload: unknown): Record<string, unknown> {
  if (typeof payload !== "object" || payload === null) return {};
  const { input, messages, instructions, tools, ...rest } = payload as Record<string, unknown>;
  const history = (input ?? messages) as unknown[] | undefined;
  return {
    ...rest,
    tools: Array.isArray(tools) ? tools.map((t) => t?.name ?? t?.function?.name) : undefined,
    message_count: Array.isArray(history) ? history.length : undefined,
  };
}

export async function runAgent(options: {
  model: Model<Api>;
  streamFn: StreamFn;
  thinkingLevel: ThinkingLevel;
  systemPrompt: string;
  briefing: string;
  tools: AgentTool[];
  maxTurns: number;
  log: Log;
}): Promise<RunResult> {
  const { log, maxTurns } = options;
  const usage: UsageTotals = { input: 0, output: 0, cache_read: 0, cache_write: 0, cost: 0 };
  const toolStarts = new Map<string, { at: number; args: unknown }>();
  let turns = 0;
  let turnStartedAt = 0;
  let portfolioId: number | null = null;
  let lastAssistant: AssistantMessage | undefined;

  const agent: Agent = new Agent({
    initialState: {
      systemPrompt: options.systemPrompt,
      model: options.model,
      thinkingLevel: options.thinkingLevel,
      tools: options.tools,
    },
    streamFn: options.streamFn,
    onPayload: (payload) => {
      log("llm_request", { turn: turns + 1, ...summarizePayload(payload) });
      return undefined;
    },
    finishTurn: ({ message, toolResults }) => {
      turns += 1;
      const saved = toolResults.find((r) => r.toolName === "submit_portfolio" && !r.isError);
      if (saved) portfolioId = Number((saved.details as { portfolio_id: number }).portfolio_id);
      if (portfolioId !== null || turns >= maxTurns) return { action: "end" };
      if (message.stopReason === "stop" && toolResults.length === 0) {
        agent.followUp({ role: "user", content: NUDGE, timestamp: Date.now() });
      }
      return undefined;
    },
  });

  agent.subscribe((event) => {
    if (event.type === "turn_start") turnStartedAt = performance.now();
    if (event.type === "message_end" && event.message.role === "assistant") {
      const message = event.message as AssistantMessage;
      lastAssistant = message;
      usage.input += message.usage.input;
      usage.output += message.usage.output;
      usage.cache_read += message.usage.cacheRead;
      usage.cache_write += message.usage.cacheWrite;
      usage.cost += message.usage.cost.total;
      log("llm_response", {
        turn: turns + 1,
        model: message.responseModel ?? message.model,
        stop_reason: message.stopReason,
        error: message.errorMessage,
        text: message.content.flatMap((c) => (c.type === "text" ? [c.text] : [])).join("\n"),
        reasoning: message.content.flatMap((c) => (c.type === "thinking" ? [c.thinking] : [])).join("\n"),
        tool_calls: message.content.flatMap((c) => (c.type === "toolCall" ? [{ name: c.name, arguments: c.arguments }] : [])),
        latency_ms: Math.round(performance.now() - turnStartedAt),
        usage: {
          input: message.usage.input,
          output: message.usage.output,
          cache_read: message.usage.cacheRead,
          cache_write: message.usage.cacheWrite,
          reasoning: message.usage.reasoning,
          total: message.usage.totalTokens,
          cost: message.usage.cost.total,
        },
      });
    }
    if (event.type === "tool_execution_start") {
      toolStarts.set(event.toolCallId, { at: performance.now(), args: event.args });
    }
    if (event.type === "tool_execution_end") {
      const result = event.result as { content?: { type: string; text?: string }[]; details?: unknown };
      const start = toolStarts.get(event.toolCallId);
      log(
        "tool_call",
        {
          turn: turns + 1,
          name: event.toolName,
          args: start?.args,
          result: result.content?.map((c) => c.text ?? "").join("\n"),
          details: result.details,
          is_error: event.isError,
          duration_ms: start ? Math.round(performance.now() - start.at) : null,
        },
        event.isError ? "WARNING" : "INFO",
      );
    }
  });

  await agent.prompt(options.briefing);

  if (portfolioId !== null) return { outcome: "saved", portfolioId, turns, usage };
  if (turns >= maxTurns) return { outcome: "max_turns", portfolioId, turns, usage };
  return {
    outcome: "error",
    portfolioId,
    turns,
    usage,
    error: lastAssistant?.errorMessage ?? agent.state.errorMessage ?? "agent stopped without a portfolio",
  };
}
```

- [ ] **Step 4: Run tests**

Run: `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/news_test bun test tests/agent.test.ts && bun run check`
Expected: PASS. If the faux provider needs a registered credential before `streamSimple`, pass `createModels({ credentials: new InMemoryCredentialStore() })` — do not add auth code to `runAgent`.

- [ ] **Step 5: Commit**

```bash
git add services/portfolio-builder
git commit -m "feat: submit_portfolio tool and the self-correcting agent run

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Credentials and `main.ts`

**Files:**
- Create: `src/credentials.ts`, `src/main.ts`, `tests/credentials.test.ts`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `class FileCredentialStore implements CredentialStore` (constructor `(path: string)`)
  - `seedCodexCredential(store: CredentialStore, settings: Pick<Settings, "openaiAccessToken" | "openaiRefreshToken" | "openaiTokenExpiresEpoch">): Promise<void>`
  - `createCodexModels(settings: Settings): Promise<Models>`
  - `main(): Promise<number>` — exit code; runs only when `import.meta.main`.

- [ ] **Step 1: Write the failing tests**

`tests/credentials.test.ts`:

```ts
import { expect, test } from "bun:test";
import { mkdtemp, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { FileCredentialStore, seedCodexCredential } from "../src/credentials.ts";

const tokens = { openaiAccessToken: "access", openaiRefreshToken: "refresh", openaiTokenExpiresEpoch: 1790000000000 };

async function tempPath() {
  return join(await mkdtemp(join(tmpdir(), "pb-cred-")), "auth.json");
}

test("seeds an empty store from the environment tokens, readable only by the owner", async () => {
  const path = await tempPath();

  await seedCodexCredential(new FileCredentialStore(path), tokens);

  expect(await new FileCredentialStore(path).read("openai-codex")).toEqual({
    type: "oauth",
    access: "access",
    refresh: "refresh",
    expires: 1790000000000,
  });
  expect((await stat(path)).mode & 0o777).toBe(0o600);
});

test("never overwrites a stored, possibly rotated credential", async () => {
  const path = await tempPath();
  const store = new FileCredentialStore(path);
  await store.modify("openai-codex", async () => ({ type: "oauth", access: "rotated", refresh: "rotated", expires: 1 }));

  await seedCodexCredential(store, tokens);

  expect((await store.read("openai-codex")) as { access: string }).toMatchObject({ access: "rotated" });
});

test("an empty store without environment tokens is an error", async () => {
  const store = new FileCredentialStore(await tempPath());

  await expect(seedCodexCredential(store, {})).rejects.toThrow("PORTFOLIO_BUILDER_OPENAI_ACCESS_TOKEN");
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `bun test tests/credentials.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

`src/credentials.ts`:

```ts
import { readFile, rename, writeFile } from "node:fs/promises";
import { type Credential, type CredentialStore, createModels, type Models } from "@earendil-works/pi-ai";
import { openaiCodexProvider } from "@earendil-works/pi-ai/providers/openai-codex";
import type { Settings } from "./settings.ts";

const PROVIDER = "openai-codex";

// OpenAI rotates the refresh token on every refresh, so it must outlive the process.
export class FileCredentialStore implements CredentialStore {
  constructor(private readonly path: string) {}

  private async load(): Promise<Record<string, Credential>> {
    try {
      return JSON.parse(await readFile(this.path, "utf8"));
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") return {};
      throw error;
    }
  }

  private async save(credentials: Record<string, Credential>): Promise<void> {
    const temporary = `${this.path}.tmp`;
    await writeFile(temporary, JSON.stringify(credentials), { mode: 0o600 });
    await rename(temporary, this.path);
  }

  async read(providerId: string) {
    return (await this.load())[providerId];
  }

  async list() {
    return Object.entries(await this.load()).map(([providerId, c]) => ({ providerId, type: c.type }));
  }

  async modify(providerId: string, fn: (current: Credential | undefined) => Promise<Credential | undefined>) {
    const credentials = await this.load();
    const next = await fn(credentials[providerId]);
    if (next !== undefined) {
      credentials[providerId] = next;
      await this.save(credentials);
    }
    return next ?? credentials[providerId];
  }

  async delete(providerId: string) {
    const credentials = await this.load();
    delete credentials[providerId];
    await this.save(credentials);
  }
}

export async function seedCodexCredential(
  store: CredentialStore,
  settings: Pick<Settings, "openaiAccessToken" | "openaiRefreshToken" | "openaiTokenExpiresEpoch">,
): Promise<void> {
  await store.modify(PROVIDER, async (current) => {
    if (current) return undefined;
    const { openaiAccessToken: access, openaiRefreshToken: refresh, openaiTokenExpiresEpoch: expires } = settings;
    if (!access || !refresh || expires === undefined) {
      throw new Error(
        "no stored OpenAI credential: set PORTFOLIO_BUILDER_OPENAI_ACCESS_TOKEN, PORTFOLIO_BUILDER_OPENAI_REFRESH_TOKEN and PORTFOLIO_BUILDER_OPENAI_TOKEN_EXPIRES_EPOCH",
      );
    }
    return { type: "oauth", access, refresh, expires };
  });
}

export async function createCodexModels(settings: Settings): Promise<Models> {
  const store = new FileCredentialStore(settings.credentialsPath);
  await seedCodexCredential(store, settings);
  const models = createModels({ credentials: store });
  models.setProvider(openaiCodexProvider());
  return models;
}
```

`src/main.ts`:

```ts
import { SQL } from "bun";
import { runAgent } from "./agent.ts";
import { createCodexModels } from "./credentials.ts";
import { createLog } from "./log.ts";
import { loadBriefing, SYSTEM_PROMPT } from "./portfolio/briefing.ts";
import { loadSettings } from "./settings.ts";
import { analyzeTechnicalsTool, connectMarketMcp } from "./tools/analyze_technicals.ts";
import { findGraphPathsTool } from "./tools/find_graph_paths.ts";
import { getNewsClusterTool } from "./tools/get_news_cluster.ts";
import { searchGraphTool } from "./tools/search_graph.ts";
import { searchNewsClusterTool } from "./tools/search_news_cluster.ts";
import { submitPortfolioTool } from "./tools/submit_portfolio.ts";

export async function main(): Promise<number> {
  const settings = loadSettings();
  const log = createLog(crypto.randomUUID(), settings.logLevel);
  const startedAt = performance.now();
  const sql = new SQL(settings.postgresDsn);
  try {
    const models = await createCodexModels(settings);
    const model = models.getModel("openai-codex", settings.llmModel);
    if (!model) throw new Error(`unknown openai-codex model ${settings.llmModel}`);
    log("run_start", {
      provider: model.provider,
      model: model.id,
      reasoning_level: settings.thinkingLevel,
      temperature: null,
      max_turns: settings.maxTurns,
      news_window_days: settings.newsWindowDays,
    });

    const briefing = await loadBriefing(sql, settings.newsWindowDays);
    log("ingestion", {
      previous_portfolio_id: briefing.previousPortfolioId,
      previous_holdings: briefing.previousHoldings,
      previous_exits: briefing.previousExits,
      cluster_ids: briefing.clusterIds,
      cluster_count: briefing.clusterIds.length,
      company_count: briefing.companyCount,
      theme_count: briefing.themeCount,
      briefing_chars: briefing.text.length,
    });
    log("prompt", { system_prompt: SYSTEM_PROMPT, briefing: briefing.text });

    const tools = [
      analyzeTechnicalsTool(sql, connectMarketMcp(settings.marketMcpUrl)),
      getNewsClusterTool(sql),
      searchNewsClusterTool(sql),
      searchGraphTool(sql),
      findGraphPathsTool(sql),
      submitPortfolioTool({
        sql,
        previous: briefing.previousCompanyIds,
        model: `${model.provider}/${model.id}`,
        log,
      }),
    ];
    const result = await runAgent({
      model,
      streamFn: models.streamSimple.bind(models),
      thinkingLevel: settings.thinkingLevel,
      systemPrompt: SYSTEM_PROMPT,
      briefing: briefing.text,
      tools,
      maxTurns: settings.maxTurns,
      log,
    });
    log(
      "run_end",
      {
        outcome: result.outcome,
        portfolio_id: result.portfolioId,
        turns: result.turns,
        usage: result.usage,
        error: result.error,
        elapsed_ms: Math.round(performance.now() - startedAt),
      },
      result.outcome === "saved" ? "INFO" : "ERROR",
    );
    return result.outcome === "saved" ? 0 : 1;
  } catch (error) {
    log(
      "run_end",
      { outcome: "error", error: error instanceof Error ? error.message : String(error), elapsed_ms: Math.round(performance.now() - startedAt) },
      "ERROR",
    );
    return 1;
  } finally {
    await sql.close();
  }
}

if (import.meta.main) process.exitCode = await main();
```

- [ ] **Step 4: Run tests and a smoke run**

Run: `bun test && bun run check`
Expected: all PASS (DB tests skipped without `KTB_TEST_POSTGRES_DSN`).

Run: `PORTFOLIO_BUILDER_POSTGRES_DSN=postgres://ktb:ktb@localhost:5432/news_test PORTFOLIO_BUILDER_MARKET_MCP_URL=http://localhost:1/mcp PORTFOLIO_BUILDER_LLM_MODEL=gpt-5.5 PORTFOLIO_BUILDER_CREDENTIALS_PATH=/tmp/pb-auth.json bun src/main.ts; echo "exit $?"`
Expected: one `run_end` line with `"outcome":"error"` and the "no stored OpenAI credential" message, then `exit 1` (proves wiring without calling OpenAI).

- [ ] **Step 5: Commit**

```bash
git add services/portfolio-builder
git commit -m "feat: portfolio-builder entry point with persisted openai-codex credentials

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Docker, compose, CI and docs

**Files:**
- Modify: `docker/portfolio-builder.Dockerfile` (rewrite), `compose.dev.yaml`, `.github/workflows/ci-dev.yaml`, `AGENTS.md`, `README.md`

- [ ] **Step 1: Rewrite the Dockerfile**

`docker/portfolio-builder.Dockerfile`:

```dockerfile
FROM oven/bun:1.4.2-slim

WORKDIR /app
COPY services/portfolio-builder/package.json services/portfolio-builder/bun.lock ./
RUN bun install --frozen-lockfile --production

COPY services/portfolio-builder/src ./src

# The credential store lives on a volume at /data so rotated OpenAI tokens survive runs.
RUN mkdir /data && chown bun:bun /data
USER bun
CMD ["bun", "src/main.ts"]
```

Run: `docker build -f docker/portfolio-builder.Dockerfile -t portfolio-builder:dev .`
Expected: builds. If the `1.4.2-slim` tag does not exist, use the nearest `oven/bun:1.4.x-slim` tag and update Global Constraints.

- [ ] **Step 2: Add the compose service**

In `compose.dev.yaml`, after `news-graph-builder`, add:

```yaml
  portfolio-builder:
    profiles: ["jobs"]
    build:
      context: .
      dockerfile: docker/portfolio-builder.Dockerfile
    environment:
      - PORTFOLIO_BUILDER_POSTGRES_DSN=postgres://ktb:ktb@postgres:5432/news
      - PORTFOLIO_BUILDER_MARKET_MCP_URL
      - PORTFOLIO_BUILDER_LLM_MODEL
      - PORTFOLIO_BUILDER_THINKING_LEVEL=${PORTFOLIO_BUILDER_THINKING_LEVEL:-medium}
      - PORTFOLIO_BUILDER_OPENAI_ACCESS_TOKEN
      - PORTFOLIO_BUILDER_OPENAI_REFRESH_TOKEN
      - PORTFOLIO_BUILDER_OPENAI_TOKEN_EXPIRES_EPOCH
      - PORTFOLIO_BUILDER_NEWS_WINDOW_DAYS=${PORTFOLIO_BUILDER_NEWS_WINDOW_DAYS:-7}
      - PORTFOLIO_BUILDER_MAX_TURNS=${PORTFOLIO_BUILDER_MAX_TURNS:-150}
      - PORTFOLIO_BUILDER_LOG_LEVEL=${PORTFOLIO_BUILDER_LOG_LEVEL:-INFO}
    volumes:
      - portfolio-builder-data:/data
    depends_on:
      postgres:
        condition: service_healthy
    restart: "no"
```

and under `volumes:` add `  portfolio-builder-data:`.

Run: `docker compose -f compose.dev.yaml config --quiet`
Expected: no output, exit 0.

- [ ] **Step 3: Add the CI job**

In `.github/workflows/ci-dev.yaml`, add a job after `lint-and-test`:

```yaml
  portfolio-builder:
    runs-on: ubuntu-latest

    services:
      postgres:
        image: pgvector/pgvector:0.8.6-pg18-trixie
        env:
          POSTGRES_USER: ktb
          POSTGRES_PASSWORD: ktb
          POSTGRES_DB: news
        ports:
          - "5432:5432"
        options: >-
          --health-cmd "pg_isready -U ktb -d news"
          --health-interval 5s
          --health-timeout 3s
          --health-retries 12
    env:
      KTB_POSTGRES_DSN: postgresql+psycopg://ktb:ktb@localhost:5432/news
      KTB_TEST_POSTGRES_DSN: postgresql+psycopg://ktb:ktb@localhost:5432/news

    steps:
      - uses: actions/checkout@v7

      - uses: astral-sh/setup-uv@v10.2.0
        with:
          enable-cache: true

      - uses: oven-sh/setup-bun@v2
        with:
          bun-version: 1.4.2

      - name: Apply database migrations
        run: |
          uv sync --all-packages --locked --group migrations
          uv run alembic upgrade head

      - name: Install
        working-directory: services/portfolio-builder
        run: bun install --frozen-lockfile

      - name: Lint and type-check
        working-directory: services/portfolio-builder
        run: bun run check

      - name: Test
        working-directory: services/portfolio-builder
        run: bun test
```

The `build-images` matrix already lists `portfolio-builder`; leave it.

- [ ] **Step 4: Update docs**

`AGENTS.md`:
- Commands block: add
  ```bash
  cd services/portfolio-builder && bun install && bun run check && bun test   # the TypeScript member
  docker compose -f compose.dev.yaml up portfolio-builder                        # needs the env below
  ```
- Environment table: replace the four `PORTFOLIO_BUILDER_*` rows with:

  | Variable | Used by | Default |
  |---|---|---|
  | `PORTFOLIO_BUILDER_POSTGRES_DSN` | portfolio-builder (plain `postgres://`, not `+psycopg`) | required |
  | `PORTFOLIO_BUILDER_MARKET_MCP_URL` | portfolio-builder (market-analyzer-mcp Streamable HTTP) | required |
  | `PORTFOLIO_BUILDER_LLM_MODEL` | portfolio-builder (`openai-codex` model id, e.g. `gpt-5.5`) | required |
  | `PORTFOLIO_BUILDER_THINKING_LEVEL` | portfolio-builder | `medium` |
  | `PORTFOLIO_BUILDER_OPENAI_ACCESS_TOKEN` / `_REFRESH_TOKEN` / `_TOKEN_EXPIRES_EPOCH` | portfolio-builder; seed the credential store only while it is empty | — |
  | `PORTFOLIO_BUILDER_CREDENTIALS_PATH` | portfolio-builder (persistent volume; OpenAI rotates refresh tokens) | `/data/auth.json` |
  | `PORTFOLIO_BUILDER_NEWS_WINDOW_DAYS` | portfolio-builder | `7` |
  | `PORTFOLIO_BUILDER_MAX_TURNS` | portfolio-builder | `150` |
  | `PORTFOLIO_BUILDER_LOG_LEVEL` | portfolio-builder | `INFO` |

- Architecture table row: `| services/portfolio-builder | service (TypeScript, Bun) | cron: runs once and exits | Postgres, market-analyzer-mcp (MCP) |`
- Replace "**Services communicate only through datastores.** ..." first sentence with "**Services communicate only through datastores, with one exception:** portfolio-builder calls market-analyzer-mcp over MCP (Streamable HTTP) for technicals." and add that portfolio-builder writes `portfolios`, `portfolio_holdings`, `portfolio_exits` (design: `docs/superpowers/specs/2026-09-27-portfolio-builder-design.md`).
- Replace the "**Work queue:** ... portfolio-builder is its only consumer." bullet with "**Work queue:** SQS in production, Redis in development; no consumer yet (portfolio-builder runs by cron in the MVP)."
- Add a bullet: "**portfolio-builder is not a uv member.** It is a Bun package excluded in `pyproject.toml`; its schema still comes from the Alembic migrations. Its company-name normalizer must match `news_graph_builder.common.normalize` — both are tested against `services/portfolio-builder/tests/fixtures/normalize_cases.json`."
- Add under the Kiwoom key bullet: "**portfolio-builder uses a personal ChatGPT subscription token (`openai-codex`).** Never commit it; the store file on the volume is mode 0600."

`README.md`: line 7 → `- `portfolio-builder`: 뉴스 클러스터·그래프·테마·기술적 지표로 모델 포트폴리오 생성 (TypeScript, Pi 에이전트)`; replace the `### portfolio-builder` table with the same variables as AGENTS.md (Korean descriptions: 필수 / 기본값 columns as the other tables).

- [ ] **Step 5: Verify everything**

Run (repo root): `uv run ruff check . && uv run ruff format --check . && uv run tach check && KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/news_test uv run pytest -q`
Run (`services/portfolio-builder`): `bun run check && KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/news_test bun test`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add docker/portfolio-builder.Dockerfile compose.dev.yaml .github/workflows/ci-dev.yaml AGENTS.md README.md
git commit -m "chore: portfolio-builder image, compose job, CI and docs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Self-review notes

- Spec §3 schema → Task 2; §4 run flow and prompt → Tasks 7, 11, 12; §5.1 → Tasks 4, 10; §5.2–5.3 → Task 8; §5.4 → Task 9; §5.5 → Tasks 5, 6, 11; §6 logging → Tasks 1, 11, 12; §7 layout/settings/Docker/CI/docs → Tasks 1, 12, 13; §8 credentials → Task 12; §9 MCP contract → Task 10; §10 testing → every task.
