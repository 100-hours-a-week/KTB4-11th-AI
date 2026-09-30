# portfolio-builder LangChain Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Python `portfolio-builder` stub with a cron job that builds one model portfolio per run using a LangChain `create_agent` agent on OpenRouter, with news, graph, TA-Lib technical-evidence and submit tools.

**Architecture:** A uv workspace member at `services/portfolio-builder`. SQLAlchemy Core reads/writes Postgres; the official `questdb` client reads OHLCV; TA-Lib computes evidence in-process. The agent loop is `create_agent` plus four middleware: RunLog (logging), ToolErrorMiddleware (recoverable tool errors go back to the model), StopOnSave, Nudge, and `ModelCallLimitMiddleware(exit_behavior="error")`. Everything is synchronous.

**Tech Stack:** Python 3.13, uv, langchain 1.4.x, langchain-openrouter 0.2.x, langgraph 1.2.x, SQLAlchemy 2 + psycopg 3, questdb 5, ta-lib 0.8, numpy, pandas, pydantic-settings, pytest.

**Spec:** `docs/superpowers/specs/2026-09-28-portfolio-builder-langchain-design.md`

## Global Constraints

- Work on branch `feat/portfolio-builder-langchain` (already created from `origin/dev`). Run every command from the repo root.
- Python code must pass `uv run ruff check .` and `uv run ruff format --check .` (line length 100, rules E, F, I, UP, B). Before every commit run `uv run ruff check --fix . && uv run ruff format .`.
- `uv run tach check` must pass after every task that touches imports.
- `packages/core` must keep **zero** third-party dependencies.
- Comments only for a non-obvious *why*. No docstrings that restate names. No thin wrappers with one caller.
- Postgres tests need a migrated test database. Once per machine:
  `docker compose -f compose.dev.yaml up -d postgres`,
  `docker compose -f compose.dev.yaml exec postgres createdb -U ktb ktb_test` (ignore "already exists"),
  `KTB_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test uv run alembic upgrade head`.
  Then run tests with `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test`. Never point tests at `ktb`.
