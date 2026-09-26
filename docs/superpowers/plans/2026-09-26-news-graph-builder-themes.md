# news-graph-builder Themes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sync Kiwoom themes and their KOSPI 200 members into `themes` / `theme_companies` on every news-graph-builder run, flagging each theme's main stocks.

**Architecture:** A new shared `kiwoom` module issues one access token per run and pages through Kiwoom REST endpoints; `company` moves onto it. A new `theme` domain fetches all themes (`ka90001`), the KOSPI 200 constituents (`ka20002`) and each theme's members (`ka90002`), filters members to KOSPI 200 stocks present in `companies`, sets `is_main` from `main_stk`, and replaces both tables in one transaction. `main()` runs it after the company sync and before graph building; a theme failure does not stop graph building but makes the run exit 1.

**Tech Stack:** Python 3.13, SQLAlchemy 2 Core, psycopg 3, Alembic, httpx, pydantic-settings, tach, pytest.

**Spec:** `docs/superpowers/specs/2026-09-24-news-graph-builder-design.md` (§4 run flow, §6 step 1, §7 theme sync, §9 code and settings, §12 testing)

## Global Constraints

- Work from the worktree root. Tests run against the separate `news_test` database, never `news`:
  `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/news_test uv run pytest …` (written `PYTEST …` below). After adding migration `0004`, apply it once:
  `KTB_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/news_test uv run alembic upgrade head`.