- Never write to QuestDB. No test touches a real QuestDB.
- Never open, read, print or `cat` the repo-root `.env`; it holds real keys. Compose reads it for `${…}` interpolation.
- Environment prefix `PORTFOLIO_BUILDER_`. The API key is a `SecretStr` and must never be logged.
- Commit messages use `feat` / `fix` / `refactor` / `chore` / `docs` / `test` prefixes and end with a blank line then `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Shell note: in zsh, never pass an unquoted `===` as an argument.

## Review Focus

1. **A second `submit_portfolio` in the same model message** must not write a second portfolio; ToolNode runs the calls in parallel threads. Pinned in Task 10 (lock test) and Task 12 (end-to-end through the real ToolNode).
2. **Turn limit vs graph recursion limit:** a model that never submits must end as `max_turns`, never as `error` from `GraphRecursionError`. Pinned in Task 12.
3. **Zero-volume bars** (trading halts, quiet minutes) must yield `null` with reason `zero volume`, never `inf`/`NaN` in the JSON sent to the model. Pinned in Task 8.
4. **A company outside the KOSPI 200 archive** must produce a readable tool error (`NoMarketData`) the model can recover from, not a crash. Pinned in Task 9.
5. **Unexpected exceptions inside a tool** (a bug, a DB outage) must end the run as `error` rather than being shown to the model as a retryable message. Pinned in Task 12.

---

## File map

```
compose.dev.yaml, .github/workflows/*, AGENTS.md, README.md             Task 0 (database rename)
infrastructure/postgres/migrations/versions/0005_create_portfolios.py   Task 1 (cherry-pick)
infrastructure/postgres/tests/test_migrations.py                        Task 1, Task 4
packages/core/src/ktb_core/normalize.py                                 Task 2 (moved)
packages/core/tests/test_normalize.py                                   Task 2
packages/core/src/ktb_core/logging.py                                   Task 3
packages/core/tests/test_logging.py                                     Task 3
services/news-graph-builder/...                                         Task 2 (imports)
tach.toml                                                               Task 2, Task 4
services/portfolio-builder/pyproject.toml                               Task 4
services/portfolio-builder/src/portfolio_builder/
  settings.py        Task 4      database.py   Task 4      log.py     Task 4
  errors.py          Task 4      portfolio.py  Task 5      evidence.py Task 8
  market.py          Task 9      briefing.py   Task 11     agent.py   Task 12
  __main__.py        Task 13
  tools/__init__.py  Task 6      tools/news.py Task 6      tools/graph.py Task 7
  tools/technicals.py Task 9     tools/submit.py Task 10
services/portfolio-builder/tests/
  conftest.py Task 5, test_settings.py Task 4, test_log.py Task 4, test_portfolio.py Task 5,
  test_news_tools.py Task 6, test_graph_tools.py Task 7, test_evidence.py Task 8,
  test_technicals.py Task 9, test_submit.py Task 10, test_briefing.py Task 11,
  test_agent.py Task 12, test_main.py Task 13
docker/portfolio-builder.Dockerfile, docker/requirements/portfolio-builder.txt,
compose.dev.yaml, AGENTS.md, README.md,
docs/superpowers/specs/2026-09-27-portfolio-builder-design.md                Task 13
```

---

### Task 0: Rename the Postgres database `news` → `ktb`

The database now holds themes, industries and portfolios, not only news. The role stays `ktb`/`ktb`.

**Files:**
- Modify: `compose.dev.yaml`, `.github/workflows/ci-dev.yaml`, `.github/workflows/ci-main.yaml`, `AGENTS.md`, `README.md`

**Interfaces:**
- Produces: development DSN `postgresql+psycopg://ktb:ktb@localhost:5432/ktb`, test DSN `postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test`. Every later task uses these.

- [ ] **Step 1: Rewrite every reference**

```bash
sed -i -e 's#POSTGRES_DB: news$#POSTGRES_DB: ktb#' -e 's#-d news"#-d ktb"#' \
  -e 's#5432/news_test#5432/ktb_test#g' -e 's#5432/news$#5432/ktb#' -e 's#5432/news\([^_a-z]\)#5432/ktb\1#g' \
  -e 's#createdb -U ktb news_test#createdb -U ktb ktb_test#' \
  compose.dev.yaml .github/workflows/ci-dev.yaml .github/workflows/ci-main.yaml AGENTS.md README.md
```
Then edit by hand:
- `AGENTS.md`, the `KTB_TEST_POSTGRES_DSN` row: ``point it at `news_test`, never `news` `` → ``point it at `ktb_test`, never `ktb` ``.
- `AGENTS.md`, the comment above the test commands: `never at \`news\`` → `never at \`ktb\``.
- `README.md`, the `KTB_TEST_POSTGRES_DSN` row: `` `news`가 아닌 `news_test`를 `` → `` `ktb`가 아닌 `ktb_test`를 ``.
- `AGENTS.md` Commands block: after the `docker compose -f compose.dev.yaml up -d` line add
```bash
# once, on a volume created before the rename:
docker compose -f compose.dev.yaml exec postgres psql -U ktb -d postgres -c "ALTER DATABASE news RENAME TO ktb"
```

- [ ] **Step 2: Verify nothing still points at `news`**

```bash
git grep -n -E "5432/news|POSTGRES_DB: news|-d news\"|news_test|never (at )?.news.|아닌 .news_test" -- . ':!docs' ':!uv.lock'
```
Expected: no output. (`news-preprocessor`, `news_clusterer` and other service names are fine and are not matched.)

- [ ] **Step 3: Rename the local databases** (the dev volume already exists)

```bash
docker compose -f compose.dev.yaml exec -T postgres psql -U ktb -d postgres -c "ALTER DATABASE news RENAME TO ktb"
docker compose -f compose.dev.yaml exec -T postgres psql -U ktb -d postgres -c "ALTER DATABASE news_test RENAME TO ktb_test"
docker compose -f compose.dev.yaml up -d postgres
docker compose -f compose.dev.yaml ps postgres
```
Expected: two `ALTER DATABASE`; postgres reports `healthy` with the new `pg_isready -d ktb` healthcheck. If a rename fails with "being accessed by other users", stop and report.

- [ ] **Step 4: Confirm the migrated state survived and tests run against `ktb_test`**

```bash
KTB_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb uv run alembic current
KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test uv run pytest -q
```
Expected: `alembic current` prints `0004 (head)`; tests pass.

- [ ] **Step 5: Commit**

```bash
git add compose.dev.yaml .github/workflows AGENTS.md README.md
git commit -m "chore: rename the Postgres database from news to ktb

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 1: Carry over migration 0005

**Files:**
- Create (cherry-pick): `infrastructure/postgres/migrations/versions/0005_create_portfolios.py`
- Modify (cherry-pick): `infrastructure/postgres/tests/test_migrations.py`

**Interfaces:**
- Produces: tables `portfolios(id bigint identity, created_at, cash_weight double, commentary text, model text)`, `portfolio_holdings(portfolio_id, company_id FK companies.corp_code, weight, reason null, cited_cluster_ids bigint[])`, `portfolio_exits(portfolio_id, company_id, reason, cited_cluster_ids)`, index `portfolios_created_at_idx`, and GIN index `cluster_summaries_fts_idx` on `to_tsvector('simple', title || ' ' || summary)`.

- [ ] **Step 1: Cherry-pick the migration commit from the TypeScript branch**

```bash
git cherry-pick 7d67be3
```
Expected: a clean cherry-pick creating `0005_create_portfolios.py` and adding four tests to `infrastructure/postgres/tests/test_migrations.py`. If it conflicts, stop and report; do not hand-resolve.

- [ ] **Step 2: Apply migrations to the test database**

```bash
KTB_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test uv run alembic upgrade head
```
Expected: `Running upgrade 0004 -> 0005`.

- [ ] **Step 3: Run the migration tests**

```bash
KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test uv run pytest infrastructure/postgres -q
```
Expected: all pass (including `test_portfolio_rows_reference_companies_and_cascade_from_portfolios`, `test_cluster_summaries_have_a_full_text_index`, `test_downgrade_to_0004_removes_the_portfolio_tables`).

- [ ] **Step 4: No extra commit needed** (the cherry-pick is the commit). Confirm with `git log --oneline -1`.

---

### Task 2: Move the company-name normaliser into `ktb_core`

**Files:**
- Create: `packages/core/src/ktb_core/normalize.py`
- Create: `packages/core/tests/test_normalize.py`
- Delete: `services/news-graph-builder/src/news_graph_builder/common/normalize.py`, `services/news-graph-builder/src/news_graph_builder/common/__init__.py`, `services/news-graph-builder/tests/common/test_normalize.py`
- Modify: `services/news-graph-builder/src/news_graph_builder/company/repository.py`, `company/service.py`, `graph/repository.py`, `graph/service.py`, `theme/service.py` (import line only)
- Modify: `tach.toml`

**Interfaces:**
- Produces: `ktb_core.normalize.normalize(text: str) -> str` (unchanged behaviour: NFKC width folding, strip `(주)`/`㈜`/`주식회사`, remove whitespace, casefold).

- [ ] **Step 1: Write the test at its new home**

`packages/core/tests/test_normalize.py`:
```python
import pytest
from ktb_core.normalize import normalize


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("삼성전자", "삼성전자"),
        ("삼성전자(주)", "삼성전자"),
        ("(주) 삼성전자", "삼성전자"),
        ("㈜LG화학", "lg화학"),
        ("㈜ 삼성 전자", "삼성전자"),
        ("주식회사 카카오", "카카오"),
        ("삼성전자 주식회사", "삼성전자"),
        (" SK 하이닉스\t", "sk하이닉스"),
        ("SK hynix Inc.", "skhynixinc."),
        ("LG Energy Solution", "lgenergysolution"),
        ("(주)", ""),
        ("（주）삼성전자", "삼성전자"),
        ("ＬＧ화학", "lg화학"),
    ],
)
def test_normalize(text, expected):
    assert normalize(text) == expected
```

- [ ] **Step 2: Run it to see it fail**

Run: `uv run pytest packages/core/tests/test_normalize.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'ktb_core.normalize'`.

- [ ] **Step 3: Move the module**

```bash
git mv services/news-graph-builder/src/news_graph_builder/common/normalize.py packages/core/src/ktb_core/normalize.py
git rm -q services/news-graph-builder/src/news_graph_builder/common/__init__.py services/news-graph-builder/tests/common/test_normalize.py
```
The moved file keeps its content exactly:
```python
import re
import unicodedata


def normalize(text: str) -> str:
    for step in (fold_width, remove_corporate_markers, remove_whitespace, str.casefold):
        text = step(text)
    return text


def fold_width(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def remove_corporate_markers(text: str) -> str:
    corporate_markers = re.compile(r"\(주\)|㈜|주식회사")
    return corporate_markers.sub("", text)


def remove_whitespace(text: str) -> str:
    return "".join(text.split())
```

- [ ] **Step 4: Update the five news-graph-builder imports**

In each of `company/repository.py`, `company/service.py`, `graph/repository.py`, `graph/service.py`, `theme/service.py` under `services/news-graph-builder/src/news_graph_builder/`, replace
```python
from news_graph_builder.common import normalize
```
with
```python
from ktb_core.normalize import normalize
```
Verify nothing else references the old path: `git grep -n "news_graph_builder.common"` must print nothing outside `tach.toml` and `docs/`.

- [ ] **Step 5: Update `tach.toml`**

Delete this module block:
```toml
[[modules]]
path = "news_graph_builder.common"
depends_on = []
```
Delete this interface block:
```toml
[[interfaces]]
expose = ["normalize"]
from = ["news_graph_builder.common"]
```
In the `depends_on` lists of `news_graph_builder.theme`, `news_graph_builder.company` and `news_graph_builder.graph`, replace `"news_graph_builder.common"` with `"ktb_core"`.

- [ ] **Step 6: Run the tests and boundary checks**

```bash
uv run pytest packages/core services/news-graph-builder -q
uv run tach check
```
Expected: all pass (DB tests skip without `KTB_TEST_POSTGRES_DSN`); tach reports no errors.

- [ ] **Step 7: Commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add -A packages/core services/news-graph-builder tach.toml
git commit -m "refactor: move the company-name normaliser into ktb_core

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Structured fields and secret redaction in `ktb_core.logging`

**Files:**
- Modify: `packages/core/src/ktb_core/logging.py`
- Modify: `packages/core/tests/test_logging.py`

**Interfaces:**
- Produces: a log record may carry `extra={"fields": {...}}`; the formatter merges that dict into the JSON payload. Every emitted line is passed through `redact(line: str) -> str`, which masks `sk-or-…` keys, JWT-shaped strings and `access_token`/`refresh_token`/`id_token` JSON fields (also when the quotes are backslash-escaped inside a string).

- [ ] **Step 1: Add the failing tests** (append to `packages/core/tests/test_logging.py`)

```python
def test_merges_structured_fields(capsys):
    setup_logging("INFO")
    logging.getLogger("svc").info("run_end", extra={"fields": {"outcome": "saved", "turns": 3}})

    payload = json.loads(capsys.readouterr().out.strip())

    assert payload["message"] == "run_end"
    assert payload["outcome"] == "saved"
    assert payload["turns"] == 3


def test_serialises_values_json_cannot(capsys):
    from datetime import UTC, datetime

    setup_logging("INFO")
    moment = datetime(2026, 9, 28, tzinfo=UTC)
    logging.getLogger("svc").info("x", extra={"fields": {"at": moment}})

    payload = json.loads(capsys.readouterr().out.strip())

    assert payload["at"] == str(moment)


def test_redacts_secrets_anywhere_in_the_line(capsys):
    setup_logging("INFO")
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl"
    logging.getLogger("svc").error(
        "failed",
        extra={
            "fields": {
                "error": 'Bearer sk-or-v1-0123abcDEF rejected; body {"access_token": "abc"}',
                "token": jwt,
            }
        },
    )

    line = capsys.readouterr().out
    payload = json.loads(line)

    assert "sk-or-v1-0123abcDEF" not in line
    assert "abc\\\"" not in line
    assert jwt not in line
    assert "[redacted-key]" in payload["error"]
    assert '\\"access_token\\":\\"[redacted]\\"' in line
    assert payload["token"] == "[redacted-jwt]"
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest packages/core/tests/test_logging.py -q`
Expected: the three new tests FAIL (`KeyError: 'outcome'` etc.).

- [ ] **Step 3: Implement** — replace `packages/core/src/ktb_core/logging.py` with:

```python
"""Structured JSON logging shared by every service."""

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

# Matches both plain and backslash-escaped quotes, since a token response is often embedded in
# an error message string that json.dumps escapes a second time.
_TOKEN_FIELD = re.compile(r'(\\?)"(access|refresh|id)_token(\\?)"\s*:\s*(\\?)"[^"\\]*(\\?)"')
_JWT = re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]+")
_OPENROUTER_KEY = re.compile(r"sk-or-[\w-]+")


def redact(line: str) -> str:
    line = _TOKEN_FIELD.sub(r'\1"\2_token\3":\4"[redacted]\5"', line)
    line = _JWT.sub("[redacted-jwt]", line)
    return _OPENROUTER_KEY.sub("[redacted-key]", line)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update(fields)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return redact(json.dumps(payload, ensure_ascii=False, default=str))


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest packages/core -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add packages/core
git commit -m "feat: structured log fields and secret redaction in ktb_core

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: portfolio-builder package foundation

Replaces the stub: dependencies, settings, table mirrors, the `log` helper and the tool error types.

**Files:**
- Modify: `services/portfolio-builder/pyproject.toml`
- Replace: `services/portfolio-builder/src/portfolio_builder/settings.py`
- Create: `services/portfolio-builder/src/portfolio_builder/database.py`, `log.py`, `errors.py`
- Replace: `services/portfolio-builder/src/portfolio_builder/__main__.py` (temporary minimal body, finished in Task 13)
- Replace: `services/portfolio-builder/tests/test_settings.py`, `services/portfolio-builder/tests/test_main.py`
- Create: `services/portfolio-builder/tests/test_log.py`
- Modify: `infrastructure/postgres/tests/test_migrations.py`, `tach.toml`, `uv.lock`

**Interfaces:**
- Produces:
  - `portfolio_builder.settings.Settings` with fields `postgres_dsn: str`, `questdb_conf: str`, `openrouter_api_key: SecretStr`, `llm_model: str`, `thinking_level: Literal["none","minimal","low","medium","high","xhigh"] = "medium"`, `news_window_days: int = 7`, `max_turns: int = 150`, `log_level: str = "INFO"`.
  - `portfolio_builder.database.metadata`, `portfolios`, `portfolio_holdings`, `portfolio_exits` (`sa.Table`).
  - `portfolio_builder.log.Log` (type alias) and `make_log(run_id: str) -> Log`; calling `log(event: str, level: int = logging.INFO, **fields)` emits one JSON line whose `message` is `event` and which carries `run_id` plus `fields`.
  - `portfolio_builder.errors.ToolError(Exception)`, and subclasses `PortfolioRejected(errors: list[str])` (attribute `.errors`), `UnknownCompany`, `NoMarketData`, `GraphTimeout`.

- [ ] **Step 1: Replace `services/portfolio-builder/pyproject.toml`**

```toml
[project]
name = "portfolio-builder"
version = "0.1.0"
description = "Cron-triggered model portfolio agent"
requires-python = ">=3.13,<3.15"
dependencies = [
    "ktb-core",
    "langchain>=1.4.2",
    "langchain-openrouter>=0.2.9",
    "langgraph>=1.2.12",
    "langchain-core>=1.6.5",
    "numpy>=2.3",
    "pandas>=2.2",
    "psycopg[binary]>=3.3.6",
    "pydantic>=2.9",
    "pydantic-settings>=2.7",
    "questdb>=5.0",
    "sqlalchemy>=2.0.54",
    "ta-lib>=0.8.1",
]

[project.scripts]
portfolio-builder = "portfolio_builder.__main__:main"

[tool.uv.sources]
ktb-core = { workspace = true }

[tool.deptry.per_rule_ignores]
# questdb's QueryResult.to_pandas() needs pandas installed; nothing imports it by name.
DEP002 = ["pandas"]

[build-system]
requires = ["uv_build>=0.12,<0.13"]
build-backend = "uv_build"
```

- [ ] **Step 2: Lock and sync**

```bash
uv lock
uv sync --all-packages --group migrations
```
Expected: `uv.lock` updated; `uv run python -c "import langchain, langchain_openrouter, talib, questdb"` succeeds.

- [ ] **Step 3: Update `tach.toml`**

Replace
```toml
[[modules]]
path = "portfolio_builder"
depends_on = ["ktb_core", "ktb_market_analyzer"]
```
with
```toml
[[modules]]
path = "portfolio_builder"
depends_on = ["ktb_core"]
```

- [ ] **Step 4: Write the failing tests**

`services/portfolio-builder/tests/test_settings.py` (replace the file):
```python
import pytest
from portfolio_builder.settings import Settings
from pydantic import ValidationError

REQUIRED = {
    "PORTFOLIO_BUILDER_POSTGRES_DSN": "postgresql+psycopg://ktb:ktb@localhost:5432/ktb",
    "PORTFOLIO_BUILDER_QUESTDB_CONF": "http::addr=localhost:9000;",
    "PORTFOLIO_BUILDER_OPENROUTER_API_KEY": "sk-or-v1-test",
    "PORTFOLIO_BUILDER_LLM_MODEL": "openai/gpt-5.5",
}


def _populate(monkeypatch, **overrides):
    for name, value in {**REQUIRED, **overrides}.items():
        monkeypatch.setenv(name, value)


def test_loads_required_values_and_defaults(monkeypatch):
    _populate(monkeypatch)

    settings = Settings()

    assert settings.postgres_dsn == REQUIRED["PORTFOLIO_BUILDER_POSTGRES_DSN"]
    assert settings.questdb_conf == "http::addr=localhost:9000;"
    assert settings.openrouter_api_key.get_secret_value() == "sk-or-v1-test"
    assert settings.llm_model == "openai/gpt-5.5"
    assert settings.thinking_level == "medium"
    assert settings.news_window_days == 7
    assert settings.max_turns == 150
    assert settings.log_level == "INFO"


def test_the_key_never_appears_in_repr(monkeypatch):
    _populate(monkeypatch)

    assert "sk-or-v1-test" not in repr(Settings())


@pytest.mark.parametrize("missing", sorted(REQUIRED))
def test_each_required_value_is_required(monkeypatch, missing):
    _populate(monkeypatch)
    monkeypatch.delenv(missing)

    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("PORTFOLIO_BUILDER_THINKING_LEVEL", "extreme"),
        ("PORTFOLIO_BUILDER_MAX_TURNS", "0"),
        ("PORTFOLIO_BUILDER_NEWS_WINDOW_DAYS", "-1"),
    ],
)
def test_rejects_invalid_values(monkeypatch, name, value):
    _populate(monkeypatch, **{name: value})

    with pytest.raises(ValidationError):
        Settings()
```

`services/portfolio-builder/tests/test_log.py`:
```python
import json
import logging

from ktb_core.logging import setup_logging
from portfolio_builder.log import make_log


def test_log_emits_event_with_run_id_and_fields(capsys):
    setup_logging("DEBUG")
    log = make_log("run-1")

    log("tool_call", logging.WARNING, name="search_graph", is_error=True)

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["message"] == "tool_call"
    assert payload["level"] == "WARNING"
    assert payload["run_id"] == "run-1"
    assert payload["name"] == "search_graph"
    assert payload["is_error"] is True
```

`services/portfolio-builder/tests/test_main.py` (replace; the real tests arrive in Task 13):
```python
import talib


def test_ta_lib_is_importable():
    assert "ROCP" in talib.get_functions()
```

- [ ] **Step 5: Run to see them fail**

Run: `uv run pytest services/portfolio-builder -q`
Expected: FAIL (`ValidationError`/`AttributeError` on the old settings, `ModuleNotFoundError: portfolio_builder.log`).

- [ ] **Step 6: Implement**

`services/portfolio-builder/src/portfolio_builder/settings.py`:
```python
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PORTFOLIO_BUILDER_", extra="ignore")

    postgres_dsn: str
    questdb_conf: str
    openrouter_api_key: SecretStr
    llm_model: str
    thinking_level: Literal["none", "minimal", "low", "medium", "high", "xhigh"] = "medium"
    news_window_days: int = Field(default=7, gt=0)
    max_turns: int = Field(default=150, gt=0)
    log_level: str = "INFO"
```

`services/portfolio-builder/src/portfolio_builder/database.py`:
```python
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# Mirrors infrastructure/postgres/migrations for the tables this service writes; the migrations
# own the schema. Reads use plain SQL.
metadata = sa.MetaData()

companies = sa.Table(
    "companies",
    metadata,
    sa.Column("corp_code", sa.Text, primary_key=True),
)

portfolios = sa.Table(
    "portfolios",
    metadata,
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
    sa.Index("portfolios_created_at_idx", "created_at"),
)

portfolio_holdings = sa.Table(
    "portfolio_holdings",
    metadata,
    sa.Column(
        "portfolio_id",
        sa.BigInteger,
        sa.ForeignKey("portfolios.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column("company_id", sa.Text, sa.ForeignKey("companies.corp_code"), primary_key=True),
    sa.Column("weight", sa.Double, nullable=False),
    sa.Column("reason", sa.Text, nullable=True),
    sa.Column(
        "cited_cluster_ids",
        postgresql.ARRAY(sa.BigInteger),
        nullable=False,
        server_default=sa.text("'{}'"),
    ),
)

portfolio_exits = sa.Table(
    "portfolio_exits",
    metadata,
    sa.Column(
        "portfolio_id",
        sa.BigInteger,
        sa.ForeignKey("portfolios.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column("company_id", sa.Text, sa.ForeignKey("companies.corp_code"), primary_key=True),
    sa.Column("reason", sa.Text, nullable=False),
    sa.Column(
        "cited_cluster_ids",
        postgresql.ARRAY(sa.BigInteger),
        nullable=False,
        server_default=sa.text("'{}'"),
    ),
)
```

`services/portfolio-builder/src/portfolio_builder/log.py`:
```python
import logging
from collections.abc import Callable
from typing import Any

Log = Callable[..., None]


def make_log(run_id: str) -> Log:
    logger = logging.getLogger("portfolio_builder")

    def log(event: str, level: int = logging.INFO, **fields: Any) -> None:
        logger.log(level, event, extra={"fields": {"run_id": run_id, **fields}})

    return log
```

`services/portfolio-builder/src/portfolio_builder/errors.py`:
```python
class ToolError(Exception):
    """A failure the model can correct; it is returned to the model instead of ending the run."""


class PortfolioRejected(ToolError):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(
            "The portfolio was not saved. Fix every error and call submit_portfolio again:\n- "
            + "\n- ".join(errors)
        )


class UnknownCompany(ToolError):
    pass


class NoMarketData(ToolError):
    pass


class GraphTimeout(ToolError):
    pass
```

`services/portfolio-builder/src/portfolio_builder/__main__.py` (temporary; Task 13 replaces it):
```python
from portfolio_builder.settings import Settings


def main() -> None:
    Settings()


if __name__ == "__main__":
    main()
```

- [ ] **Step 7: Register the table mirror in the migration test**

In `infrastructure/postgres/tests/test_migrations.py`, inside the `@pytest.mark.parametrize(("module", "owned_tables"), [...])` list of `test_service_tables_match_the_migrated_schema`, add this entry after the `news_graph_builder.database` entry:
```python
        ("portfolio_builder.database", {"portfolios", "portfolio_holdings", "portfolio_exits"}),
```
The cherry-picked `only_owned_tables` already skips `cluster_summaries_fts_idx`.

- [ ] **Step 8: Run the tests and checks**

```bash
uv run pytest services/portfolio-builder -q
KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test uv run pytest infrastructure/postgres -q
uv run tach check
```
Expected: all pass.

- [ ] **Step 9: Commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add -A services/portfolio-builder infrastructure/postgres/tests tach.toml uv.lock
git commit -m "feat: portfolio-builder settings, table mirrors, log helper and tool errors

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Validate, normalise and save a portfolio

**Files:**
- Create: `services/portfolio-builder/src/portfolio_builder/portfolio.py`
- Create: `services/portfolio-builder/tests/conftest.py`
- Create: `services/portfolio-builder/tests/test_portfolio.py`

**Interfaces:**
- Consumes: `portfolio_builder.database` tables; `portfolio_builder.errors.PortfolioRejected`.
- Produces:
  - Pydantic models `Holding(company_id: str, weight: float, reason: str | None = None, cited_cluster_ids: list[int])`, `Exit(company_id: str, reason: str, cited_cluster_ids: list[int])`, `Submission(holdings: list[Holding], exits: list[Exit], cash_weight: float, commentary: str)`.
  - `validate_portfolio(submission: Submission, previous: frozenset[str]) -> list[str]`
  - `normalize_weights(submission: Submission) -> Submission`
  - `save_portfolio(engine: sa.Engine, submission: Submission, model: str) -> int` — raises `PortfolioRejected` on an unknown company or unknown cited cluster, after rolling back.
  - Test fixture `engine` (in `tests/conftest.py`): a `sa.Engine` on the test DB, seeded with the fixture below. Company ids: Samsung `00126380` (stock `005930`), SK Hynix `00164779` (`000660`), LG Energy Solution `01515323` (`373220`). Cluster 1 is inside a 7-day window, cluster 2 is 30 days old.

- [ ] **Step 1: Create the seeded fixture** — `services/portfolio-builder/tests/conftest.py`:

```python
import pytest
import sqlalchemy as sa

TABLES = (
    "portfolio_exits, portfolio_holdings, portfolios, relations, cluster_entities, entities,"
    " cluster_summaries, article_clusters, clusters, articles, theme_companies, themes,"
    " company_aliases, companies"
)

SEED = [
    "INSERT INTO companies (corp_code, stock_code, corp_name) VALUES"
    " ('00126380', '005930', '삼성전자'), ('00164779', '000660', 'SK하이닉스'),"
    " ('01515323', '373220', 'LG에너지솔루션')",
    "INSERT INTO company_aliases (alias, corp_code) VALUES"
    " ('삼성전자', '00126380'), ('sk하이닉스', '00164779'), ('lg에너지솔루션', '01515323')",
    "INSERT INTO clusters (id, updated_at) OVERRIDING SYSTEM VALUE VALUES"
    " (1, now()), (2, now() - interval '30 days')",
    "INSERT INTO cluster_summaries (cluster_id, title, summary, cluster_updated_at) VALUES"
    " (1, '삼성전자 HBM 공급 확대', '삼성전자가 엔비디아에 HBM을 공급한다.', now()),"
    " (2, '반도체 수출 둔화', 'SK하이닉스가 HBM 생산을 늘린다.', now() - interval '30 days')",
    "INSERT INTO articles"
    " (id, source, external_id, url, title, body, published_at, raw_payload)"
    " OVERRIDING SYSTEM VALUE VALUES"
    " (1, 'yonhap', 'a1', 'https://example.com/1', '삼성 HBM 공급', '본문', now(), '{}'),"
    " (2, 'yonhap', 'a2', 'https://example.com/2', '엔비디아 HBM 조달', '본문', now(), '{}'),"
    " (3, 'yonhap', 'a3', 'https://example.com/3', '하이닉스 증산', '본문',"
    " now() - interval '30 days', '{}')",
    "INSERT INTO article_clusters (article_id, cluster_id) VALUES (1, 1), (2, 1), (3, 2)",
    "INSERT INTO entities (id, raw_name, name, type, corp_code) OVERRIDING SYSTEM VALUE VALUES"
    " (1, '삼성전자', '삼성전자', 'company', '00126380'),"
    " (2, '엔비디아', '엔비디아', 'company', NULL),"
    " (3, 'SK하이닉스', 'sk하이닉스', 'company', '00164779'),"
    " (4, 'HBM', 'hbm', 'product', NULL)",
    "INSERT INTO cluster_entities (cluster_id, entity_id) VALUES"
    " (1, 1), (1, 2), (1, 4), (2, 3), (2, 4)",
    "INSERT INTO relations"
    " (id, cluster_id, source_entity_id, target_entity_id, type, description)"
    " OVERRIDING SYSTEM VALUE VALUES"
    " (1, 1, 1, 2, 'supplies', '삼성전자가 엔비디아에 HBM을 공급'),"
    " (2, 1, 4, 2, 'used_by', 'HBM은 엔비디아 GPU에 쓰인다'),"
    " (3, 2, 3, 4, 'produces', 'SK하이닉스가 HBM을 생산')",
    "INSERT INTO themes (theme_code, name) VALUES ('T1', 'HBM')",
    "INSERT INTO theme_companies (theme_code, corp_code, is_main) VALUES"
    " ('T1', '00126380', true), ('T1', '00164779', false)",
]


def _truncate(engine: sa.Engine) -> None:
    with engine.begin() as conn:
        conn.execute(sa.text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))


@pytest.fixture
def engine(pg_engine):
    # The code under test commits, so empty the tables instead of rolling back.
    _truncate(pg_engine)
    with pg_engine.begin() as conn:
        for statement in SEED:
            conn.execute(sa.text(statement))
    yield pg_engine
    _truncate(pg_engine)
```

If an `INSERT` fails because a column does not exist on `dev`'s schema (for example `themes` needs more NOT NULL columns), read `infrastructure/postgres/migrations/versions/0004_create_themes.py` and add literal values for the missing NOT NULL columns; do not change the tested rows.

- [ ] **Step 2: Write the failing tests** — `services/portfolio-builder/tests/test_portfolio.py`:

```python
import pytest
import sqlalchemy as sa
from portfolio_builder.errors import PortfolioRejected
from portfolio_builder.portfolio import (
    Exit,
    Holding,
    Submission,
    normalize_weights,
    save_portfolio,
    validate_portfolio,
)

SAMSUNG, HYNIX = "00126380", "00164779"

BASE = Submission(
    holdings=[Holding(company_id="A", weight=1, reason="news", cited_cluster_ids=[1])],
    exits=[],
    cash_weight=0,
    commentary="overall",
)


def test_accepts_a_first_portfolio_whose_entries_all_have_reasons():
    assert validate_portfolio(BASE, frozenset()) == []


def test_a_held_company_keeps_its_place_without_a_new_reason():
    submission = BASE.model_copy(
        update={"holdings": [Holding(company_id="A", weight=1, cited_cluster_ids=[])]}
    )
    assert validate_portfolio(submission, frozenset({"A"})) == []


def test_an_exited_previous_holding_needs_only_its_exit():
    submission = BASE.model_copy(
        update={"exits": [Exit(company_id="B", reason="guidance cut", cited_cluster_ids=[2])]}
    )
    assert validate_portfolio(submission, frozenset({"B"})) == []


def test_reports_every_problem_at_once_in_order():
    submission = Submission(
        holdings=[
            Holding(company_id="A", weight=-1, cited_cluster_ids=[]),
            Holding(company_id="A", weight=0, reason="dup", cited_cluster_ids=[]),
            Holding(company_id="C", weight=0, reason="  ", cited_cluster_ids=[]),
        ],
        exits=[
            Exit(company_id="A", reason="both", cited_cluster_ids=[]),
            Exit(company_id="Z", reason="", cited_cluster_ids=[]),
        ],
        cash_weight=-0.5,
        commentary=" ",
    )

    assert validate_portfolio(submission, frozenset({"B"})) == [
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
    ]


def test_normalize_weights_scales_holdings_and_cash_to_one():
    submission = BASE.model_copy(
        update={
            "holdings": [
                Holding(company_id="A", weight=3, reason="r", cited_cluster_ids=[]),
                Holding(company_id="B", weight=1, reason="r", cited_cluster_ids=[]),
            ],
            "cash_weight": 4,
        }
    )

    normalized = normalize_weights(submission)

    assert [h.weight for h in normalized.holdings] == [0.375, 0.125]
    assert normalized.cash_weight == 0.5
    assert submission.cash_weight == 4


SAVED = Submission(
    holdings=[
        Holding(company_id=SAMSUNG, weight=0.6, reason="HBM 공급", cited_cluster_ids=[1]),
        Holding(company_id=HYNIX, weight=0.2, cited_cluster_ids=[]),
    ],
    exits=[],
    cash_weight=0.2,
    commentary="총평",
)


def _count(engine) -> int:
    with engine.connect() as conn:
        return conn.execute(sa.text("SELECT count(*) FROM portfolios")).scalar_one()


def test_save_writes_the_portfolio_holdings_and_citations(engine):
    portfolio_id = save_portfolio(engine, SAVED, "openrouter/openai/gpt-5.5")

    with engine.connect() as conn:
        portfolio = conn.execute(
            sa.text("SELECT cash_weight, commentary, model FROM portfolios WHERE id = :id"),
            {"id": portfolio_id},
        ).one()
        holdings = conn.execute(
            sa.text(
                "SELECT company_id, weight, reason, cited_cluster_ids FROM portfolio_holdings"
                " WHERE portfolio_id = :id ORDER BY company_id"
            ),
            {"id": portfolio_id},
        ).all()
    assert tuple(portfolio) == (0.2, "총평", "openrouter/openai/gpt-5.5")
    assert [tuple(h) for h in holdings] == [
        (SAMSUNG, 0.6, "HBM 공급", [1]),
        (HYNIX, 0.2, None, []),
    ]


def test_save_rolls_back_an_unknown_company_with_a_readable_error(engine):
    bad = SAVED.model_copy(
        update={
            "holdings": [
                Holding(company_id="99999999", weight=1, reason="x", cited_cluster_ids=[])
            ]
        }
    )

    with pytest.raises(PortfolioRejected) as rejected:
        save_portfolio(engine, bad, "m")

    assert "99999999" in " ".join(rejected.value.errors)
    assert _count(engine) == 0


def test_save_rolls_back_an_unknown_cited_cluster(engine):
    bad = SAVED.model_copy(
        update={
            "holdings": [
                Holding(company_id=SAMSUNG, weight=1, reason="x", cited_cluster_ids=[1, 404])
            ]
        }
    )

    with pytest.raises(PortfolioRejected) as rejected:
        save_portfolio(engine, bad, "m")

    assert rejected.value.errors == ["cited_cluster_ids not found: 404"]
    assert _count(engine) == 0
```

- [ ] **Step 3: Run to see them fail**

Run: `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test uv run pytest services/portfolio-builder/tests/test_portfolio.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'portfolio_builder.portfolio'`.

- [ ] **Step 4: Implement** — `services/portfolio-builder/src/portfolio_builder/portfolio.py`:

```python
from typing import Annotated

import sqlalchemy as sa
from pydantic import BaseModel, Field

from portfolio_builder.database import portfolio_exits, portfolio_holdings, portfolios
from portfolio_builder.errors import PortfolioRejected

# No numeric constraints here on purpose: validate_portfolio reports every problem at once,
# which a Pydantic ValidationError on the first bad field would not.
CitedClusters = Annotated[list[int], Field(description="cluster_ids this decision relies on")]


class Holding(BaseModel):
    company_id: str
    weight: float = Field(description="relative, non-negative")
    reason: str | None = Field(
        default=None, description="required for a company entering the portfolio"
    )
    cited_cluster_ids: CitedClusters


class Exit(BaseModel):
    company_id: str
    reason: str
    cited_cluster_ids: CitedClusters


class Submission(BaseModel):
    holdings: list[Holding]
    exits: list[Exit]
    cash_weight: float
    commentary: str


def validate_portfolio(submission: Submission, previous: frozenset[str]) -> list[str]:
    errors: list[str] = []
    held: set[str] = set()
    for holding in submission.holdings:
        company = holding.company_id
        if not holding.weight >= 0:
            errors.append(f"holdings: {company} weight must be >= 0")
        if company not in previous and not (holding.reason or "").strip():
            errors.append(f"holdings: {company} is entering the portfolio and needs a reason")
        if company in held:
            errors.append(f"holdings: {company} appears more than once")
        held.add(company)

    exited: set[str] = set()
    for exit_ in submission.exits:
        company = exit_.company_id
        if company in exited:
            errors.append(f"exits: {company} appears more than once")
        exited.add(company)
        if company in held:
            errors.append(f"exits: {company} is both held and exited")
        if company not in previous:
            errors.append(f"exits: {company} was not in the previous portfolio")
        if not exit_.reason.strip():
            errors.append(f"exits: {company} needs a reason")
    for company in sorted(previous):
        if company not in held and company not in exited:
            errors.append(
                f"exits: {company} was held and is dropped, so it needs an exit with a reason"
            )

    if not submission.cash_weight >= 0:
        errors.append("cash_weight must be >= 0")
    total = sum(h.weight for h in submission.holdings) + submission.cash_weight
    if not total > 0:
        errors.append("weights and cash_weight sum to 0 or less; at least one must be positive")
    if not submission.commentary.strip():
        errors.append("commentary must not be empty")
    return errors


def normalize_weights(submission: Submission) -> Submission:
    total = sum(h.weight for h in submission.holdings) + submission.cash_weight
    return submission.model_copy(
        update={
            "holdings": [
                h.model_copy(update={"weight": h.weight / total}) for h in submission.holdings
            ],
            "cash_weight": submission.cash_weight / total,
        }
    )


def save_portfolio(engine: sa.Engine, submission: Submission, model: str) -> int:
    cited = sorted(
        {c for item in [*submission.holdings, *submission.exits] for c in item.cited_cluster_ids}
    )
    try:
        with engine.begin() as conn:
            portfolio_id = conn.execute(
                sa.insert(portfolios)
                .values(
                    cash_weight=submission.cash_weight,
                    commentary=submission.commentary,
                    model=model,
                )
                .returning(portfolios.c.id)
            ).scalar_one()
            if submission.holdings:
                conn.execute(
                    sa.insert(portfolio_holdings),
                    [
                        {"portfolio_id": portfolio_id, **h.model_dump()}
                        for h in submission.holdings
                    ],
                )
            if submission.exits:
                conn.execute(
                    sa.insert(portfolio_exits),
                    [{"portfolio_id": portfolio_id, **e.model_dump()} for e in submission.exits],
                )
            # An array column cannot carry a foreign key, so citations are checked here instead.
            found = set(
                conn.execute(
                    sa.text("SELECT id FROM clusters WHERE id = ANY(CAST(:ids AS bigint[]))"),
                    {"ids": cited},
                ).scalars()
            )
            missing = [c for c in cited if c not in found]
            if missing:
                raise PortfolioRejected(
                    [f"cited_cluster_ids not found: {', '.join(map(str, missing))}"]
                )
            return portfolio_id
    except sa.exc.IntegrityError as error:
        diag = getattr(error.orig, "diag", None)
        detail = getattr(diag, "message_detail", None) or str(error.orig)
        raise PortfolioRejected([detail]) from error
```

- [ ] **Step 5: Run the tests**

Run: `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test uv run pytest services/portfolio-builder -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add services/portfolio-builder
git commit -m "feat: validate, normalise and atomically save model portfolios

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: News tools

**Files:**
- Create: `services/portfolio-builder/src/portfolio_builder/tools/__init__.py`
- Create: `services/portfolio-builder/src/portfolio_builder/tools/news.py`
- Create: `services/portfolio-builder/tests/test_news_tools.py`

**Interfaces:**
- Consumes: `ToolError`; fixture `engine`.
- Produces:
  - `portfolio_builder.tools.to_json(value: object) -> str` — `json.dumps` with `ensure_ascii=False`; datetimes become ISO 8601.
  - `portfolio_builder.tools.news.to_prefix_query(query: str) -> str`
  - `portfolio_builder.tools.news.news_tools(engine: sa.Engine) -> list[BaseTool]` returning `[get_news_cluster, search_news_cluster]`. Both return JSON strings.

- [ ] **Step 1: Write the failing tests** — `services/portfolio-builder/tests/test_news_tools.py`:

```python
import json

import pytest
from portfolio_builder.errors import ToolError
from portfolio_builder.tools.news import news_tools, to_prefix_query


def test_builds_a_prefix_tsquery_and_drops_operators():
    assert to_prefix_query(" 삼성 HBM ") == "삼성:* & HBM:*"
    assert to_prefix_query("a&b | !c:*") == "ab:* & c:*"
    assert to_prefix_query("&&") == ""


def _tools(engine):
    return {t.name: t for t in news_tools(engine)}


def test_get_news_cluster_returns_summary_articles_entities_relations(engine):
    result = json.loads(_tools(engine)["get_news_cluster"].invoke({"id": 1}))

    assert result["cluster_id"] == 1
    assert result["title"] == "삼성전자 HBM 공급 확대"
    assert len(result["articles"]) == 2
    assert {"id": 1, "name": "삼성전자", "type": "company", "company_id": "00126380"} in result[
        "entities"
    ]
    assert [(r["source"], r["type"], r["target"]) for r in result["relations"]] == [
        ("삼성전자", "supplies", "엔비디아"),
        ("HBM", "used_by", "엔비디아"),
    ]


def test_get_news_cluster_rejects_an_unknown_id(engine):
    with pytest.raises(ToolError, match="999"):
        _tools(engine)["get_news_cluster"].invoke({"id": 999})


def test_search_matches_a_word_with_a_particle_attached(engine):
    # Cluster 2 contains only "SK하이닉스가"; a non-prefix query would miss it.
    result = json.loads(_tools(engine)["search_news_cluster"].invoke({"query": "SK하이닉스"}))

    assert [r["cluster_id"] for r in result] == [2]


def test_search_rejects_a_query_with_no_searchable_terms(engine):
    with pytest.raises(ToolError):
        _tools(engine)["search_news_cluster"].invoke({"query": "&|"})
```

- [ ] **Step 2: Run to see them fail**

Run: `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test uv run pytest services/portfolio-builder/tests/test_news_tools.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`services/portfolio-builder/src/portfolio_builder/tools/__init__.py`:
```python
import json
from datetime import date


def to_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        default=lambda v: v.isoformat() if isinstance(v, date) else str(v),
    )
```

`services/portfolio-builder/src/portfolio_builder/tools/news.py`:
```python
import re
from typing import Annotated

import sqlalchemy as sa
from langchain.tools import tool
from langchain_core.tools import BaseTool
from pydantic import Field

from portfolio_builder.errors import ToolError
from portfolio_builder.tools import to_json

_TSQUERY_OPERATORS = re.compile(r"[&|!():*<>'\\]")


def to_prefix_query(query: str) -> str:
    terms = (_TSQUERY_OPERATORS.sub("", term) for term in query.split())
    return " & ".join(f"{term}:*" for term in terms if term)


def news_tools(engine: sa.Engine) -> list[BaseTool]:
    @tool(
        "get_news_cluster",
        description=(
            "One news cluster by cluster_id: title, summary, member articles, entities and the"
            " relations extracted from it."
        ),
    )
    def get_news_cluster(id: Annotated[int, Field(description="cluster_id")]) -> str:
        with engine.connect() as conn:
            summary = (
                conn.execute(
                    sa.text(
                        "SELECT s.cluster_id, s.title, s.summary, c.updated_at"
                        " FROM cluster_summaries s JOIN clusters c ON c.id = s.cluster_id"
                        " WHERE s.cluster_id = :id"
                    ),
                    {"id": id},
                )
                .mappings()
                .first()
            )
            if summary is None:
                raise ToolError(f"news cluster {id} does not exist or has no summary yet")
            articles = conn.execute(
                sa.text(
                    "SELECT a.title, a.source, a.published_at"
                    " FROM article_clusters ac JOIN articles a ON a.id = ac.article_id"
                    " WHERE ac.cluster_id = :id ORDER BY a.published_at DESC"
                ),
                {"id": id},
            ).mappings()
            entities = conn.execute(
                sa.text(
                    "SELECT e.id, e.raw_name AS name, e.type, e.corp_code AS company_id"
                    " FROM cluster_entities ce JOIN entities e ON e.id = ce.entity_id"
                    " WHERE ce.cluster_id = :id ORDER BY e.id"
                ),
                {"id": id},
            ).mappings()
            relations = conn.execute(
                sa.text(
                    "SELECT s.raw_name AS source, r.type, t.raw_name AS target, r.description"
                    " FROM relations r"
                    " JOIN entities s ON s.id = r.source_entity_id"
                    " JOIN entities t ON t.id = r.target_entity_id"
                    " WHERE r.cluster_id = :id ORDER BY r.id"
                ),
                {"id": id},
            ).mappings()
            return to_json(
                {
                    **summary,
                    "articles": [dict(a) for a in articles],
                    "entities": [dict(e) for e in entities],
                    "relations": [dict(r) for r in relations],
                }
            )

    @tool(
        "search_news_cluster",
        description=(
            "Full-text search over every news cluster's title and summary (not only the briefing"
            " window). Every word must match as a prefix. Returns up to 10 clusters, best first."
        ),
    )
    def search_news_cluster(
        query: Annotated[str, Field(description="space-separated words")],
    ) -> str:
        tsquery = to_prefix_query(query)
        if not tsquery:
            raise ToolError("query has no searchable words")
        # The to_tsvector expression must match cluster_summaries_fts_idx exactly.
        with engine.connect() as conn:
            rows = conn.execute(
                sa.text(
                    "SELECT s.cluster_id, s.title, left(s.summary, 200) AS excerpt,"
                    " ts_rank(to_tsvector('simple', s.title || ' ' || s.summary), q) AS rank"
                    " FROM cluster_summaries s, to_tsquery('simple', :q) AS q"
                    " WHERE to_tsvector('simple', s.title || ' ' || s.summary) @@ q"
                    " ORDER BY rank DESC, s.cluster_id DESC LIMIT 10"
                ),
                {"q": tsquery},
            ).mappings()
            return to_json([dict(r) for r in rows])

    return [get_news_cluster, search_news_cluster]
```

- [ ] **Step 4: Run the tests**

Run: `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test uv run pytest services/portfolio-builder/tests/test_news_tools.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add services/portfolio-builder
git commit -m "feat: get_news_cluster and search_news_cluster tools

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Knowledge-graph tools

**Files:**
- Create: `services/portfolio-builder/src/portfolio_builder/tools/graph.py`
- Create: `services/portfolio-builder/tests/test_graph_tools.py`

**Interfaces:**
- Consumes: `ktb_core.normalize.normalize`, `ToolError`, `GraphTimeout`, `to_json`, fixture `engine`.
- Produces:
  - `find_seed_entities(conn: sa.Connection, name: str) -> list[int]` — raises `ToolError` listing up to 5 candidates when nothing matches.
  - `graph_transaction(engine: sa.Engine)` — context manager yielding a `sa.Connection` inside a transaction with `statement_timeout = '10s'`; a Postgres query cancel (SQLSTATE `57014`) becomes `GraphTimeout`.
  - `graph_tools(engine: sa.Engine) -> list[BaseTool]` returning `[search_graph, find_graph_paths]`.

- [ ] **Step 1: Write the failing tests** — `services/portfolio-builder/tests/test_graph_tools.py`:

```python
import json

import pytest
import sqlalchemy as sa
from portfolio_builder.errors import GraphTimeout, ToolError
from portfolio_builder.tools.graph import find_seed_entities, graph_tools, graph_transaction


def _tools(engine):
    return {t.name: t for t in graph_tools(engine)}


def test_seeds_match_normalised_names_and_company_aliases(engine):
    with engine.connect() as conn:
        assert find_seed_entities(conn, "㈜ 삼성전자") == [1]
        assert find_seed_entities(conn, "hbm") == [4]


def test_an_unknown_name_lists_candidates(engine):
    with engine.connect() as conn, pytest.raises(ToolError, match="삼성전자"):
        find_seed_entities(conn, "삼성바이오")


@pytest.mark.parametrize(
    ("depth", "expected"),
    [(1, [1, 2]), (2, [1, 2, 4]), (3, [1, 2, 4, 3])],
)
def test_search_graph_reaches_nodes_nearest_first(engine, depth, expected):
    result = json.loads(_tools(engine)["search_graph"].invoke({"name": "삼성전자", "depth": depth}))

    assert [n["id"] for n in result["nodes"]] == expected


def test_search_graph_returns_edges_between_reached_nodes(engine):
    result = json.loads(_tools(engine)["search_graph"].invoke({"name": "삼성전자", "depth": 2}))

    assert [
        (e["source"], e["type"], e["target"], e["cluster_id"], e["hop"]) for e in result["edges"]
    ] == [
        ("삼성전자", "supplies", "엔비디아", 1, 0),
        ("HBM", "used_by", "엔비디아", 1, 1),
    ]


def test_find_graph_paths_walks_both_directions_without_revisiting(engine):
    result = json.loads(
        _tools(engine)["find_graph_paths"].invoke(
            {"from_name": "삼성전자", "to_name": "SK하이닉스", "max_depth": 4}
        )
    )

    assert len(result["paths"]) == 1
    assert [
        (s["from"], s["to"], s["type"], s["direction"], s["cluster_id"])
        for s in result["paths"][0]["steps"]
    ] == [
        ("삼성전자", "엔비디아", "supplies", "forward", 1),
        ("엔비디아", "HBM", "used_by", "backward", 1),
        ("HBM", "SK하이닉스", "produces", "backward", 2),
    ]


def test_find_graph_paths_respects_max_depth(engine):
    result = json.loads(
        _tools(engine)["find_graph_paths"].invoke(
            {"from_name": "삼성전자", "to_name": "SK하이닉스", "max_depth": 2}
        )
    )

    assert result["paths"] == []


def test_find_graph_paths_does_not_revisit_a_node_on_a_cycle(engine):
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO relations"
                " (id, cluster_id, source_entity_id, target_entity_id, type, description)"
                " OVERRIDING SYSTEM VALUE VALUES (4, 1, 1, 4, 'related_to', '삼성전자와 HBM')"
            )
        )

    result = json.loads(
        _tools(engine)["find_graph_paths"].invoke(
            {"from_name": "삼성전자", "to_name": "SK하이닉스", "max_depth": 4}
        )
    )

    assert [p["length"] for p in result["paths"]] == [2, 3]


def test_graph_transaction_maps_a_statement_timeout(engine):
    with pytest.raises(GraphTimeout, match="timed out"), graph_transaction(engine) as conn:
        conn.execute(sa.text("SET LOCAL statement_timeout = '10ms'"))
        conn.execute(sa.text("SELECT pg_sleep(1)"))
```

- [ ] **Step 2: Run to see them fail**

Run: `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test uv run pytest services/portfolio-builder/tests/test_graph_tools.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement** — `services/portfolio-builder/src/portfolio_builder/tools/graph.py`:

```python
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

import sqlalchemy as sa
from ktb_core.normalize import normalize
from langchain.tools import tool
from langchain_core.tools import BaseTool
from pydantic import Field

from portfolio_builder.errors import GraphTimeout, ToolError
from portfolio_builder.tools import to_json

QUERY_CANCELED = "57014"


def _like(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def find_seed_entities(conn: sa.Connection, name: str) -> list[int]:
    normalized = normalize(name)
    if not normalized:
        raise ToolError("name must not be empty")
    ids = list(
        conn.execute(
            sa.text(
                "SELECT e.id FROM entities e WHERE e.name LIKE :pattern"
                " UNION"
                " SELECT e.id FROM company_aliases a JOIN entities e ON e.corp_code = a.corp_code"
                " WHERE a.alias = :alias"
                " ORDER BY id"
            ),
            {"pattern": _like(normalized), "alias": normalized},
        ).scalars()
    )
    if ids:
        return ids
    candidates = list(
        conn.execute(
            sa.text(
                "SELECT DISTINCT raw_name FROM entities WHERE name LIKE :pattern"
                " ORDER BY raw_name LIMIT 5"
            ),
            {"pattern": _like(normalized[:2])},
        ).scalars()
    )
    raise ToolError(
        f'no entity matches "{name}". Candidates: {", ".join(candidates) or "none"}'
    )


@contextmanager
def graph_transaction(engine: sa.Engine) -> Iterator[sa.Connection]:
    # ponytail: fixed 10 s cap, not a setting; a hub entity at high depth is the only slow case.
    try:
        with engine.begin() as conn:
            conn.execute(sa.text("SET LOCAL statement_timeout = '10s'"))
            yield conn
    except sa.exc.OperationalError as error:
        if getattr(error.orig, "sqlstate", None) == QUERY_CANCELED:
            raise GraphTimeout(
                "graph query timed out; use a smaller depth or a more specific name"
            ) from error
        raise


def graph_tools(engine: sa.Engine) -> list[BaseTool]:
    @tool(
        "search_graph",
        description=(
            "The knowledge-graph neighbourhood of an entity (company, product, person, ...) found"
            " by name: every entity within `depth` hops over relations in either direction, and"
            " every relation among them with the cluster_id it came from. Nearest first."
        ),
    )
    def search_graph(
        name: str,
        depth: Annotated[int, Field(ge=1, le=3)] = 2,
    ) -> str:
        with graph_transaction(engine) as conn:
            seeds = find_seed_entities(conn, name)
            nodes = [
                dict(n)
                for n in conn.execute(
                    sa.text(
                        "WITH RECURSIVE edges AS ("
                        "  SELECT source_entity_id AS a, target_entity_id AS b FROM relations"
                        "  UNION ALL"
                        "  SELECT target_entity_id, source_entity_id FROM relations"
                        "), walk(entity_id, hop) AS ("
                        "  SELECT id, 0 FROM unnest(CAST(:seeds AS bigint[])) AS s(id)"
                        "  UNION"
                        "  SELECT e.b, w.hop + 1 FROM walk w JOIN edges e ON e.a = w.entity_id"
                        "  WHERE w.hop < :depth"
                        ")"
                        " SELECT w.entity_id AS id, min(w.hop) AS hop, e.raw_name AS name,"
                        " e.type, e.corp_code AS company_id"
                        " FROM walk w JOIN entities e ON e.id = w.entity_id"
                        " GROUP BY w.entity_id, e.raw_name, e.type, e.corp_code"
                        " ORDER BY hop, id"
                    ),
                    {"seeds": seeds, "depth": depth},
                ).mappings()
            ]
            hops = {n["id"]: n["hop"] for n in nodes}
            rows = conn.execute(
                sa.text(
                    "SELECT r.id, r.source_entity_id, r.target_entity_id, s.raw_name AS source,"
                    " r.type, t.raw_name AS target, r.description, r.cluster_id"
                    " FROM relations r"
                    " JOIN entities s ON s.id = r.source_entity_id"
                    " JOIN entities t ON t.id = r.target_entity_id"
                    " WHERE r.source_entity_id = ANY(CAST(:ids AS bigint[]))"
                    " AND r.target_entity_id = ANY(CAST(:ids AS bigint[]))"
                    " ORDER BY r.id"
                ),
                {"ids": list(hops)},
            ).mappings()
            edges = sorted(
                (
                    {
                        "id": r["id"],
                        "source": r["source"],
                        "type": r["type"],
                        "target": r["target"],
                        "description": r["description"],
                        "cluster_id": r["cluster_id"],
                        "hop": min(hops[r["source_entity_id"]], hops[r["target_entity_id"]]),
                    }
                    for r in rows
                ),
                key=lambda e: (e["hop"], e["id"]),
            )
        return to_json({"nodes": nodes, "edges": edges})

    @tool(
        "find_graph_paths",
        description=(
            "Every simple path of at most max_depth relations between two entities in the"
            " knowledge graph, following relations in either direction, shortest first. Use it"
            " to see how an event or company reaches another company."
        ),
    )
    def find_graph_paths(
        from_name: str,
        to_name: str,
        max_depth: Annotated[int, Field(ge=1, le=6)] = 4,
    ) -> str:
        with graph_transaction(engine) as conn:
            sources = find_seed_entities(conn, from_name)
            targets = find_seed_entities(conn, to_name)
            paths = conn.execute(
                sa.text(
                    "WITH RECURSIVE edges AS ("
                    "  SELECT id AS relation_id, source_entity_id AS a, target_entity_id AS b,"
                    "  'forward'::text AS direction FROM relations"
                    "  UNION ALL"
                    "  SELECT id, target_entity_id, source_entity_id, 'backward'::text"
                    "  FROM relations"
                    "), paths(node, nodes, relation_ids, directions) AS ("
                    "  SELECT id, ARRAY[id], ARRAY[]::bigint[], ARRAY[]::text[]"
                    "  FROM unnest(CAST(:sources AS bigint[])) AS s(id)"
                    "  UNION ALL"
                    "  SELECT e.b, p.nodes || e.b, p.relation_ids || e.relation_id,"
                    "  p.directions || e.direction"
                    "  FROM paths p JOIN edges e ON e.a = p.node"
                    "  WHERE cardinality(p.relation_ids) < :max_depth"
                    "  AND e.b <> ALL(p.nodes)"
                    "  AND p.node <> ALL(CAST(:targets AS bigint[]))"
                    ")"
                    " SELECT nodes, relation_ids, directions FROM paths"
                    " WHERE node = ANY(CAST(:targets AS bigint[]))"
                    " AND cardinality(relation_ids) > 0"
                    " ORDER BY cardinality(relation_ids), nodes"
                ),
                {"sources": sources, "targets": targets, "max_depth": max_depth},
            ).all()
            node_ids = sorted({n for p in paths for n in p.nodes})
            relation_ids = sorted({r for p in paths for r in p.relation_ids})
            names = dict(
                conn.execute(
                    sa.text(
                        "SELECT id, raw_name FROM entities"
                        " WHERE id = ANY(CAST(:ids AS bigint[]))"
                    ),
                    {"ids": node_ids},
                ).all()
            )
            relations = {
                r["id"]: r
                for r in conn.execute(
                    sa.text(
                        "SELECT id, type, description, cluster_id FROM relations"
                        " WHERE id = ANY(CAST(:ids AS bigint[]))"
                    ),
                    {"ids": relation_ids},
                ).mappings()
            }
        return to_json(
            {
                "paths": [
                    {
                        "length": len(p.relation_ids),
                        "steps": [
                            {
                                "from": names[p.nodes[i]],
                                "to": names[p.nodes[i + 1]],
                                "type": relations[rid]["type"],
                                "direction": p.directions[i],
                                "description": relations[rid]["description"],
                                "cluster_id": relations[rid]["cluster_id"],
                            }
                            for i, rid in enumerate(p.relation_ids)
                        ],
                    }
                    for p in paths
                ]
            }
        )

    return [search_graph, find_graph_paths]
```

- [ ] **Step 4: Run the tests**

Run: `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test uv run pytest services/portfolio-builder/tests/test_graph_tools.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add services/portfolio-builder
git commit -m "feat: multi-hop search_graph and find_graph_paths tools

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: TA-Lib technical evidence (pure)

**Files:**
- Create: `services/portfolio-builder/src/portfolio_builder/evidence.py`
- Create: `services/portfolio-builder/tests/test_evidence.py`

**Interfaces:**
- Produces:
  - `TIMEFRAMES = ("1m", "15m", "1h", "1d")`
  - `Bars(NamedTuple)`: `high`, `low`, `close`, `volume` — `numpy.float64` arrays, oldest first.
  - `Evidence(NamedTuple)`: `values: dict[str, float | bool]`, `unavailable: dict[str, str]` (name → reason).
  - `compute_evidence(timeframe: str, bars: Bars, universe_closes: Mapping[str, Array] | None = None) -> Evidence`
  - Daily keys: `return_5d, return_20d, return_60d, ma_gap_20_60, distance_to_prev_20d_high, breakout_20d, realized_volatility_20d, relative_volume_20d, price_to_52w_high, momentum_12m_skip1m, volatility_percentile_1y, amihud_illiquidity_20d, amihud_percentile_1y, market_excess_return_5d, industry_excess_return_5d, return_5d_cross_section_percentile, momentum_cross_section_percentile`.
  - Intraday keys (exactly): `return_5, return_20, return_60, ma_gap_20_60, distance_to_prev_20_high, breakout_20, realized_volatility_20, relative_volume_20`.
  - Every key appears in exactly one of `values` / `unavailable`.

- [ ] **Step 1: Write the failing tests** — `services/portfolio-builder/tests/test_evidence.py`:

```python
import numpy as np
import pytest
from portfolio_builder.evidence import Bars, compute_evidence, cross_section_percentile

DAILY_KEYS = {
    "return_5d", "return_20d", "return_60d", "ma_gap_20_60", "distance_to_prev_20d_high",
    "breakout_20d", "realized_volatility_20d", "relative_volume_20d", "price_to_52w_high",
    "momentum_12m_skip1m", "volatility_percentile_1y", "amihud_illiquidity_20d",
    "amihud_percentile_1y", "market_excess_return_5d", "industry_excess_return_5d",
    "return_5d_cross_section_percentile", "momentum_cross_section_percentile",
}  # fmt: skip
INTRADAY_KEYS = {
    "return_5", "return_20", "return_60", "ma_gap_20_60", "distance_to_prev_20_high",
    "breakout_20", "realized_volatility_20", "relative_volume_20",
}  # fmt: skip


def bars(close, high=None, volume=None) -> Bars:
    close = np.asarray(close, dtype=np.float64)
    return Bars(
        high=np.asarray(close if high is None else high, dtype=np.float64),
        low=close.copy(),
        close=close,
        volume=np.asarray(
            np.full(close.size, 1000.0) if volume is None else volume, dtype=np.float64
        ),
    )


def _keys(evidence):
    assert not set(evidence.values) & set(evidence.unavailable)
    return set(evidence.values) | set(evidence.unavailable)


def test_daily_returns_every_daily_key():
    assert _keys(compute_evidence("1d", bars(np.arange(1, 301)))) == DAILY_KEYS


@pytest.mark.parametrize("timeframe", ["1m", "15m", "1h"])
def test_intraday_returns_only_the_scale_free_subset(timeframe):
    assert _keys(compute_evidence(timeframe, bars(np.arange(1, 301)))) == INTRADAY_KEYS


def test_returns_over_several_horizons():
    evidence = compute_evidence("1d", bars(np.arange(1, 301)))

    assert evidence.values["return_5d"] == pytest.approx(300 / 295 - 1)
    assert evidence.values["return_20d"] == pytest.approx(300 / 280 - 1)
    assert evidence.values["return_60d"] == pytest.approx(300 / 240 - 1)


def test_ma_gap():
    close = np.arange(1, 301, dtype=np.float64)
    evidence = compute_evidence("1d", bars(close))

    assert evidence.values["ma_gap_20_60"] == pytest.approx(
        close[-20:].mean() / close[-60:].mean() - 1
    )


def test_breakout_and_52_week_high_exclude_the_current_bar():
    close = np.full(300, 100.0)
    close[-1] = 110
    evidence = compute_evidence("1d", bars(close))

    assert evidence.values["breakout_20d"] is True
    assert evidence.values["distance_to_prev_20d_high"] == pytest.approx(0.1)
    assert evidence.values["price_to_52w_high"] == pytest.approx(1.1)


def test_no_breakout_below_the_previous_high():
    close = np.full(300, 100.0)
    close[-1] = 95
    evidence = compute_evidence("1d", bars(close))

    assert evidence.values["breakout_20d"] is False
    assert evidence.values["distance_to_prev_20d_high"] == pytest.approx(-0.05)


def test_momentum_skips_the_last_month():
    close = np.arange(1, 301, dtype=np.float64)
    evidence = compute_evidence("1d", bars(close))

    assert evidence.values["momentum_12m_skip1m"] == pytest.approx(close[-22] / close[-253] - 1)


def test_realized_volatility_is_zero_for_a_constant_growth_rate():
    close = 100 * 1.01 ** np.arange(300)
    evidence = compute_evidence("1d", bars(close))

    assert evidence.values["realized_volatility_20d"] == pytest.approx(0, abs=1e-12)


def test_relative_volume():
    volume = np.full(300, 1000.0)
    volume[-1] = 2900
    evidence = compute_evidence("1d", bars(np.arange(1, 301), volume=volume))

    assert evidence.values["relative_volume_20d"] == pytest.approx(2900 / (19 * 1000 + 2900) * 20)


def test_short_history_is_unavailable_with_the_bars_it_needs():
    evidence = compute_evidence("1d", bars(np.arange(1, 31)))

    assert evidence.unavailable["return_60d"] == "needs 61 bars, have 30"
    assert evidence.unavailable["momentum_12m_skip1m"] == "needs 253 bars, have 30"
    assert evidence.unavailable["volatility_percentile_1y"] == "needs 273 bars, have 30"
    assert "breakout_20d" in evidence.values
    assert evidence.values["return_20d"] == pytest.approx(30 / 10 - 1)


def test_breakout_needs_21_bars():
    evidence = compute_evidence("1d", bars(np.arange(1, 21)))

    assert evidence.unavailable["breakout_20d"] == "needs 21 bars, have 20"
    assert evidence.unavailable["distance_to_prev_20d_high"] == "needs 21 bars, have 20"


def test_zero_volume_is_unavailable_never_infinite():
    volume = np.full(300, 1000.0)
    volume[-20:] = 0
    evidence = compute_evidence("1d", bars(np.arange(1, 301), volume=volume))

    assert evidence.unavailable["relative_volume_20d"] == "zero volume"
    assert evidence.unavailable["amihud_illiquidity_20d"] == "zero volume"
    assert evidence.unavailable["amihud_percentile_1y"] == "zero volume"
    assert all(np.isfinite(v) for v in evidence.values.values())


def test_percentiles_stay_in_range_and_benchmarks_are_not_collected():
    rng = np.random.default_rng(0)
    close = 100 * np.cumprod(1 + rng.normal(0, 0.01, 300))
    universe = {"000001": close, "000002": close * np.linspace(1, 2, 300)}
    evidence = compute_evidence("1d", bars(close), universe)

    assert 0 <= evidence.values["volatility_percentile_1y"] <= 100
    assert 0 <= evidence.values["amihud_percentile_1y"] <= 100
    assert 0 <= evidence.values["return_5d_cross_section_percentile"] <= 100
    assert 0 <= evidence.values["momentum_cross_section_percentile"] <= 100
    assert evidence.unavailable["market_excess_return_5d"] == "benchmark data not collected"
    assert evidence.unavailable["industry_excess_return_5d"] == "benchmark data not collected"


def test_cross_section_without_universe_is_unavailable():
    evidence = compute_evidence("1d", bars(np.arange(1, 301)), None)

    assert evidence.unavailable["return_5d_cross_section_percentile"] == "no universe data"


def test_cross_section_percentile_counts_values_at_or_below():
    assert cross_section_percentile(2.0, [1.0, 2.0, 3.0, 4.0]) == 50.0
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest services/portfolio-builder/tests/test_evidence.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement** — `services/portfolio-builder/src/portfolio_builder/evidence.py`:

```python
from collections.abc import Mapping
from typing import NamedTuple

import numpy as np
import numpy.typing as npt
import talib

Array = npt.NDArray[np.float64]
Value = float | bool

TIMEFRAMES = ("1m", "15m", "1h", "1d")
YEAR = 252
MONTH = 21


class Bars(NamedTuple):
    """Oldest first, one entry per bar."""

    high: Array
    low: Array
    close: Array
    volume: Array


class Evidence(NamedTuple):
    values: dict[str, Value]
    unavailable: dict[str, str]


def _newest(series: Array) -> float | None:
    if series.size == 0 or not np.isfinite(series[-1]):
        return None
    return float(series[-1])


class _Collector:
    def __init__(self, bars: int) -> None:
        self.bars = bars
        self.values: dict[str, Value] = {}
        self.unavailable: dict[str, str] = {}

    def has(self, name: str, needed: int) -> bool:
        if self.bars >= needed:
            return True
        self.unavailable[name] = f"needs {needed} bars, have {self.bars}"
        return False

    def put(self, name: str, value: float | None, reason: str = "not computable") -> None:
        if value is None or not np.isfinite(value):
            self.unavailable[name] = reason
        else:
            self.values[name] = float(value)


def return_n(close: Array, n: int) -> float | None:
    return _newest(talib.ROCP(close, timeperiod=n)) if close.size > n else None


def momentum_12m_skip1m(close: Array) -> float | None:
    if close.size < YEAR + 1:
        return None
    return float(close[-1 - MONTH] / close[-1 - YEAR] - 1)


def cross_section_percentile(value: float, universe: list[float]) -> float:
    ranked = np.asarray(universe, dtype=np.float64)
    return float((ranked <= value).mean() * 100)


def _amihud(bars: Bars) -> Array:
    # bars stores OHLCV only, so traded value is approximated as close * volume.
    with np.errstate(divide="ignore", invalid="ignore"):
        illiquidity = np.abs(talib.ROCP(bars.close, timeperiod=1)) / (bars.close * bars.volume)
    return talib.SMA(illiquidity, timeperiod=20)


def compute_evidence(
    timeframe: str,
    bars: Bars,
    universe_closes: Mapping[str, Array] | None = None,
) -> Evidence:
    c = _Collector(bars.close.size)
    close, volume = bars.close, bars.volume
    daily = timeframe == "1d"
    unit = "d" if daily else ""

    for n in (5, 20, 60):
        name = f"return_{n}{unit}"
        if c.has(name, n + 1):
            c.put(name, return_n(close, n))

    if c.has("ma_gap_20_60", 60):
        sma20 = _newest(talib.SMA(close, timeperiod=20))
        sma60 = _newest(talib.SMA(close, timeperiod=60))
        c.put("ma_gap_20_60", sma20 / sma60 - 1 if sma20 and sma60 else None)

    distance = f"distance_to_prev_20{unit}_high"
    breakout = f"breakout_20{unit}"
    enough = [c.has(distance, 21), c.has(breakout, 21)]
    if all(enough):
        previous_high = _newest(talib.MAX(bars.high[:-1], timeperiod=20))
        c.put(distance, close[-1] / previous_high - 1 if previous_high else None)
        if previous_high:
            c.values[breakout] = bool(close[-1] > previous_high)
        else:
            c.unavailable[breakout] = "not computable"

    volatility = talib.STDDEV(talib.ROCP(close, timeperiod=1), timeperiod=20)
    volatility_name = f"realized_volatility_20{unit}"
    if c.has(volatility_name, 21):
        c.put(volatility_name, _newest(volatility))

    relative_volume = f"relative_volume_20{unit}"
    if c.has(relative_volume, 20):
        average = _newest(talib.SMA(volume, timeperiod=20))
        c.put(relative_volume, volume[-1] / average if average else None, "zero volume")

    if not daily:
        return Evidence(c.values, c.unavailable)

    if c.has("price_to_52w_high", YEAR + 1):
        previous_close_high = _newest(talib.MAX(close[:-1], timeperiod=YEAR))
        c.put(
            "price_to_52w_high", close[-1] / previous_close_high if previous_close_high else None
        )

    if c.has("momentum_12m_skip1m", YEAR + 1):
        c.put("momentum_12m_skip1m", momentum_12m_skip1m(close))

    # A 20-bar volatility needs 21 closes, then it is ranked against the previous 252 values.
    if c.has("volatility_percentile_1y", 21 + YEAR):
        c.put("volatility_percentile_1y", _newest(talib.PERCENTRANK(volatility, timeperiod=YEAR)))

    amihud = _amihud(bars)
    if c.has("amihud_illiquidity_20d", 21):
        c.put("amihud_illiquidity_20d", _newest(amihud), "zero volume")
    if c.has("amihud_percentile_1y", 21 + YEAR):
        if np.isfinite(amihud[-(YEAR + 1) :]).all():
            c.put("amihud_percentile_1y", _newest(talib.PERCENTRANK(amihud, timeperiod=YEAR)))
        else:
            c.unavailable["amihud_percentile_1y"] = "zero volume"

    for name in ("market_excess_return_5d", "industry_excess_return_5d"):
        c.unavailable[name] = "benchmark data not collected"

    _cross_section(c, universe_closes)
    return Evidence(c.values, c.unavailable)


def _cross_section(c: _Collector, universe: Mapping[str, Array] | None) -> None:
    measures = {
        "return_5d_cross_section_percentile": ("return_5d", lambda x: return_n(x, 5)),
        "momentum_cross_section_percentile": ("momentum_12m_skip1m", momentum_12m_skip1m),
    }
    for name, (own, measure) in measures.items():
        if own not in c.values:
            c.unavailable[name] = f"{own} unavailable"
            continue
        peers = [v for x in (universe or {}).values() if (v := measure(x)) is not None]
        if not peers:
            c.unavailable[name] = "no universe data"
            continue
        c.put(name, cross_section_percentile(float(c.values[own]), peers))
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest services/portfolio-builder/tests/test_evidence.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add services/portfolio-builder
git commit -m "feat: TA-Lib technical evidence from OHLCV bars

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: QuestDB reader and `analyze_technicals`

**Files:**
- Create: `services/portfolio-builder/src/portfolio_builder/market.py`
- Create: `services/portfolio-builder/src/portfolio_builder/tools/technicals.py`
- Create: `services/portfolio-builder/tests/test_technicals.py`

**Interfaces:**
- Consumes: `Bars`, `compute_evidence`, `TIMEFRAMES` (Task 8); `ktb_core.normalize.normalize`; `UnknownCompany`, `NoMarketData`; `to_json`; fixture `engine`.
- Produces:
  - `portfolio_builder.market.QuestDBMarket(conf: str)` with `bars(symbol: str, timeframe: str) -> tuple[Bars, datetime | None]` (empty arrays and `None` when there are no rows) and `universe_closes() -> dict[str, Array]` (daily regular-session closes of the latest KOSPI 200 snapshot, oldest first).
  - `portfolio_builder.tools.technicals.technicals_tool(engine: sa.Engine, market) -> BaseTool` named `analyze_technicals` with arguments `name: str`, `timeframe: Literal["1m","15m","1h","1d"]` (required). `market` is any object with the two `QuestDBMarket` methods.

- [ ] **Step 1: Write the failing tests** — `services/portfolio-builder/tests/test_technicals.py`:

```python
import json
from datetime import UTC, datetime

import numpy as np
import pytest
from portfolio_builder import market as market_module
from portfolio_builder.errors import NoMarketData, UnknownCompany
from portfolio_builder.evidence import Bars
from portfolio_builder.market import QuestDBMarket
from portfolio_builder.tools.technicals import technicals_tool

AS_OF = datetime(2026, 9, 28, 6, 30, tzinfo=UTC)


def _bars(n: int) -> Bars:
    close = np.arange(1, n + 1, dtype=np.float64)
    return Bars(close.copy(), close.copy(), close, np.full(n, 1000.0))


class FakeMarket:
    def __init__(self, n: int = 300):
        self.n = n
        self.calls: list[tuple[str, str]] = []
        self.universe_calls = 0

    def bars(self, symbol, timeframe):
        self.calls.append((symbol, timeframe))
        if self.n == 0:
            return _bars(0), None
        return _bars(self.n), AS_OF

    def universe_closes(self):
        self.universe_calls += 1
        return {"005930": _bars(300).close, "000660": _bars(300).close * 2}


def test_daily_evidence_for_a_company_found_by_name(engine):
    market = FakeMarket()
    tool = technicals_tool(engine, market)

    result = json.loads(tool.invoke({"name": "㈜삼성전자", "timeframe": "1d"}))

    assert market.calls == [("005930", "1d")]
    assert result["company_id"] == "00126380"
    assert result["stock_code"] == "005930"
    assert result["timeframe"] == "1d"
    assert result["bars"] == 300
    assert result["as_of"] == AS_OF.isoformat()
    assert "return_5d" in result["evidence"]
    assert result["unavailable"]["market_excess_return_5d"] == "benchmark data not collected"


def test_intraday_evidence_skips_the_universe(engine):
    market = FakeMarket()
    tool = technicals_tool(engine, market)

    result = json.loads(tool.invoke({"name": "00164779", "timeframe": "15m"}))

    assert market.calls == [("000660", "15m")]
    assert market.universe_calls == 0
    assert "return_5" in result["evidence"]
    assert "momentum_12m_skip1m" not in result["evidence"]


def test_the_universe_is_read_once_per_run(engine):
    market = FakeMarket()
    tool = technicals_tool(engine, market)

    tool.invoke({"name": "삼성전자", "timeframe": "1d"})
    tool.invoke({"name": "SK하이닉스", "timeframe": "1d"})

    assert market.universe_calls == 1


def test_timeframe_is_required(engine):
    with pytest.raises(Exception, match="timeframe"):
        technicals_tool(engine, FakeMarket()).invoke({"name": "삼성전자"})


def test_unknown_company_lists_candidates(engine):
    with pytest.raises(UnknownCompany, match="삼성전자"):
        technicals_tool(engine, FakeMarket()).invoke({"name": "삼성", "timeframe": "1d"})


def test_no_bars_is_a_recoverable_error(engine):
    with pytest.raises(NoMarketData, match="373220"):
        technicals_tool(engine, FakeMarket(n=0)).invoke({"name": "LG에너지솔루션", "timeframe": "1d"})


class FakeResult:
    def __init__(self, records):
        self.records = records

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def to_pandas(self):
        records = self.records

        class Frame:
            def to_dict(self, orient):
                assert orient == "records"
                return records

        return Frame()


class FakeDB:
    def __init__(self, answers):
        self.answers = answers
        self.queries: list[tuple[str, list]] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def query(self, sql, binds=None):
        self.queries.append((sql, binds))
        return FakeResult(self.answers.pop(0))


@pytest.mark.parametrize(
    ("timeframe", "view", "session"),
    [("1m", "bars_1m", True), ("15m", "bars_15m", False), ("1h", "bars_1h", False),
     ("1d", "bars_1d", True)],
)  # fmt: skip
def test_bars_reads_the_timeframe_view_newest_first_and_reverses(
    monkeypatch, timeframe, view, session
):
    rows = [
        {"ts": AS_OF, "high": 3.0, "low": 1.0, "close": 2.0, "volume": 30},
        {"ts": datetime(2026, 9, 27, tzinfo=UTC), "high": 2.0, "low": 1.0, "close": 1.5,
         "volume": 20},
    ]  # fmt: skip
    db = FakeDB([rows])
    monkeypatch.setattr(market_module.questdb, "connect", lambda conf: db)

    bars, as_of = QuestDBMarket("http::addr=x:9000;").bars("005930", timeframe)

    sql, binds = db.queries[0]
    assert f"FROM {view} " in sql
    assert ("session = 'regular'" in sql) is session
    assert binds == ["005930", 300]
    assert bars.close.tolist() == [1.5, 2.0]
    assert bars.volume.dtype == np.float64
    assert as_of == AS_OF


def test_bars_rejects_an_unknown_timeframe():
    with pytest.raises(KeyError):
        QuestDBMarket("x").bars("005930", "5m")


def test_universe_closes_groups_the_latest_snapshot_by_symbol(monkeypatch):
    members = [{"symbol": "005930"}, {"symbol": "000660"}]
    closes = [
        {"symbol": "000660", "close": 10.0},
        {"symbol": "005930", "close": 1.0},
        {"symbol": "005930", "close": 2.0},
        {"symbol": "999999", "close": 5.0},
    ]
    db = FakeDB([members, closes])
    monkeypatch.setattr(market_module.questdb, "connect", lambda conf: db)

    universe = QuestDBMarket("x").universe_closes()

    assert {k: v.tolist() for k, v in universe.items()} == {
        "005930": [1.0, 2.0],
        "000660": [10.0],
    }
```

- [ ] **Step 2: Run to see them fail**

Run: `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test uv run pytest services/portfolio-builder/tests/test_technicals.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`services/portfolio-builder/src/portfolio_builder/market.py`:
```python
from collections import defaultdict
from datetime import datetime
from typing import Any

import numpy as np
import questdb

from portfolio_builder.evidence import Array, Bars

VIEWS = {"1m": "bars_1m", "15m": "bars_15m", "1h": "bars_1h", "1d": "bars_1d"}
# bars_15m and bars_1h are materialized views without a session column (see issue #47).
SESSION_FILTERED = {"1m", "1d"}
LIMIT = 300
KOSPI200 = "201"
# 400 calendar days hold more than the 253 trading days that 12-month momentum needs.
UNIVERSE_DAYS = 400


class QuestDBMarket:
    def __init__(self, conf: str) -> None:
        self._conf = conf

    # A new connection per call: tools run on ToolNode worker threads and a QuestDB query result
    # is bound to the thread that created it.
    def _records(self, sql: str, binds: list[Any] | None = None) -> list[dict[str, Any]]:
        with questdb.connect(self._conf) as db, db.query(sql, binds) as result:
            return result.to_pandas().to_dict("records")

    def bars(self, symbol: str, timeframe: str) -> tuple[Bars, datetime | None]:
        view = VIEWS[timeframe]
        session = " AND session = 'regular'" if timeframe in SESSION_FILTERED else ""
        records = self._records(
            f"SELECT ts, high, low, close, volume FROM {view} "
            f"WHERE symbol = $1{session} ORDER BY ts DESC LIMIT $2",
            [symbol, LIMIT],
        )
        records.reverse()

        def column(name: str) -> Array:
            return np.array([r[name] for r in records], dtype=np.float64)

        bars = Bars(column("high"), column("low"), column("close"), column("volume"))
        return bars, (records[-1]["ts"] if records else None)

    def universe_closes(self) -> dict[str, Array]:
        members = {
            r["symbol"]
            for r in self._records(
                "SELECT symbol FROM universe_members WHERE index_code = $1"
                " AND ts = (SELECT max(ts) FROM universe_members WHERE index_code = $1)",
                [KOSPI200],
            )
        }
        closes: dict[str, list[float]] = defaultdict(list)
        for r in self._records(
            "SELECT symbol, ts, close FROM bars_1d WHERE session = 'regular'"
            f" AND ts > dateadd('d', -{UNIVERSE_DAYS}, now()) ORDER BY symbol, ts"
        ):
            if r["symbol"] in members:
                closes[r["symbol"]].append(r["close"])
        return {symbol: np.array(values, dtype=np.float64) for symbol, values in closes.items()}
```

`services/portfolio-builder/src/portfolio_builder/tools/technicals.py`:
```python
from functools import cache
from typing import Annotated, Any, Literal

import sqlalchemy as sa
from ktb_core.normalize import normalize
from langchain.tools import tool
from langchain_core.tools import BaseTool
from pydantic import Field

from portfolio_builder.errors import NoMarketData, UnknownCompany
from portfolio_builder.evidence import compute_evidence
from portfolio_builder.tools import to_json

TIMEFRAME_HELP = (
    "bar size: 1m or 15m for intraday momentum and volume spikes, 1h for the last few sessions,"
    " 1d for multi-week trend, 52-week position, 12-month momentum, volatility and liquidity"
    " regimes and KOSPI 200 cross-section ranks (daily only)"
)


def _resolve_company(engine: sa.Engine, name: str) -> dict[str, Any]:
    with engine.connect() as conn:
        company = (
            conn.execute(
                sa.text(
                    "SELECT corp_code, corp_name, stock_code FROM ("
                    "  SELECT c.corp_code, c.corp_name, c.stock_code, 0 AS priority"
                    "  FROM companies c WHERE c.corp_code = :code"
                    "  UNION ALL"
                    "  SELECT c.corp_code, c.corp_name, c.stock_code, 1"
                    "  FROM company_aliases a JOIN companies c ON c.corp_code = a.corp_code"
                    "  WHERE a.alias = :alias"
                    ") AS matches ORDER BY priority LIMIT 1"
                ),
                {"code": name.strip(), "alias": normalize(name)},
            )
            .mappings()
            .first()
        )
        if company is not None:
            return dict(company)
        escaped = name.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        candidates = list(
            conn.execute(
                sa.text(
                    "SELECT corp_name FROM companies WHERE corp_name ILIKE :pattern"
                    " ORDER BY corp_name LIMIT 5"
                ),
                {"pattern": f"%{escaped}%"},
            ).scalars()
        )
    raise UnknownCompany(
        f'no company matches "{name}". Candidates: {", ".join(candidates) or "none"}'
    )


def technicals_tool(engine: sa.Engine, market: Any) -> BaseTool:
    universe = cache(market.universe_closes)

    @tool(
        "analyze_technicals",
        description=(
            "Technical evidence computed from one listed company's OHLCV bars at the timeframe"
            " you choose: returns, trend, breakout, 52-week position, volatility, volume and"
            " liquidity, each with its value; anything that cannot be computed is listed under"
            " unavailable with the reason. Look the company up by name or company_id."
        ),
    )
    def analyze_technicals(
        name: Annotated[str, Field(description="company name or company_id")],
        timeframe: Annotated[
            Literal["1m", "15m", "1h", "1d"], Field(description=TIMEFRAME_HELP)
        ],
    ) -> str:
        company = _resolve_company(engine, name)
        bars, as_of = market.bars(company["stock_code"], timeframe)
        if bars.close.size == 0:
            raise NoMarketData(
                f"no {timeframe} bars for {company['corp_name']} (stock_code"
                f" {company['stock_code']}); it is outside the KOSPI 200 archive or not"
                " collected yet"
            )
        evidence = compute_evidence(timeframe, bars, universe() if timeframe == "1d" else None)
        return to_json(
            {
                "company": company["corp_name"],
                "company_id": company["corp_code"],
                "stock_code": company["stock_code"],
                "timeframe": timeframe,
                "as_of": as_of,
                "bars": int(bars.close.size),
                "evidence": evidence.values,
                "unavailable": evidence.unavailable,
            }
        )

    return analyze_technicals
```

- [ ] **Step 4: Run the tests**

Run: `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test uv run pytest services/portfolio-builder/tests/test_technicals.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add services/portfolio-builder
git commit -m "feat: analyze_technicals reads QuestDB bars at the agent's timeframe

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: `submit_portfolio` tool

**Files:**
- Create: `services/portfolio-builder/src/portfolio_builder/tools/submit.py`
- Create: `services/portfolio-builder/tests/test_submit.py`

**Interfaces:**
- Consumes: `Holding`, `Exit`, `Submission`, `validate_portfolio`, `normalize_weights`, `save_portfolio` (Task 5); `PortfolioRejected`, `ToolError`; `Log`; fixture `engine`.
- Produces: `submit_tool(engine: sa.Engine, previous: frozenset[str], model: str, log: Log) -> BaseTool` named `submit_portfolio`. On success it returns `Command(update={"portfolio_id": int, "messages": [ToolMessage("Saved portfolio {id}.", tool_call_id=...)]})`. On validation or save failure it logs `validation_failed` (WARNING, field `errors`) and raises `PortfolioRejected`. After one success, every later call raises `ToolError("portfolio already saved as {id}; the run is over")`. Calls are serialised with a `threading.Lock`.

- [ ] **Step 1: Write the failing tests** — `services/portfolio-builder/tests/test_submit.py`:

```python
import logging
import threading

import pytest
import sqlalchemy as sa
from portfolio_builder.errors import PortfolioRejected, ToolError
from portfolio_builder.tools.submit import submit_tool

SAMSUNG = "00126380"
VALID = {
    "holdings": [
        {"company_id": SAMSUNG, "weight": 3, "reason": "HBM 공급 확대", "cited_cluster_ids": [1]}
    ],
    "exits": [],
    "cash_weight": 1,
    "commentary": "HBM 수요에 집중",
}


def _call(args, call_id="c1"):
    return {"name": "submit_portfolio", "args": args, "id": call_id, "type": "tool_call"}


class Recorder:
    def __init__(self):
        self.events = []

    def __call__(self, event, level=logging.INFO, **fields):
        self.events.append((event, level, fields))


def _rows(engine):
    with engine.connect() as conn:
        return conn.execute(sa.text("SELECT id, cash_weight FROM portfolios")).all()


def test_saves_a_normalised_portfolio_and_returns_its_id_in_state(engine):
    tool = submit_tool(engine, frozenset(), "openrouter/m", Recorder())

    command = tool.invoke(_call(VALID))

    [(portfolio_id, cash_weight)] = _rows(engine)
    assert command.update["portfolio_id"] == portfolio_id
    assert command.update["messages"][0].content == f"Saved portfolio {portfolio_id}."
    assert command.update["messages"][0].tool_call_id == "c1"
    assert cash_weight == pytest.approx(0.25)


def test_rejection_lists_every_error_and_logs_it(engine):
    log = Recorder()
    tool = submit_tool(engine, frozenset(), "m", log)
    bad = {**VALID, "holdings": [{**VALID["holdings"][0], "reason": ""}], "commentary": ""}

    with pytest.raises(PortfolioRejected) as rejected:
        tool.invoke(_call(bad))

    assert rejected.value.errors == [
        f"holdings: {SAMSUNG} is entering the portfolio and needs a reason",
        "commentary must not be empty",
    ]
    assert log.events == [("validation_failed", logging.WARNING, {"errors": rejected.value.errors})]
    assert _rows(engine) == []


def test_a_save_failure_is_returned_as_a_rejection(engine):
    tool = submit_tool(engine, frozenset(), "m", Recorder())
    bad = {**VALID, "holdings": [{**VALID["holdings"][0], "cited_cluster_ids": [404]}]}

    with pytest.raises(PortfolioRejected, match="cited_cluster_ids not found: 404"):
        tool.invoke(_call(bad))


def test_a_second_submit_is_refused(engine):
    tool = submit_tool(engine, frozenset(), "m", Recorder())
    tool.invoke(_call(VALID))

    with pytest.raises(ToolError, match="already saved"):
        tool.invoke(_call(VALID, "c2"))
    assert len(_rows(engine)) == 1


def test_concurrent_submits_write_one_portfolio(engine):
    tool = submit_tool(engine, frozenset(), "m", Recorder())
    outcomes = []

    def submit(call_id):
        try:
            tool.invoke(_call(VALID, call_id))
            outcomes.append("saved")
        except ToolError:
            outcomes.append("refused")

    threads = [threading.Thread(target=submit, args=(f"c{i}",)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ["refused", "refused", "refused", "saved"]
    assert len(_rows(engine)) == 1
```

- [ ] **Step 2: Run to see them fail**

Run: `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test uv run pytest services/portfolio-builder/tests/test_submit.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement** — `services/portfolio-builder/src/portfolio_builder/tools/submit.py`:

```python
import logging
import threading
from typing import Annotated

import sqlalchemy as sa
from langchain.tools import tool
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, InjectedToolCallId
from langgraph.types import Command
from pydantic import Field

from portfolio_builder.errors import PortfolioRejected, ToolError
from portfolio_builder.log import Log
from portfolio_builder.portfolio import (
    Exit,
    Holding,
    Submission,
    normalize_weights,
    save_portfolio,
    validate_portfolio,
)


def submit_tool(engine: sa.Engine, previous: frozenset[str], model: str, log: Log) -> BaseTool:
    # ToolNode runs one message's tool calls on parallel threads; the lock keeps a run to one row.
    lock = threading.Lock()
    saved: list[int] = []

    @tool(
        "submit_portfolio",
        description=(
            "Submit the new model portfolio. Weights are relative; the system scales holdings"
            " and cash_weight to sum to 1. On errors, fix every one and submit again."
        ),
    )
    def submit_portfolio(
        holdings: list[Holding],
        exits: Annotated[list[Exit], Field(description="every previous holding that is dropped")],
        cash_weight: Annotated[float, Field(description="relative, non-negative")],
        commentary: Annotated[
            str, Field(description="overall assessment of the portfolio and this run's decisions")
        ],
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> Command:
        submission = Submission(
            holdings=holdings, exits=exits, cash_weight=cash_weight, commentary=commentary
        )
        with lock:
            if saved:
                raise ToolError(f"portfolio already saved as {saved[0]}; the run is over")
            errors = validate_portfolio(submission, previous)
            if not errors:
                try:
                    saved.append(save_portfolio(engine, normalize_weights(submission), model))
                except PortfolioRejected as rejected:
                    errors = rejected.errors
            if errors:
                log("validation_failed", logging.WARNING, errors=errors)
                raise PortfolioRejected(errors)
        portfolio_id = saved[0]
        return Command(
            update={
                "portfolio_id": portfolio_id,
                "messages": [
                    ToolMessage(f"Saved portfolio {portfolio_id}.", tool_call_id=tool_call_id)
                ],
            }
        )

    return submit_portfolio
```

- [ ] **Step 4: Run the tests**

Run: `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test uv run pytest services/portfolio-builder/tests/test_submit.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add services/portfolio-builder
git commit -m "feat: submit_portfolio tool writing the saved id into agent state

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Briefing and system prompt

**Files:**
- Create: `services/portfolio-builder/src/portfolio_builder/briefing.py`
- Create: `services/portfolio-builder/tests/test_briefing.py`

**Interfaces:**
- Consumes: fixture `engine`; `save_portfolio`, `Submission`, `Holding` (tests only).
- Produces:
  - `SYSTEM_PROMPT: str`
  - `Briefing` dataclass: `previous_portfolio_id: int | None`, `previous_company_ids: frozenset[str]`, `previous_holdings: int`, `previous_exits: int`, `cluster_ids: list[int]`, `company_count: int`, `theme_count: int`, `text: str`.
  - `load_briefing(engine: sa.Engine, news_window_days: int) -> Briefing`

- [ ] **Step 1: Write the failing tests** — `services/portfolio-builder/tests/test_briefing.py`:

```python
from portfolio_builder.briefing import SYSTEM_PROMPT, load_briefing
from portfolio_builder.portfolio import Holding, Submission, save_portfolio

SAMSUNG, HYNIX = "00126380", "00164779"


def test_the_system_prompt_states_the_goal_grounding_and_tools():
    assert "model portfolio" in SYSTEM_PROMPT
    assert "pre-trained knowledge" in SYSTEM_PROMPT
    assert "submit_portfolio" in SYSTEM_PROMPT
    assert "timeframe" in SYSTEM_PROMPT


def test_first_run_briefs_only_clusters_inside_the_window(engine):
    briefing = load_briefing(engine, 7)

    assert briefing.previous_portfolio_id is None
    assert briefing.previous_company_ids == frozenset()
    assert briefing.cluster_ids == [1]
    assert briefing.company_count == 1
    assert briefing.theme_count == 1
    assert "first portfolio" in briefing.text
    assert "[cluster 1] 삼성전자 HBM 공급 확대" in briefing.text
    assert f"삼성전자 (company_id {SAMSUNG}, stock_code 005930)" in briefing.text
    assert "HBM (main)" in briefing.text
    assert "반도체 수출 둔화" not in briefing.text


def _submission(company_id, reason, commentary, clusters):
    return Submission(
        holdings=[
            Holding(company_id=company_id, weight=1, reason=reason, cited_cluster_ids=clusters)
        ],
        exits=[],
        cash_weight=0,
        commentary=commentary,
    )


def test_the_most_recently_created_portfolio_is_the_previous_one(engine):
    save_portfolio(engine, _submission(HYNIX, "old", "older", []), "m")
    latest = save_portfolio(engine, _submission(SAMSUNG, "new", "newer", [1]), "m")

    briefing = load_briefing(engine, 7)

    assert briefing.previous_portfolio_id == latest
    assert briefing.previous_company_ids == frozenset({SAMSUNG})
    assert briefing.previous_holdings == 1
    assert "newer" in briefing.text
    assert "older" not in briefing.text
```

- [ ] **Step 2: Run to see them fail**

Run: `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test uv run pytest services/portfolio-builder/tests/test_briefing.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement** — `services/portfolio-builder/src/portfolio_builder/briefing.py`:

```python
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa

SYSTEM_PROMPT = """You are the portfolio manager of one model portfolio of KOSPI stocks that every user of this service follows.

Goal: each run, review the previous portfolio against what has happened in the news since, and decide what to hold, at what relative weight, what to drop and how much to keep in cash. Then call submit_portfolio with the new portfolio and its reasons.

Grounding: the portfolio must carry reasons, and every stored reason must originate in the briefing or in a tool result from this run: a news cluster (cite its cluster_id), a graph relation, or technical evidence. You may use your pre-trained knowledge while thinking (to interpret events, relate industries, decide what to look up), but a fact you know only from memory cannot be the basis of a stored reason; find it in the data with a tool first.

Rules:
- Identify companies by company_id as shown in the briefing and tool results.
- Weights are relative and non-negative; the system scales holdings and cash_weight so they sum to 1.
- Every company not in the previous portfolio needs a reason.
- Every previous holding you drop needs an entry in exits with a reason.
- Cite the cluster_ids each decision relies on in cited_cluster_ids.
- Write a commentary covering the portfolio as a whole and this run's decisions.
- If submit_portfolio returns errors, fix every one and call it again.

Tools: get_news_cluster and search_news_cluster read news clusters; search_graph and find_graph_paths explore the knowledge graph of entities and relations; analyze_technicals returns a company's technical evidence (returns, trend, breakout, volatility, volume, liquidity) computed from its price bars at the timeframe you choose (1m, 15m, 1h or 1d)."""  # noqa: E501


@dataclass(frozen=True)
class Briefing:
    previous_portfolio_id: int | None
    previous_company_ids: frozenset[str]
    previous_holdings: int
    previous_exits: int
    cluster_ids: list[int]
    company_count: int
    theme_count: int
    text: str


def _label(company: Any) -> str:
    return (
        f"{company['corp_name']} (company_id {company['corp_code']},"
        f" stock_code {company['stock_code']})"
    )


def load_briefing(engine: sa.Engine, news_window_days: int) -> Briefing:
    with engine.connect() as conn:
        previous = (
            conn.execute(
                sa.text(
                    "SELECT id, created_at, cash_weight, commentary FROM portfolios"
                    " ORDER BY created_at DESC, id DESC LIMIT 1"
                )
            )
            .mappings()
            .first()
        )
        holdings = []
        exits = []
        if previous is not None:
            holdings = list(
                conn.execute(
                    sa.text(
                        "SELECT c.corp_code, c.corp_name, c.stock_code, h.weight, h.reason"
                        " FROM portfolio_holdings h JOIN companies c"
                        " ON c.corp_code = h.company_id"
                        " WHERE h.portfolio_id = :id ORDER BY h.weight DESC"
                    ),
                    {"id": previous["id"]},
                ).mappings()
            )
            exits = list(
                conn.execute(
                    sa.text(
                        "SELECT c.corp_code, c.corp_name, c.stock_code, e.reason"
                        " FROM portfolio_exits e JOIN companies c ON c.corp_code = e.company_id"
                        " WHERE e.portfolio_id = :id"
                    ),
                    {"id": previous["id"]},
                ).mappings()
            )
        clusters = list(
            conn.execute(
                sa.text(
                    "SELECT s.cluster_id, s.title, s.summary, c.updated_at"
                    " FROM cluster_summaries s JOIN clusters c ON c.id = s.cluster_id"
                    " WHERE c.updated_at >= now() - make_interval(days => :days)"
                    " ORDER BY c.updated_at DESC"
                ),
                {"days": news_window_days},
            ).mappings()
        )
        cluster_ids = [c["cluster_id"] for c in clusters]
        mentions = conn.execute(
            sa.text(
                "SELECT DISTINCT ce.cluster_id, co.corp_code, co.corp_name, co.stock_code"
                " FROM cluster_entities ce"
                " JOIN entities e ON e.id = ce.entity_id"
                " JOIN companies co ON co.corp_code = e.corp_code"
                " WHERE ce.cluster_id = ANY(CAST(:ids AS bigint[]))"
                " ORDER BY co.corp_code, ce.cluster_id"
            ),
            {"ids": cluster_ids},
        ).mappings()
        companies: dict[str, dict[str, Any]] = {}
        for m in mentions:
            entry = companies.setdefault(m["corp_code"], {**m, "clusters": []})
            entry["clusters"].append(m["cluster_id"])
        themes = list(
            conn.execute(
                sa.text(
                    "SELECT tc.corp_code, t.name, tc.is_main"
                    " FROM theme_companies tc JOIN themes t ON t.theme_code = tc.theme_code"
                    " WHERE tc.corp_code = ANY(CAST(:codes AS text[]))"
                    " ORDER BY tc.is_main DESC, t.name"
                ),
                {"codes": list(companies)},
            ).mappings()
        )
    themes_by_company: dict[str, list[str]] = defaultdict(list)
    for t in themes:
        themes_by_company[t["corp_code"]].append(f"{t['name']} (main)" if t["is_main"] else t["name"])

    lines = ["# Previous portfolio"]
    if previous is None:
        lines.append("None: this is the first portfolio, so every holding is an entry.")
    else:
        lines.append(
            f"Portfolio {previous['id']}, created {previous['created_at'].isoformat()},"
            f" cash_weight {previous['cash_weight']}"
        )
        for h in holdings:
            lines.append(
                f"- {_label(h)}: weight {h['weight']}."
                f" Reason: {h['reason'] or '(kept, no new reason)'}"
            )
        if exits:
            lines.append("Exited last time:")
        for e in exits:
            lines.append(f"- {_label(e)}. Reason: {e['reason']}")
        lines.append(f"Commentary: {previous['commentary']}")
    lines += ["", f"# News clusters updated in the last {news_window_days} days"]
    if not clusters:
        lines.append("None.")
    for c in clusters:
        lines += [
            f"## [cluster {c['cluster_id']}] {c['title']}",
            f"Updated {c['updated_at'].isoformat()}",
            c["summary"],
            "",
        ]
    lines.append("# Companies mentioned in these clusters")
    if not companies:
        lines.append("None.")
    for code, c in companies.items():
        theme_text = ", ".join(themes_by_company[code]) or "none"
        clusters_text = ", ".join(map(str, c["clusters"]))
        lines.append(f"- {_label(c)}: clusters {clusters_text}; themes: {theme_text}")

    return Briefing(
        previous_portfolio_id=previous["id"] if previous is not None else None,
        previous_company_ids=frozenset(h["corp_code"] for h in holdings),
        previous_holdings=len(holdings),
        previous_exits=len(exits),
        cluster_ids=cluster_ids,
        company_count=len(companies),
        theme_count=len(themes),
        text="\n".join(lines),
    )
```

- [ ] **Step 4: Run the tests**

Run: `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test uv run pytest services/portfolio-builder/tests/test_briefing.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add services/portfolio-builder
git commit -m "feat: briefing of the previous portfolio, recent clusters, companies and themes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: The agent loop

**Files:**
- Create: `services/portfolio-builder/src/portfolio_builder/agent.py`
- Create: `services/portfolio-builder/tests/test_agent.py`

**Interfaces:**
- Consumes: `ToolError` (Task 4); `submit_tool` (Task 10, tests); `Log`; fixture `engine` (tests).
- Produces:
  - `RunResult` dataclass: `outcome: Literal["saved", "max_turns", "error"]`, `portfolio_id: int | None`, `turns: int`, `usage: dict[str, float]`, `error: str | None = None`.
  - `run_agent(*, model: BaseChatModel, tools: list[BaseTool], system_prompt: str, briefing: str, max_turns: int, log: Log) -> RunResult`
  - `NUDGE: str`

- [ ] **Step 1: Write the failing tests** — `services/portfolio-builder/tests/test_agent.py`:

```python
import itertools
import logging

import sqlalchemy as sa
from langchain.tools import tool
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from portfolio_builder.agent import NUDGE, run_agent
from portfolio_builder.tools.submit import submit_tool

SAMSUNG = "00126380"
VALID = {
    "holdings": [
        {"company_id": SAMSUNG, "weight": 3, "reason": "HBM 공급 확대", "cited_cluster_ids": [1]}
    ],
    "exits": [],
    "cash_weight": 1,
    "commentary": "HBM 수요에 집중",
}
_ids = itertools.count()


class ScriptedModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def reply(text="", *calls):
    # Every message needs its own id: the message reducer replaces messages that share one.
    return AIMessage(
        text,
        id=f"ai-{next(_ids)}",
        tool_calls=[
            {"name": name, "args": args, "id": f"call-{next(_ids)}", "type": "tool_call"}
            for name, args in calls
        ],
    )


class Recorder:
    def __init__(self):
        self.events = []

    def __call__(self, event, level=logging.INFO, **fields):
        self.events.append((event, level, fields))

    def names(self):
        return [e[0] for e in self.events]


def _run(engine, replies, max_turns=10, extra_tools=()):
    log = Recorder()
    tools = [submit_tool(engine, frozenset(), "m", log), *extra_tools]
    result = run_agent(
        model=ScriptedModel(messages=iter(replies)),
        tools=tools,
        system_prompt="sys",
        briefing="brief",
        max_turns=max_turns,
        log=log,
    )
    return result, log


def _count(engine):
    with engine.connect() as conn:
        return conn.execute(sa.text("SELECT count(*) FROM portfolios")).scalar_one()


def test_an_invalid_submission_is_fed_back_and_the_corrected_one_saved(engine):
    invalid = {**VALID, "holdings": [{**VALID["holdings"][0], "reason": ""}]}
    result, log = _run(
        engine,
        [reply("", ("submit_portfolio", invalid)), reply("", ("submit_portfolio", VALID))],
    )

    assert result.outcome == "saved"
    assert result.turns == 2
    assert _count(engine) == 1
    tool_calls = [f for e, _, f in log.events if e == "tool_call"]
    assert tool_calls[0]["is_error"] is True
    assert "needs a reason" in tool_calls[0]["result"]
    assert tool_calls[1]["is_error"] is False
    for name in ("llm_request", "llm_response", "tool_call", "validation_failed"):
        assert name in log.names()


def test_a_model_that_never_submits_is_nudged_and_ends_at_the_turn_limit(engine):
    result, log = _run(engine, [reply(f"thinking {i}") for i in range(20)], max_turns=3)

    assert result.outcome == "max_turns"
    assert result.turns == 3
    assert result.portfolio_id is None
    assert _count(engine) == 0
    requests = [f for e, _, f in log.events if e == "llm_request"]
    assert requests[1]["message_count"] == 3  # briefing, reply, nudge


def test_a_submission_on_the_last_allowed_turn_is_saved(engine):
    result, _ = _run(
        engine,
        [reply("a"), reply("b"), reply("", ("submit_portfolio", VALID))],
        max_turns=3,
    )

    assert result.outcome == "saved"
    assert result.turns == 3


def test_two_submissions_in_one_message_write_one_portfolio(engine):
    result, log = _run(
        engine,
        [reply("", ("submit_portfolio", VALID), ("submit_portfolio", VALID))],
    )

    assert result.outcome == "saved"
    assert _count(engine) == 1
    results = sorted(f["result"] for e, _, f in log.events if e == "tool_call")
    assert results[0].startswith("Saved portfolio")
    assert "already saved" in results[1]


@tool("explode", description="always fails")
def explode() -> str:
    raise RuntimeError("database is down")


def test_an_unexpected_tool_exception_ends_the_run_as_error(engine):
    result, log = _run(engine, [reply("", ("explode", {}))], extra_tools=[explode])

    assert result.outcome == "error"
    assert "database is down" in result.error
    assert _count(engine) == 0
    assert any(e == "tool_call" and f["is_error"] for e, _, f in log.events)


def test_the_nudge_text():
    assert "submit_portfolio" in NUDGE
```

- [ ] **Step 2: Run to see them fail**

Run: `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test uv run pytest services/portfolio-builder/tests/test_agent.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement** — `services/portfolio-builder/src/portfolio_builder/agent.py`:

```python
import logging
import time
from dataclasses import dataclass
from typing import Any, Literal, NotRequired

from langchain.agents import AgentState, create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelCallLimitMiddleware,
    ToolErrorMiddleware,
    hook_config,
)
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool

from portfolio_builder.errors import ToolError
from portfolio_builder.log import Log

NUDGE = (
    "You stopped without a saved portfolio. Keep investigating with the tools if you need to,"
    " then call submit_portfolio."
)


class PortfolioState(AgentState):
    portfolio_id: NotRequired[int]


@dataclass
class RunResult:
    outcome: Literal["saved", "max_turns", "error"]
    portfolio_id: int | None
    turns: int
    usage: dict[str, float]
    error: str | None = None


class StopOnSave(AgentMiddleware):
    state_schema = PortfolioState

    @hook_config(can_jump_to=["end"])
    def before_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        if state.get("portfolio_id") is not None:
            return {"jump_to": "end"}
        return None


class Nudge(AgentMiddleware):
    @hook_config(can_jump_to=["model"])
    def after_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and not last.tool_calls:
            return {"messages": [HumanMessage(NUDGE)], "jump_to": "model"}
        return None


def _tool_messages(result: Any) -> list[ToolMessage]:
    if isinstance(result, ToolMessage):
        return [result]
    update = getattr(result, "update", None) or {}
    return [m for m in update.get("messages", []) if isinstance(m, ToolMessage)]


class RunLog(AgentMiddleware):
    """Logs every model and tool call, and keeps the turn and usage totals for run_end."""

    def __init__(self, log: Log) -> None:
        super().__init__()
        self.log = log
        self.turns = 0
        self.usage: dict[str, float] = {
            "input": 0,
            "output": 0,
            "cache_read": 0,
            "reasoning": 0,
            "cost": 0.0,
        }

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        self.turns += 1
        self.log(
            "llm_request",
            turn=self.turns,
            tools=[getattr(t, "name", None) for t in request.tools],
            message_count=len(request.messages),
        )
        started = time.perf_counter()
        response = handler(request)
        message = next((m for m in reversed(response.result) if isinstance(m, AIMessage)), None)
        if message is None:
            return response
        usage = message.usage_metadata or {}
        turn_usage = {
            "input": usage.get("input_tokens", 0),
            "output": usage.get("output_tokens", 0),
            "cache_read": (usage.get("input_token_details") or {}).get("cache_read") or 0,
            "reasoning": (usage.get("output_token_details") or {}).get("reasoning") or 0,
            "total": usage.get("total_tokens", 0),
        }
        cost = message.response_metadata.get("cost")
        for key in ("input", "output", "cache_read", "reasoning"):
            self.usage[key] += turn_usage[key]
        if cost is not None:
            self.usage["cost"] += cost
            turn_usage["cost"] = cost
        self.log(
            "llm_response",
            turn=self.turns,
            model=message.response_metadata.get("model_name"),
            finish_reason=message.response_metadata.get("finish_reason"),
            text=message.text,
            reasoning="\n".join(
                b.get("reasoning", "") for b in message.content_blocks if b["type"] == "reasoning"
            ),
            tool_calls=[{"name": c["name"], "arguments": c["args"]} for c in message.tool_calls],
            latency_ms=round((time.perf_counter() - started) * 1000),
            usage=turn_usage,
        )
        return response

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        call = request.tool_call
        started = time.perf_counter()
        fields = {"turn": self.turns, "name": call["name"], "args": call["args"]}
        try:
            result = handler(request)
        except Exception as error:
            self.log(
                "tool_call",
                logging.ERROR,
                **fields,
                result=f"{type(error).__name__}: {error}",
                is_error=True,
                duration_ms=round((time.perf_counter() - started) * 1000),
            )
            raise
        messages = _tool_messages(result)
        is_error = any(m.status == "error" for m in messages)
        self.log(
            "tool_call",
            logging.WARNING if is_error else logging.INFO,
            **fields,
            result="\n".join(m.text for m in messages),
            is_error=is_error,
            duration_ms=round((time.perf_counter() - started) * 1000),
        )
        return result


def _recoverable(error: Exception, request: Any) -> str | None:
    # Only failures the model can fix go back to it; anything else ends the run as an error.
    return str(error) if isinstance(error, ToolError) else None


def run_agent(
    *,
    model: BaseChatModel,
    tools: list[BaseTool],
    system_prompt: str,
    briefing: str,
    max_turns: int,
    log: Log,
) -> RunResult:
    run_log = RunLog(log)
    agent = create_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        state_schema=PortfolioState,
        middleware=[
            run_log,
            ToolErrorMiddleware(_recoverable),
            StopOnSave(),
            Nudge(),
            ModelCallLimitMiddleware(run_limit=max_turns, exit_behavior="error"),
        ],
    )
    # Every middleware hook is its own graph node, so a turn costs up to one step per node.
    # Doubling that keeps GraphRecursionError from ever firing before the turn limit.
    steps_per_turn = len(agent.get_graph().nodes) - 2
    try:
        final = agent.invoke(
            {"messages": [HumanMessage(briefing)]},
            {"recursion_limit": steps_per_turn * max_turns * 2},
        )
    except ModelCallLimitExceededError:
        return RunResult("max_turns", None, run_log.turns, run_log.usage)
    except Exception as error:
        return RunResult(
            "error", None, run_log.turns, run_log.usage, f"{type(error).__name__}: {error}"
        )
    portfolio_id = final.get("portfolio_id")
    if portfolio_id is None:
        return RunResult(
            "error", None, run_log.turns, run_log.usage, "agent stopped without a portfolio"
        )
    return RunResult("saved", portfolio_id, run_log.turns, run_log.usage)
```

- [ ] **Step 4: Run the tests**

Run: `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test uv run pytest services/portfolio-builder/tests/test_agent.py -q`
Expected: all pass. If `test_an_unexpected_tool_exception_ends_the_run_as_error` fails because ToolNode itself converts the `RuntimeError` into an error `ToolMessage` (so the run continues), stop and report the observed behaviour instead of changing the assertion.

- [ ] **Step 5: Commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add services/portfolio-builder
git commit -m "feat: create_agent loop with stop-on-save, nudge, turn limit and run log

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Entry point, image, compose and docs

**Files:**
- Replace: `services/portfolio-builder/src/portfolio_builder/__main__.py`
- Replace: `services/portfolio-builder/tests/test_main.py`
- Modify: `docker/portfolio-builder.Dockerfile`, `docker/requirements/portfolio-builder.txt`, `compose.dev.yaml`, `AGENTS.md`, `README.md`
- Create: `docs/superpowers/specs/2026-09-27-portfolio-builder-design.md` (copied from the TS branch with a header)

**Interfaces:**
- Consumes: everything above.
- Produces: `portfolio_builder.__main__.main() -> None`, which raises `SystemExit(0)` when a portfolio is saved and `SystemExit(1)` otherwise.

- [ ] **Step 1: Write the failing tests** — replace `services/portfolio-builder/tests/test_main.py`:

```python
import json

import pytest
import talib
from portfolio_builder import __main__ as entry
from portfolio_builder.agent import RunResult
from portfolio_builder.briefing import Briefing

REQUIRED = {
    "PORTFOLIO_BUILDER_POSTGRES_DSN": "postgresql+psycopg://ktb:ktb@localhost:5432/ktb",
    "PORTFOLIO_BUILDER_QUESTDB_CONF": "http::addr=localhost:9000;",
    "PORTFOLIO_BUILDER_OPENROUTER_API_KEY": "sk-or-v1-secret",
    "PORTFOLIO_BUILDER_LLM_MODEL": "openai/gpt-5.5",
}
BRIEFING = Briefing(None, frozenset(), 0, 0, [1], 1, 1, "brief")


def test_ta_lib_is_importable():
    assert "ROCP" in talib.get_functions()


@pytest.fixture
def env(monkeypatch):
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(entry, "load_briefing", lambda engine, days: BRIEFING)


def _events(capsys):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def test_a_saved_portfolio_exits_zero(env, monkeypatch, capsys):
    captured = {}

    def fake_run_agent(**kwargs):
        captured.update(kwargs)
        return RunResult("saved", 7, 3, {"input": 10})

    monkeypatch.setattr(entry, "run_agent", fake_run_agent)

    with pytest.raises(SystemExit) as exit_:
        entry.main()

    assert exit_.value.code == 0
    events = _events(capsys)
    assert [e["message"] for e in events] == ["run_start", "ingestion", "prompt", "run_end"]
    assert events[-1]["outcome"] == "saved"
    assert events[-1]["portfolio_id"] == 7
    assert len({e["run_id"] for e in events}) == 1
    assert {t.name for t in captured["tools"]} == {
        "get_news_cluster",
        "search_news_cluster",
        "search_graph",
        "find_graph_paths",
        "analyze_technicals",
        "submit_portfolio",
    }
    assert captured["max_turns"] == 150
    assert "sk-or-v1-secret" not in capsys.readouterr().out


def test_max_turns_exits_one(env, monkeypatch, capsys):
    monkeypatch.setattr(entry, "run_agent", lambda **kwargs: RunResult("max_turns", None, 150, {}))

    with pytest.raises(SystemExit) as exit_:
        entry.main()

    assert exit_.value.code == 1
    assert _events(capsys)[-1]["level"] == "ERROR"


def test_a_failure_before_the_agent_exits_one_with_run_end(env, monkeypatch, capsys):
    def broken(engine, days):
        raise RuntimeError("postgres unreachable")

    monkeypatch.setattr(entry, "load_briefing", broken)

    with pytest.raises(SystemExit) as exit_:
        entry.main()

    assert exit_.value.code == 1
    last = _events(capsys)[-1]
    assert last["message"] == "run_end"
    assert last["outcome"] == "error"
    assert "postgres unreachable" in last["error"]
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest services/portfolio-builder/tests/test_main.py -q`
Expected: FAIL (`AttributeError: module 'portfolio_builder.__main__' has no attribute 'load_briefing'`).

- [ ] **Step 3: Implement** — replace `services/portfolio-builder/src/portfolio_builder/__main__.py`:

```python
import logging
import time
import uuid

import sqlalchemy as sa
from ktb_core.logging import setup_logging
from langchain_openrouter import ChatOpenRouter

from portfolio_builder.agent import run_agent
from portfolio_builder.briefing import SYSTEM_PROMPT, load_briefing
from portfolio_builder.log import make_log
from portfolio_builder.market import QuestDBMarket
from portfolio_builder.settings import Settings
from portfolio_builder.tools.graph import graph_tools
from portfolio_builder.tools.news import news_tools
from portfolio_builder.tools.submit import submit_tool
from portfolio_builder.tools.technicals import technicals_tool


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level)
    log = make_log(str(uuid.uuid4()))
    started = time.perf_counter()

    def elapsed_ms() -> int:
        return round((time.perf_counter() - started) * 1000)

    engine = sa.create_engine(settings.postgres_dsn)
    try:
        log(
            "run_start",
            provider="openrouter",
            model=settings.llm_model,
            reasoning_level=settings.thinking_level,
            max_turns=settings.max_turns,
            news_window_days=settings.news_window_days,
        )
        briefing = load_briefing(engine, settings.news_window_days)
        log(
            "ingestion",
            previous_portfolio_id=briefing.previous_portfolio_id,
            previous_holdings=briefing.previous_holdings,
            previous_exits=briefing.previous_exits,
            cluster_ids=briefing.cluster_ids,
            cluster_count=len(briefing.cluster_ids),
            company_count=briefing.company_count,
            theme_count=briefing.theme_count,
            briefing_chars=len(briefing.text),
        )
        log("prompt", system_prompt=SYSTEM_PROMPT, briefing=briefing.text)
        model = ChatOpenRouter(
            model=settings.llm_model,
            api_key=settings.openrouter_api_key,
            reasoning={"effort": settings.thinking_level},
        )
        tools = [
            *news_tools(engine),
            *graph_tools(engine),
            technicals_tool(engine, QuestDBMarket(settings.questdb_conf)),
            submit_tool(
                engine,
                briefing.previous_company_ids,
                f"openrouter/{settings.llm_model}",
                log,
            ),
        ]
        result = run_agent(
            model=model,
            tools=tools,
            system_prompt=SYSTEM_PROMPT,
            briefing=briefing.text,
            max_turns=settings.max_turns,
            log=log,
        )
    except Exception as error:
        log(
            "run_end",
            logging.ERROR,
            outcome="error",
            error=f"{type(error).__name__}: {error}",
            elapsed_ms=elapsed_ms(),
        )
        raise SystemExit(1) from error
    finally:
        engine.dispose()

    saved = result.outcome == "saved"
    log(
        "run_end",
        logging.INFO if saved else logging.ERROR,
        outcome=result.outcome,
        portfolio_id=result.portfolio_id,
        turns=result.turns,
        usage=result.usage,
        error=result.error,
        elapsed_ms=elapsed_ms(),
    )
    raise SystemExit(0 if saved else 1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the whole suite and every CI gate**

```bash
KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run tach check
uv run tach check-external -e packages/market-analyzer,services
uv run deptry services/portfolio-builder/src --config services/portfolio-builder/pyproject.toml
```
Expected: all pass. `deptry` must not report `DEP001`/`DEP003` (undeclared imports).

- [ ] **Step 5: Regenerate the image requirements**

```bash
uv export --package portfolio-builder --no-dev --no-emit-workspace --format requirements-txt -o docker/requirements/portfolio-builder.txt
```

- [ ] **Step 6: Update `docker/portfolio-builder.Dockerfile`** — delete the line
```dockerfile
COPY packages/market-analyzer packages/market-analyzer
```
and change the install line to
```dockerfile
RUN uv pip install --no-deps ./packages/core ./services/portfolio-builder
```

- [ ] **Step 7: Build the image**

```bash
docker build -f docker/portfolio-builder.Dockerfile -t portfolio-builder:dev .
docker run --rm portfolio-builder:dev python -c "import talib, langchain_openrouter, questdb; print('ok')"
```
Expected: builds; prints `ok`.

- [ ] **Step 8: Add the compose job** — in `compose.dev.yaml`, after the `news-graph-builder` service and before `volumes:`, add:

```yaml
  portfolio-builder:
    profiles: ["jobs"]
    build:
      context: .
      dockerfile: docker/portfolio-builder.Dockerfile
    environment:
      - PORTFOLIO_BUILDER_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@postgres:5432/ktb
      - PORTFOLIO_BUILDER_QUESTDB_CONF=http::addr=questdb:9000;
      - PORTFOLIO_BUILDER_OPENROUTER_API_KEY=${OPENROUTER_API_KEY:-}
      - PORTFOLIO_BUILDER_LLM_MODEL
      - PORTFOLIO_BUILDER_THINKING_LEVEL=${PORTFOLIO_BUILDER_THINKING_LEVEL:-medium}
      - PORTFOLIO_BUILDER_NEWS_WINDOW_DAYS=${PORTFOLIO_BUILDER_NEWS_WINDOW_DAYS:-7}
      - PORTFOLIO_BUILDER_MAX_TURNS=${PORTFOLIO_BUILDER_MAX_TURNS:-150}
      - PORTFOLIO_BUILDER_LOG_LEVEL=${PORTFOLIO_BUILDER_LOG_LEVEL:-INFO}
    depends_on:
      postgres:
        condition: service_healthy
      questdb:
        condition: service_healthy
    restart: "no"
```
Validate: `docker compose -f compose.dev.yaml config --quiet` exits 0.

- [ ] **Step 9: Update `AGENTS.md`**

1. In the Commands block, after the `news-graph-builder` compose line, add:
```bash
PORTFOLIO_BUILDER_LLM_MODEL=<openrouter model id> docker compose -f compose.dev.yaml up portfolio-builder   # key from OPENROUTER_API_KEY in .env
```
2. In the environment table, replace the four `PORTFOLIO_BUILDER_*` rows with:
```markdown
| `PORTFOLIO_BUILDER_POSTGRES_DSN` | portfolio-builder | required |
| `PORTFOLIO_BUILDER_QUESTDB_CONF` | portfolio-builder (official client config, e.g. `http::addr=localhost:9000;`) | required |
| `PORTFOLIO_BUILDER_OPENROUTER_API_KEY` | portfolio-builder | required |
| `PORTFOLIO_BUILDER_LLM_MODEL` | portfolio-builder (OpenRouter model id) | required |
| `PORTFOLIO_BUILDER_THINKING_LEVEL` | portfolio-builder (`none`/`minimal`/`low`/`medium`/`high`/`xhigh`) | `medium` |
| `PORTFOLIO_BUILDER_NEWS_WINDOW_DAYS` | portfolio-builder | `7` |
| `PORTFOLIO_BUILDER_MAX_TURNS` | portfolio-builder | `150` |
| `PORTFOLIO_BUILDER_LOG_LEVEL` | portfolio-builder | `INFO` |
```
3. In the member table, replace the portfolio-builder row with:
```markdown
| `services/portfolio-builder` | service | cron: `main()` runs once and exits | `ktb-core` |
```
4. Replace the line starting `- **Work queue:**` with:
```markdown
- **Work queue:** SQS in production, Redis in development. No consumer yet; portfolio-rebalancer-http consumes it after the MVP.
```
5. In the "Services communicate only through datastores" bullet, replace `and portfolio-builder reads them all.` with `and portfolio-builder reads them all plus QuestDB bars and writes \`portfolios\`, \`portfolio_holdings\`, \`portfolio_exits\` (design: \`docs/superpowers/specs/2026-09-28-portfolio-builder-langchain-design.md\`).`
6. Add this bullet after the market-analyzer bullet:
```markdown
- **portfolio-builder runs a LangChain `create_agent` agent on OpenRouter** and computes technical evidence with TA-Lib directly (not `ktb-market-analyzer`). The OpenRouter key lives only in the environment.
```

- [ ] **Step 10: Update `README.md`** — replace the four rows under `### portfolio-builder (\`PORTFOLIO_BUILDER_\`)` with:
```markdown
| `PORTFOLIO_BUILDER_POSTGRES_DSN` | 필수 | |
| `PORTFOLIO_BUILDER_QUESTDB_CONF` | 필수 | 예: `http::addr=localhost:9000;` |
| `PORTFOLIO_BUILDER_OPENROUTER_API_KEY` | 필수 | |
| `PORTFOLIO_BUILDER_LLM_MODEL` | 필수 | OpenRouter 모델 ID |
| `PORTFOLIO_BUILDER_THINKING_LEVEL` | | `medium` |
| `PORTFOLIO_BUILDER_NEWS_WINDOW_DAYS` | | `7` |
| `PORTFOLIO_BUILDER_MAX_TURNS` | | `150` |
| `PORTFOLIO_BUILDER_LOG_LEVEL` | | `INFO` |
```
Keep the table's existing header row. Also change line 7's description to `- \`portfolio-builder\`: 뉴스·지식 그래프·기술적 근거로 모델 포트폴리오 생성 (LangChain 에이전트)`.

- [ ] **Step 11: Bring over the superseded spec with a header**

```bash
git show claude/typescript-pi-langchain-migration-e24c71:docs/superpowers/specs/2026-09-27-portfolio-builder-design.md > docs/superpowers/specs/2026-09-27-portfolio-builder-design.md
```
Then insert these lines directly under its first heading line:
```markdown

> **Superseded in part** by `2026-09-28-portfolio-builder-langchain-design.md`: the service is Python with a LangChain agent on OpenRouter, reads QuestDB directly and never shipped the TypeScript/Pi runtime or market-analyzer-mcp. Tables, validation, briefing and tools below still apply.
```

- [ ] **Step 12: Re-run everything and commit**

```bash
uv run ruff check --fix . && uv run ruff format .
KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test uv run pytest -q
git add -A services/portfolio-builder docker compose.dev.yaml AGENTS.md README.md docs/superpowers/specs/2026-09-27-portfolio-builder-design.md
git commit -m "feat: portfolio-builder entry point, image, compose job and docs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