- Never read real API keys, `.env` files or `~/.config/ktb4-ai/`; make no real network calls. Tests use fakes and `httpx.MockTransport` only.
- Code style (AGENTS.md and the user's review): verb-first function names (`fetch_…`, `find_…`, `replace_…`, `sync_…`); keyword-only parameters where a value's meaning isn't obvious; assign results to variables instead of nesting calls as arguments; no comments or docstrings that restate names; no thin wrappers.
- Each domain package exposes its public API in `__init__.py`; `tach.toml` lists every cross-package import in `depends_on` and `[[interfaces]]`. `uv run tach check` must pass after every task.
- Every settings class uses `env_prefix="NEWS_GRAPH_BUILDER_"`, `extra="ignore"`, `hide_input_in_errors=True`. Functions that need settings take `settings: X | None = None` and fall back to `X()`.
- Tests that construct `KiwoomSettings` set `kiwoom_request_interval=0` so they don't sleep.
- Some SQL string literals in the test code below exceed 100 characters; `ruff format` does not split strings, so wrap those by hand (implicit string concatenation) when `ruff check` reports E501.
- Before every commit: `uv run ruff check --fix . && uv run ruff format .`, then the full suite once (`PYTEST -q`). Commit messages use `feat` / `fix` / `refactor` / `chore` / `docs` prefixes and end with a `Co-Authored-By:` line for the model that wrote the commit.
- Branch: `feat/7/news-graph-builder` (PR #30). Pushes use `--force-with-lease`.

## File Structure

```
infrastructure/postgres/migrations/versions/0004_create_themes.py   Task 1
infrastructure/postgres/tests/test_migrations.py                     Task 1
services/news-graph-builder/src/news_graph_builder/
  database.py                   + themes, theme_companies            Task 1
  kiwoom/__init__.py            public API                           Task 2
  kiwoom/settings.py            KiwoomSettings                       Task 2
  kiwoom/client.py              fetch_token, fetch_pages             Task 2
  company/kiwoom.py             fetch_kospi on kiwoom.fetch_pages    Task 2
  company/settings.py           CompanySettings: dart_api_key only   Task 2
  theme/__init__.py             public API                           Task 3, 4
  theme/dto.py                  Theme, ThemeMember                   Task 3
  theme/kiwoom.py               fetch_themes, fetch_kospi200_codes,
                                fetch_theme_members                  Task 3
  theme/repository.py           find_corp_codes_by_stock_code,
                                replace_themes                       Task 4
  theme/service.py              sync_themes                          Task 4
  __main__.py                   token once, theme sync step          Task 5
services/news-graph-builder/tests/
  conftest.py                   TRUNCATE list                        Task 1
  kiwoom/test_client.py, kiwoom/test_settings.py                     Task 2
  company/test_kiwoom.py, company/test_settings.py                   Task 2
  theme/test_kiwoom.py                                               Task 3
  theme/test_service.py                                              Task 4
  test_main.py                                                       Task 5
tach.toml                                                            Task 2, 3, 5
AGENTS.md                                                            Task 5
```

Import graph (no cycles): `common`, `database`, `kiwoom` import no sibling package; `company` → `common`, `database`, `kiwoom`; `theme` → `common`, `database`, `kiwoom`; `graph` → `common`, `company`, `database`; root (`__main__`) → `cluster`, `company`, `graph`, `kiwoom`, `theme`.

---

### Task 1: Theme tables (migration `0004`)

**Files:**
- Create: `infrastructure/postgres/migrations/versions/0004_create_themes.py`
- Modify: `services/news-graph-builder/src/news_graph_builder/database.py` (append two tables)
- Modify: `infrastructure/postgres/tests/test_migrations.py`
- Modify: `services/news-graph-builder/tests/conftest.py:10-13`

**Interfaces:**
- Produces: tables `themes(theme_code PK, name, synced_at)` and `theme_companies(theme_code → themes ON DELETE CASCADE, corp_code → companies ON DELETE CASCADE, is_main boolean NOT NULL DEFAULT false, PK (theme_code, corp_code), INDEX theme_companies_corp_code_idx)`; SQLAlchemy mirrors `database.themes` and `database.theme_companies`.

- [ ] **Step 1: Write the failing migration tests**

In `infrastructure/postgres/tests/test_migrations.py`, extend the owned-tables set of the `news_graph_builder.database` row in `test_service_tables_match_the_migrated_schema` with `"themes"` and `"theme_companies"`, and append:

```python
def test_theme_memberships_cascade_from_themes_and_companies(pg_dsn, pg_engine, monkeypatch):
    monkeypatch.setenv("KTB_POSTGRES_DSN", pg_dsn)
    command.upgrade(_alembic_config(), "head")
    try:
        with pg_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO companies (corp_code, stock_code, corp_name)"
                    " VALUES ('00126380', '005930', '삼성전자'), ('00164779', '000660', 'SK하이닉스')"
                )
            )
            conn.execute(sa.text("INSERT INTO themes (theme_code, name) VALUES ('1', 'HBM'), ('2', '반도체')"))
            conn.execute(
                sa.text(
                    "INSERT INTO theme_companies (theme_code, corp_code, is_main)"
                    " VALUES ('1', '00126380', true), ('2', '00164779', false)"
                )
            )
        with pg_engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM themes WHERE theme_code = '1'"))
            conn.execute(sa.text("DELETE FROM companies WHERE corp_code = '00164779'"))
        with pg_engine.connect() as conn:
            remaining = conn.execute(sa.text("SELECT count(*) FROM theme_companies")).scalar_one()
        assert remaining == 0
    finally:
        with pg_engine.begin() as conn:
            conn.execute(sa.text("TRUNCATE theme_companies, themes, companies CASCADE"))


def test_downgrade_to_0003_removes_the_theme_tables(pg_dsn, pg_engine, monkeypatch):
    monkeypatch.setenv("KTB_POSTGRES_DSN", pg_dsn)
    config = _alembic_config()

    command.downgrade(config, "0003")
    with pg_engine.connect() as conn:
        assert conn.execute(sa.text("SELECT to_regclass('themes')")).scalar() is None
        assert conn.execute(sa.text("SELECT to_regclass('theme_companies')")).scalar() is None

    command.upgrade(config, "head")
    with pg_engine.connect() as conn:
        assert conn.execute(sa.text("SELECT to_regclass('theme_companies')")).scalar() is not None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTEST infrastructure/postgres/tests/test_migrations.py -k "theme or service_tables" -v`
Expected: FAIL — `themes` does not exist (and the metadata comparison fails because `database.py` has no theme tables).

- [ ] **Step 3: Write migration `0004`**

`infrastructure/postgres/migrations/versions/0004_create_themes.py`:

```python
"""create Kiwoom theme tables

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-26
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "themes",
        sa.Column("theme_code", sa.Text, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_table(
        "theme_companies",
        sa.Column(
            "theme_code",
            sa.Text,
            sa.ForeignKey("themes.theme_code", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "corp_code",
            sa.Text,
            sa.ForeignKey("companies.corp_code", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("is_main", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.create_index("theme_companies_corp_code_idx", "theme_companies", ["corp_code"])


def downgrade() -> None:
    op.drop_table("theme_companies")
    op.drop_table("themes")
```

- [ ] **Step 4: Mirror the tables in `database.py`**

Append to `services/news-graph-builder/src/news_graph_builder/database.py`:

```python
themes = sa.Table(
    "themes",
    metadata,
    sa.Column("theme_code", sa.Text, primary_key=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column(
        "synced_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    ),
)

theme_companies = sa.Table(
    "theme_companies",
    metadata,
    sa.Column(
        "theme_code",
        sa.Text,
        sa.ForeignKey("themes.theme_code", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column(
        "corp_code",
        sa.Text,
        sa.ForeignKey("companies.corp_code", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column("is_main", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Index("theme_companies_corp_code_idx", "corp_code"),
)
```

In `services/news-graph-builder/tests/conftest.py` change `TABLES` to:

```python
TABLES = (
    "theme_companies, themes, relations, cluster_entities, cluster_summaries, entities,"
    " company_aliases, companies, article_clusters, clusters, articles"
)
```

Apply the migration to the test database:
`KTB_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/news_test uv run alembic upgrade head`

- [ ] **Step 5: Run the tests to verify they pass**

Run: `PYTEST infrastructure services/news-graph-builder -q`
Expected: PASS, including both new tests and the metadata comparison for `news_graph_builder.database`.

- [ ] **Step 6: Commit**

```bash
git add infrastructure services/news-graph-builder
git commit -m "feat(graph-builder): add themes and theme_companies tables"
```

---

### Task 2: Shared `kiwoom` module; `company` moves onto it

**Files:**
- Create: `services/news-graph-builder/src/news_graph_builder/kiwoom/__init__.py`, `kiwoom/settings.py`, `kiwoom/client.py`
- Modify: `services/news-graph-builder/src/news_graph_builder/company/kiwoom.py` (whole file)
- Modify: `services/news-graph-builder/src/news_graph_builder/company/settings.py` (whole file)
- Modify: `services/news-graph-builder/src/news_graph_builder/__main__.py:42-46` (token)
- Create: `services/news-graph-builder/tests/kiwoom/test_client.py`, `tests/kiwoom/test_settings.py`
- Modify: `services/news-graph-builder/tests/company/test_kiwoom.py` (whole file), `tests/company/test_settings.py` (whole file), `tests/test_main.py` (fixture)
- Modify: `tach.toml`

**Interfaces:**
- Produces:
  - `kiwoom.KiwoomSettings` with `kiwoom_app_key: SecretStr`, `kiwoom_secret_key: SecretStr`, `kiwoom_base_uri: str = "https://api.kiwoom.com"`, `kiwoom_request_interval: float = 0.2` (`ge=0`)
  - `kiwoom.fetch_token(client: httpx.Client, *, settings: KiwoomSettings | None = None) -> str`
  - `kiwoom.fetch_pages(client: httpx.Client, *, token: str, api_id: str, path: str, body: dict[str, str], list_key: str, settings: KiwoomSettings | None = None) -> list[dict[str, Any]]` — `POST {base}/api/dostk/{path}`, sleeps `kiwoom_request_interval` before every request, follows `cont-yn` / `next-key`, raises `RuntimeError` on a non-zero `return_code`; a page without `list_key` contributes no rows.
  - `company.fetch_kospi(client, *, token: str, settings: KiwoomSettings | None = None) -> list[tuple[str, str]]` (changed signature)
  - `company.settings.CompanySettings` now holds only `dart_api_key: SecretStr`.

- [ ] **Step 1: Write the failing tests**

`services/news-graph-builder/tests/kiwoom/test_client.py`:

```python
import json

import httpx
import pytest
from news_graph_builder.kiwoom import KiwoomSettings, fetch_pages, fetch_token
from news_graph_builder.kiwoom import client as kiwoom_client

BASE_URI = "https://kiwoom.test"
SETTINGS = KiwoomSettings(
    kiwoom_app_key="app",
    kiwoom_secret_key="secret",
    kiwoom_base_uri=BASE_URI,
    kiwoom_request_interval=0,
)


def replying(pages: list[tuple[dict, dict]], seen: list) -> httpx.Client:
    remaining = list(pages)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body, headers = remaining.pop(0)
        return httpx.Response(200, json=body, headers=headers)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_token_posts_the_app_credentials():
    seen = []
    client = replying([({"return_code": 0, "token": "tok"}, {})], seen)

    token = fetch_token(client, settings=SETTINGS)

    assert token == "tok"
    assert str(seen[0].url) == f"{BASE_URI}/oauth2/token"
    assert json.loads(seen[0].content) == {
        "grant_type": "client_credentials",
        "appkey": "app",
        "secretkey": "secret",
    }


def test_a_refused_token_raises():
    client = replying([({"return_code": 3, "return_msg": "invalid appkey"}, {})], [])

    with pytest.raises(RuntimeError, match="invalid appkey"):
        fetch_token(client, settings=SETTINGS)


def test_fetch_pages_follows_continuation_and_sleeps_between_requests(monkeypatch):
    sleeps = []
    monkeypatch.setattr(kiwoom_client.time, "sleep", sleeps.append)
    seen = []
    client = replying(
        [
            ({"return_code": 0, "rows": [{"a": 1}]}, {"cont-yn": "Y", "next-key": "k1"}),
            ({"return_code": 0, "rows": [{"a": 2}]}, {"cont-yn": "N", "next-key": ""}),
        ],
        seen,
    )
    settings = SETTINGS.model_copy(update={"kiwoom_request_interval": 0.5})

    rows = fetch_pages(
        client,
        token="tok",
        api_id="ka99999",
        path="thme",
        body={"x": "1"},
        list_key="rows",
        settings=settings,
    )

    assert rows == [{"a": 1}, {"a": 2}]
    assert sleeps == [0.5, 0.5]
    first, second = seen
    assert str(first.url) == f"{BASE_URI}/api/dostk/thme"
    assert first.headers["api-id"] == "ka99999"
    assert first.headers["authorization"] == "Bearer tok"
    assert (first.headers["cont-yn"], first.headers["next-key"]) == ("N", "")
    assert json.loads(first.content) == {"x": "1"}
    assert (second.headers["cont-yn"], second.headers["next-key"]) == ("Y", "k1")


def test_a_page_without_the_list_key_contributes_no_rows():
    client = replying([({"return_code": 0}, {})], [])

    rows = fetch_pages(
        client, token="tok", api_id="ka99999", path="thme", body={}, list_key="rows", settings=SETTINGS
    )

    assert rows == []


def test_a_failed_page_raises():
    client = replying([({"return_code": 5, "return_msg": "rate limited"}, {})], [])

    with pytest.raises(RuntimeError, match="ka99999 failed: 5 rate limited"):
        fetch_pages(
            client, token="tok", api_id="ka99999", path="thme", body={}, list_key="rows", settings=SETTINGS
        )
```

`services/news-graph-builder/tests/kiwoom/test_settings.py`:

```python
import pytest
from news_graph_builder.kiwoom import KiwoomSettings
from pydantic import ValidationError

REQUIRED = {
    "NEWS_GRAPH_BUILDER_KIWOOM_APP_KEY": "app-key",
    "NEWS_GRAPH_BUILDER_KIWOOM_SECRET_KEY": "secret-key",
}


@pytest.fixture
def required_env(monkeypatch):
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)


def test_defaults(required_env):
    settings = KiwoomSettings()

    assert settings.kiwoom_base_uri == "https://api.kiwoom.com"
    assert settings.kiwoom_request_interval == 0.2


def test_keys_are_hidden_from_repr(required_env):
    settings = KiwoomSettings()

    for key in REQUIRED.values():
        assert key not in repr(settings)


@pytest.mark.parametrize("missing", sorted(REQUIRED))
def test_missing_required_value_raises(required_env, monkeypatch, missing):
    monkeypatch.delenv(missing)

    with pytest.raises(ValidationError):
        KiwoomSettings()


def test_a_negative_interval_raises(required_env, monkeypatch):
    monkeypatch.setenv("NEWS_GRAPH_BUILDER_KIWOOM_REQUEST_INTERVAL", "-1")

    with pytest.raises(ValidationError):
        KiwoomSettings()


def test_secrets_not_in_validation_error(required_env, monkeypatch):
    monkeypatch.delenv("NEWS_GRAPH_BUILDER_KIWOOM_SECRET_KEY")
    monkeypatch.setenv("NEWS_GRAPH_BUILDER_KIWOOM_APP_KEY", "FAKE-APP-KEY-123")

    with pytest.raises(ValidationError) as info:
        KiwoomSettings()

    assert "FAKE-APP-KEY-123" not in str(info.value)
```

Replace `services/news-graph-builder/tests/company/test_kiwoom.py` with:

```python
import json

import httpx
from news_graph_builder.company import fetch_kospi
from news_graph_builder.kiwoom import KiwoomSettings

SETTINGS = KiwoomSettings(
    kiwoom_app_key="app",
    kiwoom_secret_key="secret",
    kiwoom_base_uri="https://kiwoom.test",
    kiwoom_request_interval=0,
)


def test_fetch_kospi_lists_code_and_name_from_ka10099():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        rows = [{"code": "005930", "name": "삼성전자"}, {"code": "000660", "name": "SK하이닉스"}]
        return httpx.Response(200, json={"return_code": 0, "list": rows})

    client = httpx.Client(transport=httpx.MockTransport(handler))

    kospi = fetch_kospi(client, token="tok", settings=SETTINGS)

    assert kospi == [("005930", "삼성전자"), ("000660", "SK하이닉스")]
    assert seen[0].url.path == "/api/dostk/stkinfo"
    assert seen[0].headers["api-id"] == "ka10099"
    assert json.loads(seen[0].content) == {"mrkt_tp": "0"}
```

Replace `services/news-graph-builder/tests/company/test_settings.py` with:

```python
import pytest
from news_graph_builder.company.settings import CompanySettings
from pydantic import ValidationError


def test_the_dart_key_is_hidden_from_repr(monkeypatch):
    monkeypatch.setenv("NEWS_GRAPH_BUILDER_DART_API_KEY", "dart-key")

    settings = CompanySettings()

    assert settings.dart_api_key.get_secret_value() == "dart-key"
    assert "dart-key" not in repr(settings)


def test_a_missing_dart_key_raises(monkeypatch):
    monkeypatch.delenv("NEWS_GRAPH_BUILDER_DART_API_KEY", raising=False)

    with pytest.raises(ValidationError):
        CompanySettings()
```

In `services/news-graph-builder/tests/company/test_dart.py`, `settings_with` becomes:

```python
def settings_with(dart_api_key: str) -> CompanySettings:
    return CompanySettings(dart_api_key=dart_api_key)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTEST services/news-graph-builder/tests/kiwoom services/news-graph-builder/tests/company -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'news_graph_builder.kiwoom'`.

- [ ] **Step 3: Write the `kiwoom` module**

`kiwoom/settings.py`:

```python
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class KiwoomSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEWS_GRAPH_BUILDER_",
        extra="ignore",
        hide_input_in_errors=True,
    )

    kiwoom_app_key: SecretStr
    kiwoom_secret_key: SecretStr
    kiwoom_base_uri: str = "https://api.kiwoom.com"
    kiwoom_request_interval: float = Field(default=0.2, ge=0)
```

`kiwoom/client.py`:

```python
import time
from typing import Any

import httpx

from news_graph_builder.kiwoom.settings import KiwoomSettings

HEADERS = {"content-type": "application/json;charset=UTF-8"}


def fetch_token(client: httpx.Client, *, settings: KiwoomSettings | None = None) -> str:
    settings = settings or KiwoomSettings()
    body = {
        "grant_type": "client_credentials",
        "appkey": settings.kiwoom_app_key.get_secret_value(),
        "secretkey": settings.kiwoom_secret_key.get_secret_value(),
    }
    response = client.post(
        f"{settings.kiwoom_base_uri.rstrip('/')}/oauth2/token", json=body, headers=HEADERS
    )
    reply = response.raise_for_status().json()
    if "token" not in reply:
        raise RuntimeError(
            f"Kiwoom token refused: {reply.get('return_code')} {reply.get('return_msg')}"
        )
    return reply["token"]


def fetch_pages(
    client: httpx.Client,
    *,
    token: str,
    api_id: str,
    path: str,
    body: dict[str, str],
    list_key: str,
    settings: KiwoomSettings | None = None,
) -> list[dict[str, Any]]:
    settings = settings or KiwoomSettings()
    url = f"{settings.kiwoom_base_uri.rstrip('/')}/api/dostk/{path}"
    headers = HEADERS | {
        "authorization": f"Bearer {token}",
        "api-id": api_id,
        "cont-yn": "N",
        "next-key": "",
    }
    rows: list[dict[str, Any]] = []
    while True:
        time.sleep(settings.kiwoom_request_interval)
        response = client.post(url, json=body, headers=headers).raise_for_status()
        page = response.json()
        if page.get("return_code") != 0:
            raise RuntimeError(
                f"Kiwoom {api_id} failed: {page.get('return_code')} {page.get('return_msg')}"
            )
        rows += page.get(list_key, [])
        if response.headers.get("cont-yn") != "Y":
            break
        headers |= {"cont-yn": "Y", "next-key": response.headers["next-key"]}
    return rows
```

`kiwoom/__init__.py`:

```python
from news_graph_builder.kiwoom.client import fetch_pages, fetch_token
from news_graph_builder.kiwoom.settings import KiwoomSettings

__all__ = ["KiwoomSettings", "fetch_pages", "fetch_token"]
```

- [ ] **Step 4: Move `company` onto it**

Replace `company/kiwoom.py`:

```python
import httpx

from news_graph_builder.kiwoom import KiwoomSettings, fetch_pages


def fetch_kospi(
    client: httpx.Client, *, token: str, settings: KiwoomSettings | None = None
) -> list[tuple[str, str]]:
    rows = fetch_pages(
        client,
        token=token,
        api_id="ka10099",
        path="stkinfo",
        body={"mrkt_tp": "0"},
        list_key="list",
        settings=settings,
    )
    return [(row["code"], row["name"]) for row in rows]
```

Replace `company/settings.py`:

```python
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class CompanySettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEWS_GRAPH_BUILDER_",
        extra="ignore",
        hide_input_in_errors=True,
    )

    dart_api_key: SecretStr
```

In `__main__.py`: add `from news_graph_builder.kiwoom import fetch_token` to the imports, and replace the start of the company-sync `try:` block

```python
        try:
            kospi = fetch_kospi(client)
```

with

```python
        try:
            token = fetch_token(client)
            kospi = fetch_kospi(client, token=token)
```

(Task 5 restructures this further; here the token only needs to reach `fetch_kospi`.)

In `tests/test_main.py`, the `companies_api` fixture gains a token fake:

```python
@pytest.fixture
def companies_api(monkeypatch):
    monkeypatch.setattr(entry, "fetch_token", lambda client, **kwargs: "tok")
    monkeypatch.setattr(entry, "fetch_kospi", lambda client, **kwargs: [("005930", "삼성전자")])
    monkeypatch.setattr(entry, "fetch_corp_codes", lambda **kwargs: [SAMSUNG])
```

and in `test_a_failed_first_sync_exits_before_any_llm_call` add, before the existing `monkeypatch.setattr(entry, "fetch_kospi", unreachable)` line:

```python
    monkeypatch.setattr(entry, "fetch_token", lambda client, **kwargs: "tok")
```

`tach.toml`: add the module and interface, and let `company` and the root depend on it:

```toml
[[modules]]
path = "news_graph_builder.kiwoom"
depends_on = []

[[interfaces]]
expose = ["KiwoomSettings", "fetch_pages", "fetch_token"]
from = ["news_graph_builder.kiwoom"]
```

- `news_graph_builder.company` → `depends_on = ["news_graph_builder.common", "news_graph_builder.database", "news_graph_builder.kiwoom"]`
- `news_graph_builder` (root) → add `"news_graph_builder.kiwoom"` to its `depends_on`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `PYTEST services/news-graph-builder -q` and `uv run tach check`
Expected: PASS; tach reports no errors.

- [ ] **Step 6: Commit**

```bash
git add services/news-graph-builder tach.toml
git commit -m "refactor(graph-builder): share Kiwoom token and paging in a kiwoom module"
```

---

### Task 3: Theme fetches from Kiwoom

**Files:**
- Create: `services/news-graph-builder/src/news_graph_builder/theme/__init__.py`, `theme/dto.py`, `theme/kiwoom.py`
- Create: `services/news-graph-builder/tests/theme/test_kiwoom.py`
- Modify: `tach.toml`

**Interfaces:**
- Consumes: `kiwoom.fetch_pages`, `kiwoom.KiwoomSettings` (Task 2).
- Produces:
  - `theme.Theme(NamedTuple)`: `code: str`, `name: str`, `main_stocks: str` (raw `main_stk`, `""` when absent)
  - `theme.ThemeMember(NamedTuple)`: `stock_code: str`, `stock_name: str`
  - `theme.fetch_themes(client, *, token: str, settings: KiwoomSettings | None = None) -> list[Theme]`
  - `theme.fetch_kospi200_codes(client, *, token: str, settings: KiwoomSettings | None = None) -> set[str]`
  - `theme.fetch_theme_members(client, *, token: str, theme_codes: Sequence[str], settings: KiwoomSettings | None = None) -> dict[str, list[ThemeMember]]`
  - Every returned stock code is cut at the first `_`.

- [ ] **Step 1: Write the failing tests**

`services/news-graph-builder/tests/theme/test_kiwoom.py`:

```python
import json

import httpx
from news_graph_builder.kiwoom import KiwoomSettings
from news_graph_builder.theme import (
    Theme,
    ThemeMember,
    fetch_kospi200_codes,
    fetch_theme_members,
    fetch_themes,
)

SETTINGS = KiwoomSettings(
    kiwoom_app_key="app",
    kiwoom_secret_key="secret",
    kiwoom_base_uri="https://kiwoom.test",
    kiwoom_request_interval=0,
)


def replying(bodies: list[dict], seen: list) -> httpx.Client:
    remaining = list(bodies)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"return_code": 0, **remaining.pop(0)})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_themes_reads_ka90001():
    seen = []
    client = replying(
        [
            {
                "thema_grp": [
                    {"thema_grp_cd": "100", "thema_nm": "HBM", "main_stk": "SK하이닉스"},
                    {"thema_grp_cd": "200", "thema_nm": "2차전지"},
                ]
            }
        ],
        seen,
    )

    themes = fetch_themes(client, token="tok", settings=SETTINGS)

    assert themes == [Theme("100", "HBM", "SK하이닉스"), Theme("200", "2차전지", "")]
    assert seen[0].url.path == "/api/dostk/thme"
    assert seen[0].headers["api-id"] == "ka90001"
    assert json.loads(seen[0].content) == {
        "qry_tp": "0",
        "date_tp": "1",
        "flu_pl_amt_tp": "1",
        "stex_tp": "1",
    }


def test_fetch_kospi200_codes_reads_sector_201_and_cuts_suffixes():
    seen = []
    client = replying([{"inds_stkpc": [{"stk_cd": "005930"}, {"stk_cd": "000660_AL"}]}], seen)

    codes = fetch_kospi200_codes(client, token="tok", settings=SETTINGS)

    assert codes == {"005930", "000660"}
    assert seen[0].url.path == "/api/dostk/sect"
    assert seen[0].headers["api-id"] == "ka20002"
    assert json.loads(seen[0].content) == {"mrkt_tp": "2", "inds_cd": "201", "stex_tp": "1"}


def test_fetch_theme_members_calls_ka90002_per_theme():
    seen = []
    client = replying(
        [
            {"thema_comp_stk": [{"stk_cd": "000660_AL", "stk_nm": "SK하이닉스"}]},
            {"thema_comp_stk": []},
        ],
        seen,
    )

    members = fetch_theme_members(client, token="tok", theme_codes=["100", "200"], settings=SETTINGS)

    assert members == {"100": [ThemeMember("000660", "SK하이닉스")], "200": []}
    assert [request.headers["api-id"] for request in seen] == ["ka90002", "ka90002"]
    assert json.loads(seen[0].content) == {"thema_grp_cd": "100", "stex_tp": "1", "date_tp": "1"}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTEST services/news-graph-builder/tests/theme -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'news_graph_builder.theme'`.

- [ ] **Step 3: Write the DTOs and fetches**

`theme/dto.py`:

```python
from typing import NamedTuple


class Theme(NamedTuple):
    code: str
    name: str
    main_stocks: str


class ThemeMember(NamedTuple):
    stock_code: str
    stock_name: str
```

`theme/kiwoom.py`:

```python
from collections.abc import Sequence

import httpx

from news_graph_builder.kiwoom import KiwoomSettings, fetch_pages
from news_graph_builder.theme.dto import Theme, ThemeMember


def fetch_themes(
    client: httpx.Client, *, token: str, settings: KiwoomSettings | None = None
) -> list[Theme]:
    rows = fetch_pages(
        client,
        token=token,
        api_id="ka90001",
        path="thme",
        body={"qry_tp": "0", "date_tp": "1", "flu_pl_amt_tp": "1", "stex_tp": "1"},
        list_key="thema_grp",
        settings=settings,
    )
    return [Theme(row["thema_grp_cd"], row["thema_nm"], row.get("main_stk") or "") for row in rows]


def fetch_kospi200_codes(
    client: httpx.Client, *, token: str, settings: KiwoomSettings | None = None
) -> set[str]:
    rows = fetch_pages(
        client,
        token=token,
        api_id="ka20002",
        path="sect",
        body={"mrkt_tp": "2", "inds_cd": "201", "stex_tp": "1"},
        list_key="inds_stkpc",
        settings=settings,
    )
    return {strip_market_suffix(row["stk_cd"]) for row in rows}


def fetch_theme_members(
    client: httpx.Client,
    *,
    token: str,
    theme_codes: Sequence[str],
    settings: KiwoomSettings | None = None,
) -> dict[str, list[ThemeMember]]:
    members: dict[str, list[ThemeMember]] = {}
    for theme_code in theme_codes:
        rows = fetch_pages(
            client,
            token=token,
            api_id="ka90002",
            path="thme",
            body={"thema_grp_cd": theme_code, "stex_tp": "1", "date_tp": "1"},
            list_key="thema_comp_stk",
            settings=settings,
        )
        members[theme_code] = [
            ThemeMember(strip_market_suffix(row["stk_cd"]), row["stk_nm"]) for row in rows
        ]
    return members


def strip_market_suffix(stock_code: str) -> str:
    return stock_code.split("_", 1)[0]
```

`theme/__init__.py` (Task 4 extends it):

```python
from news_graph_builder.theme.dto import Theme, ThemeMember
from news_graph_builder.theme.kiwoom import fetch_kospi200_codes, fetch_theme_members, fetch_themes

__all__ = ["Theme", "ThemeMember", "fetch_kospi200_codes", "fetch_theme_members", "fetch_themes"]
```

`tach.toml`:

```toml
[[modules]]
path = "news_graph_builder.theme"
depends_on = [
    "news_graph_builder.common",
    "news_graph_builder.database",
    "news_graph_builder.kiwoom",
]

[[interfaces]]
expose = ["Theme", "ThemeMember", "fetch_kospi200_codes", "fetch_theme_members", "fetch_themes"]
from = ["news_graph_builder.theme"]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTEST services/news-graph-builder -q` and `uv run tach check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/news-graph-builder tach.toml
git commit -m "feat(graph-builder): fetch Kiwoom themes, KOSPI 200 codes and theme members"
```

---

### Task 4: Theme filter, main flag and replace

**Files:**
- Create: `services/news-graph-builder/src/news_graph_builder/theme/repository.py`, `theme/service.py`
- Modify: `services/news-graph-builder/src/news_graph_builder/theme/__init__.py`
- Create: `services/news-graph-builder/tests/theme/test_service.py`
- Modify: `tach.toml` (theme interface)

**Interfaces:**
- Consumes: `Theme`, `ThemeMember` (Task 3); `database.companies`, `database.themes`, `database.theme_companies` (Task 1); `common.normalize`.
- Produces:
  - `theme/repository.py`: `find_corp_codes_by_stock_code(conn) -> dict[str, str]` (the most recently synced company wins a shared code); `replace_themes(conn, *, themes: Sequence[Theme], memberships: Sequence[tuple[str, str, bool]]) -> None` (tuples are `(theme_code, corp_code, is_main)`).
  - `theme.sync_themes(conn, *, themes: Sequence[Theme], kospi200_codes: set[str], members: dict[str, list[ThemeMember]]) -> tuple[int, int, int]` returning `(kept, main, skipped)`; raises `ValueError` on zero themes or zero KOSPI 200 codes before writing anything.

- [ ] **Step 1: Write the failing tests**

`services/news-graph-builder/tests/theme/test_service.py`:

```python
import pytest
import sqlalchemy as sa
from news_graph_builder.theme import Theme, ThemeMember, sync_themes

SAMSUNG = ("00126380", "005930", "삼성전자")
HYNIX = ("00164779", "000660", "SK하이닉스")
KOSPI200 = {"005930", "000660"}


@pytest.fixture
def companies(engine):
    with engine.begin() as conn:
        for corp_code, stock_code, corp_name in (SAMSUNG, HYNIX):
            conn.execute(
                sa.text(
                    "INSERT INTO companies (corp_code, stock_code, corp_name)"
                    " VALUES (:corp_code, :stock_code, :corp_name)"
                ),
                {"corp_code": corp_code, "stock_code": stock_code, "corp_name": corp_name},
            )


def sync(engine, themes, members, kospi200=KOSPI200):
    with engine.begin() as conn:
        return sync_themes(conn, themes=themes, kospi200_codes=kospi200, members=members)


def memberships(engine):
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text("SELECT theme_code, corp_code, is_main FROM theme_companies ORDER BY 1, 2")
        ).all()
    return [tuple(row) for row in rows]


def test_keeps_kospi200_members_known_as_companies(engine, companies):
    members = {
        "100": [
            ThemeMember("005930", "삼성전자"),
            ThemeMember("000660", "SK하이닉스"),
            ThemeMember("247540", "에코프로비엠"),
            ThemeMember("123456", "비상장"),
        ]
    }

    counts = sync(
        engine, [Theme("100", "HBM", "")], members, kospi200=KOSPI200 | {"123456"}
    )

    assert counts == (2, 0, 2)
    assert memberships(engine) == [("100", "00126380", False), ("100", "00164779", False)]


@pytest.mark.parametrize(
    "main_stocks",
    ["SK하이닉스", "000660", "삼성전자, SK하이닉스", "삼성전자,000660", "SK 하이닉스"],
)
def test_main_stocks_match_by_code_or_normalized_name(engine, companies, main_stocks):
    members = {"100": [ThemeMember("005930", "삼성전자"), ThemeMember("000660", "SK하이닉스")]}

    kept, main, skipped = sync(engine, [Theme("100", "HBM", main_stocks)], members)

    rows = dict(((corp_code, is_main) for _, corp_code, is_main in memberships(engine)))
    assert rows["00164779"] is True
    assert rows["00126380"] is ("삼성전자" in main_stocks)
    assert main == sum(rows.values())


def test_themes_without_kospi200_members_are_still_stored(engine, companies):
    sync(engine, [Theme("100", "HBM", ""), Theme("200", "2차전지", "")], {"100": [], "200": []})

    with engine.connect() as conn:
        names = conn.execute(sa.text("SELECT name FROM themes ORDER BY theme_code")).scalars().all()
    assert names == ["HBM", "2차전지"]


def test_a_second_sync_replaces_everything(engine, companies):
    sync(engine, [Theme("100", "HBM", "")], {"100": [ThemeMember("005930", "삼성전자")]})

    sync(engine, [Theme("200", "2차전지", "")], {"200": [ThemeMember("000660", "SK하이닉스")]})

    with engine.connect() as conn:
        codes = conn.execute(sa.text("SELECT theme_code FROM themes")).scalars().all()
    assert codes == ["200"]
    assert memberships(engine) == [("200", "00164779", False)]


@pytest.mark.parametrize(("themes", "kospi200"), [([], KOSPI200), ([Theme("100", "HBM", "")], set())])
def test_empty_kiwoom_data_raises_and_keeps_the_old_tables(engine, companies, themes, kospi200):
    sync(engine, [Theme("100", "HBM", "")], {"100": [ThemeMember("005930", "삼성전자")]})

    with pytest.raises(ValueError):
        sync(engine, themes, {}, kospi200=kospi200)

    assert memberships(engine) == [("100", "00126380", False)]


def test_a_shared_stock_code_resolves_to_the_most_recently_synced_company(engine, companies):
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO companies (corp_code, stock_code, corp_name, synced_at)"
                " VALUES ('00999999', '005930', '옛삼성', now() - interval '1 day')"
            )
        )

    sync(engine, [Theme("100", "HBM", "")], {"100": [ThemeMember("005930", "삼성전자")]})

    assert memberships(engine) == [("100", "00126380", False)]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTEST services/news-graph-builder/tests/theme/test_service.py -q`
Expected: FAIL — `ImportError: cannot import name 'sync_themes'`.

- [ ] **Step 3: Write the repository and service**

`theme/repository.py`:

```python
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from news_graph_builder.database import companies, theme_companies
from news_graph_builder.database import themes as themes_table
from news_graph_builder.theme.dto import Theme


def find_corp_codes_by_stock_code(conn: sa.Connection) -> dict[str, str]:
    query = sa.select(companies.c.stock_code, companies.c.corp_code).order_by(
        companies.c.synced_at, companies.c.corp_code
    )
    return {row.stock_code: row.corp_code for row in conn.execute(query)}


def replace_themes(
    conn: sa.Connection,
    *,
    themes: Sequence[Theme],
    memberships: Sequence[tuple[str, str, bool]],
) -> None:
    conn.execute(sa.delete(theme_companies))
    conn.execute(sa.delete(themes_table))
    if themes:
        conn.execute(
            insert(themes_table), [{"theme_code": theme.code, "name": theme.name} for theme in themes]
        )
    if memberships:
        conn.execute(
            insert(theme_companies),
            [
                {"theme_code": theme_code, "corp_code": corp_code, "is_main": is_main}
                for theme_code, corp_code, is_main in memberships
            ],
        )
```

`theme/service.py`:

```python
from collections.abc import Sequence

import sqlalchemy as sa

from news_graph_builder.common import normalize
from news_graph_builder.theme.dto import Theme, ThemeMember
from news_graph_builder.theme.repository import find_corp_codes_by_stock_code, replace_themes


def sync_themes(
    conn: sa.Connection,
    *,
    themes: Sequence[Theme],
    kospi200_codes: set[str],
    members: dict[str, list[ThemeMember]],
) -> tuple[int, int, int]:
    if not themes:
        raise ValueError("Kiwoom returned no themes")
    if not kospi200_codes:
        raise ValueError("Kiwoom returned no KOSPI 200 codes")

    corp_codes = find_corp_codes_by_stock_code(conn)
    unique_themes = list({theme.code: theme for theme in themes}.values())
    is_main_by_key: dict[tuple[str, str], bool] = {}
    skipped = 0
    for theme in unique_themes:
        main_parts = {part.strip() for part in theme.main_stocks.split(",") if part.strip()}
        main_names = {normalize(part) for part in main_parts}
        for member in members.get(theme.code, []):
            corp_code = corp_codes.get(member.stock_code)
            if member.stock_code not in kospi200_codes or corp_code is None:
                skipped += 1
                continue
            is_main = member.stock_code in main_parts or normalize(member.stock_name) in main_names
            key = (theme.code, corp_code)
            is_main_by_key[key] = is_main_by_key.get(key, False) or is_main

    memberships = [
        (theme_code, corp_code, is_main)
        for (theme_code, corp_code), is_main in is_main_by_key.items()
    ]
    replace_themes(conn, themes=unique_themes, memberships=memberships)
    main = sum(is_main for _, _, is_main in memberships)
    return len(memberships), main, skipped
```

`theme/__init__.py`:

```python
from news_graph_builder.theme.dto import Theme, ThemeMember
from news_graph_builder.theme.kiwoom import fetch_kospi200_codes, fetch_theme_members, fetch_themes
from news_graph_builder.theme.service import sync_themes

__all__ = [
    "Theme",
    "ThemeMember",
    "fetch_kospi200_codes",
    "fetch_theme_members",
    "fetch_themes",
    "sync_themes",
]
```

In `tach.toml`, add `"sync_themes"` to the `news_graph_builder.theme` interface `expose` list.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTEST services/news-graph-builder -q` and `uv run tach check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/news-graph-builder tach.toml
git commit -m "feat(graph-builder): keep KOSPI 200 theme members and flag main stocks"
```

---

### Task 5: Run the theme sync from `main()`; docs

**Files:**
- Modify: `services/news-graph-builder/src/news_graph_builder/__main__.py`
- Modify: `services/news-graph-builder/tests/test_main.py`
- Modify: `tach.toml` (root depends on `theme`)
- Modify: `AGENTS.md` (datastore bullet)

**Interfaces:**
- Consumes: `kiwoom.fetch_token` (Task 2), `company.fetch_kospi(client, *, token)` (Task 2), `theme.fetch_themes`, `theme.fetch_kospi200_codes`, `theme.fetch_theme_members`, `theme.sync_themes` (Tasks 3–4).
- Produces: `main()` issues one token; runs company sync, then theme sync; a theme failure logs `theme sync failed`, keeps the old tables, continues to graph building and makes the run exit 1; a token failure fails both syncs.

- [ ] **Step 1: Write the failing tests**

In `tests/test_main.py`:

1. Add imports: `from news_graph_builder.theme import Theme, ThemeMember`.
2. Rename the fixture `companies_api` to `market_data` everywhere in the file, and replace its body so it fakes every Kiwoom and DART call and counts token requests:

```python
@pytest.fixture
def market_data(monkeypatch):
    tokens = []

    def fetch_token(client, **kwargs):
        tokens.append("tok")
        return "tok"

    monkeypatch.setattr(entry, "fetch_token", fetch_token)
    monkeypatch.setattr(entry, "fetch_kospi", lambda client, **kwargs: [("005930", "삼성전자")])
    monkeypatch.setattr(entry, "fetch_corp_codes", lambda **kwargs: [SAMSUNG])
    monkeypatch.setattr(
        entry, "fetch_themes", lambda client, **kwargs: [Theme("100", "반도체", "삼성전자")]
    )
    monkeypatch.setattr(entry, "fetch_kospi200_codes", lambda client, **kwargs: {"005930"})
    monkeypatch.setattr(
        entry,
        "fetch_theme_members",
        lambda client, **kwargs: {"100": [ThemeMember("005930", "삼성전자")]},
    )
    return tokens
```

3. In `test_a_failed_first_sync_exits_before_any_llm_call`, add `market_data` to the parameters and delete its `monkeypatch.setattr(entry, "fetch_token", …)` and `monkeypatch.setattr(entry, "fetch_corp_codes", …)` lines (the fixture provides them); keep the `unreachable` override of `fetch_kospi`.
4. Append:

```python
def theme_rows(engine):
    with engine.connect() as conn:
        return conn.execute(
            sa.text("SELECT theme_code, corp_code, is_main FROM theme_companies")
        ).all()


def test_syncs_themes_with_one_token(env, engine, two_clusters, market_data, llm):
    assert run() == 0

    assert [tuple(row) for row in theme_rows(engine)] == [("100", "00126380", True)]
    assert market_data == ["tok"]


def test_a_failed_theme_sync_keeps_old_themes_and_still_builds(
    env, engine, article, cluster, market_data, llm, monkeypatch
):
    assert run() == 0
    with engine.begin() as conn:
        cluster(conn, [article(conn)])

    def unreachable(client, **kwargs):
        raise RuntimeError("Kiwoom theme API down")

    monkeypatch.setattr(entry, "fetch_themes", unreachable)

    assert run() == 1
    assert len(llm) == 1
    assert [tuple(row) for row in theme_rows(engine)] == [("100", "00126380", True)]


def test_a_failed_token_fails_both_syncs(env, engine, two_clusters, market_data, llm, monkeypatch):
    def refused(client, **kwargs):
        raise RuntimeError("Kiwoom token refused")

    monkeypatch.setattr(entry, "fetch_token", refused)

    assert run() == 1
    assert llm == []
    assert theme_rows(engine) == []
```

(`test_a_failed_token_fails_both_syncs` runs on an empty `companies` table, so the run exits 1 before any LLM call, like the existing first-sync test.)

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTEST services/news-graph-builder/tests/test_main.py -q`
Expected: FAIL — `AttributeError: … has no attribute 'fetch_themes'` (the fixture cannot patch names `__main__` doesn't import yet).

- [ ] **Step 3: Restructure the sync part of `main()`**

Imports in `__main__.py`:

```python
from news_graph_builder.kiwoom import fetch_token
from news_graph_builder.theme import (
    fetch_kospi200_codes,
    fetch_theme_members,
    fetch_themes,
    sync_themes,
)
```

Replace everything from `        try:` (the company sync, currently beginning `token = fetch_token(client)`) up to, but not including, `        with engine.connect() as conn:\n            clusters = find_stale_clusters(conn)` with:

```python
        token = None
        try:
            token = fetch_token(client)
        except Exception:
            logger.exception("Kiwoom token request failed")
            sync_failed = True

        if token is not None:
            try:
                kospi = fetch_kospi(client, token=token)
                dart = fetch_corp_codes()
                with engine.begin() as conn:
                    joined, merged = sync_companies(conn, kospi, dart)
                logger.info(
                    "synced companies: kiwoom=%d dart=%d joined=%d merged=%d",
                    len(kospi),
                    len(dart),
                    joined,
                    merged,
                )
            except Exception:
                logger.exception("company sync failed")
                sync_failed = True

        if sync_failed:
            with engine.connect() as conn:
                if not has_companies(conn):
                    # Without companies every company would become a plain entity for good.
                    sys.exit(1)

        if token is not None:
            try:
                themes = fetch_themes(client, token=token)
                kospi200_codes = fetch_kospi200_codes(client, token=token)
                theme_codes = [theme.code for theme in themes]
                members = fetch_theme_members(client, token=token, theme_codes=theme_codes)
                with engine.begin() as conn:
                    kept, main_stocks, skipped = sync_themes(
                        conn, themes=themes, kospi200_codes=kospi200_codes, members=members
                    )
                logger.info(
                    "synced themes: themes=%d kospi200=%d members=%d main=%d skipped=%d",
                    len(themes),
                    len(kospi200_codes),
                    kept,
                    main_stocks,
                    skipped,
                )
            except Exception:
                logger.exception("theme sync failed")
                sync_failed = True

```

`tach.toml`: add `"news_graph_builder.theme"` to the root `news_graph_builder` module's `depends_on`.

`AGENTS.md`: in the "Services communicate only through datastores" bullet, change `writes \`cluster_summaries\` plus the knowledge graph (…)` to `writes \`cluster_summaries\`, the knowledge graph (…) and Kiwoom theme data (\`themes\`, \`theme_companies\`)`, keeping the rest of the bullet unchanged.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTEST -q` (whole repo), `uv run ruff check .`, `uv run ruff format --check .`, `uv run tach check`, `uv run tach check-external -e packages/market-analyzer,services`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add services/news-graph-builder tach.toml AGENTS.md
git commit -m "feat(graph-builder): sync Kiwoom themes on every run"
```
