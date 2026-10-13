# market-streamer and market-http Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stream live KOSPI 200 prices from Kiwoom `0B` into Valkey, serve them and QuestDB history through a public market-http, run market-collector every minute, and move portfolio-builder off QuestDB.

**Architecture:** market-streamer is an asyncio worker that holds one `kwcli` WebSocket and writes `SET` + `PUBLISH price:{code}` to Valkey. market-http is a FastAPI service: `/prices` and `/prices/stream` read Valkey (filling misses from QuestDB with `SET NX`), `/bars/{code}` reads QuestDB with timestamp cursors. market-collector re-fetches its checkpoint bar so partial minutes are upserted. portfolio-builder calls `/bars/{code}` with `urllib`.

**Tech Stack:** Python 3.13, uv workspace, FastAPI 0.142 (`fastapi.sse`), redis-py asyncio, botocore (ElastiCache IAM), kwcli (`kiwoom`), official `questdb` client, systemd timers, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-10-13-market-streamer-http-design.md`

## Global Constraints

- Every environment variable uses its service prefix (`MARKET_STREAMER_`, `MARKET_HTTP_`, …). No shared `VALKEY_*` variables anywhere, including Compose.
- Nothing is added to `ktb-core`; its `dependencies = []` stays empty. The Valkey IAM provider is copied into each service's `valkey.py`.
- Valkey key and channel: `price:{code}`; payload JSON `{"code","price","change","rate","volume","ts"}`; no TTL.
- `MARKET_COLLECTOR_REQUEST_INTERVAL` code default stays `1.3`; production Compose default becomes `0.25`.
- Never write code comments or docstrings (`AGENTS.md`). Ruff line length 100; `ruff format` must pass.
- After any dependency change: `uv lock`, then export with exactly `uv export --package <svc> --no-dev --no-emit-workspace --format requirements-txt -o docker/requirements/<svc>.txt` and `uv export --all-packages --no-dev --group migrations --no-emit-workspace --format requirements-txt -o docker/requirements/app.txt`.
- Commit titles use `feat`/`fix`/`refactor`/`chore`/`docs`; end each commit message with the session's `Co-Authored-By` trailer.
- DB-backed tests skip when their variable is unset: `KTB_TEST_QUESTDB_CONF`, and the new `KTB_TEST_VALKEY_URL` (e.g. `redis://localhost:6379/0`). Valkey tests call `FLUSHDB`, so never point it at a shared instance.

## Review Focus

1. A `0B` entry with a blank or garbled field (Kiwoom pads with spaces) must be skipped and logged while the rest of the frame is published — Task 3 `test_frame_prices_skips_malformed_entries`.
2. Kiwoom rejecting `REG` must back off 1 → 2 → 4 s instead of reconnecting in a tight loop — Task 4 `test_rejected_registration_backs_off_exponentially`.
3. `/prices?codes=` with a malformed code must return 422, not reach Valkey — Task 7 `test_prices_rejects_malformed_codes`.
4. A code unknown to both Valkey and QuestDB must be omitted with 200, not 500 — Task 7 `test_prices_omits_codes_without_any_data`.
5. portfolio-builder receiving an empty `/bars` page must still raise its `NoMarketData` path (`as_of is None`) — Task 9 `test_http_market_returns_empty_bars_for_unknown_symbol`.

---

### Task 1: market-collector upserts the checkpoint bar and runs every minute

**Files:**
- Modify: `services/market-collector/src/market_collector/reconcile.py` (final filter)
- Modify: `services/market-collector/src/market_collector/__main__.py` (`archive_ohlcv`)
- Modify: `infrastructure/systemd/ktb-market-collector.timer`
- Test: `services/market-collector/tests/test_reconcile.py`, `services/market-collector/tests/test_main.py`

**Interfaces:**
- Consumes: existing `reconcile_candles(client, store, symbol, timeframe, base_dt, latest)`, `FakeClient`, `FakeSink`, `_minute_row`, `_daily_row`, `ANCHOR`, `BASE_DT` in `test_reconcile.py`.
- Produces: `market_collector.__main__.CLOSE_RUN = time(15, 35)`; `archive_ohlcv` collects `("1m",)` before `CLOSE_RUN` KST and `("1m", "1d")` from it.

- [ ] **Step 1: Write the failing reconcile tests**

Append to `services/market-collector/tests/test_reconcile.py`:

```python
def _minute_at(at: datetime, close: int, volume: int) -> dict[str, str]:
    return {
        "cntr_tm": at.strftime("%Y%m%d%H%M%S"),
        "cur_prc": f"+{close}",
        "open_pric": "+100",
        "high_pric": f"+{max(close, 100)}",
        "low_pric": "+100",
        "trde_qty": str(volume),
    }


def _written(sink: FakeSink) -> list[tuple[datetime, float, int]]:
    return [(row[3], row[2]["close"], row[2]["volume"]) for row in sink.rows]


def test_forming_minute_is_rewritten_by_the_next_run():
    nine = datetime(2026, 9, 22, 9, 0, tzinfo=KST)
    first = FakeSink()
    reconcile_candles(
        FakeClient([Page([_minute_at(nine, 101, 10)], None, False)]),
        Store(first),
        "005930",
        "1m",
        BASE_DT,
        None,
    )
    second = FakeSink()

    written = reconcile_candles(
        FakeClient(
            [
                Page(
                    [_minute_at(nine + timedelta(minutes=1), 103, 5), _minute_at(nine, 102, 30)],
                    None,
                    False,
                )
            ]
        ),
        Store(second),
        "005930",
        "1m",
        BASE_DT,
        nine.astimezone(UTC),
    )

    assert _written(first) == [(nine.astimezone(UTC), 101.0, 10)]
    assert written == 2
    assert _written(second) == [
        (nine.astimezone(UTC), 102.0, 30),
        ((nine + timedelta(minutes=1)).astimezone(UTC), 103.0, 5),
    ]


def test_close_run_rewrites_the_last_minute_and_writes_today_daily_bar():
    last = datetime(2026, 9, 22, 15, 30, tzinfo=KST)
    minute = FakeSink()
    daily = FakeSink()

    reconcile_candles(
        FakeClient([Page([_minute_at(last, 105, 70)], None, False)]),
        Store(minute),
        "005930",
        "1m",
        BASE_DT,
        last.astimezone(UTC),
    )
    written = reconcile_candles(
        FakeClient([Page([_daily_row("20260922", 105), _daily_row("20260919", 99)], None, False)]),
        Store(daily),
        "005930",
        "1d",
        BASE_DT,
        datetime(2026, 9, 19, tzinfo=UTC),
    )

    assert _written(minute) == [(last.astimezone(UTC), 105.0, 70)]
    assert written == 2
    assert [row[3] for row in daily.rows] == [
        datetime(2026, 9, 19, tzinfo=UTC),
        datetime(2026, 9, 22, tzinfo=UTC),
    ]
```

The spec's third case (a first run caps at `LIMITS` and writes no bar twice) is already pinned by `test_initial_collection_caps_unique_bars_and_keeps_latest_in_chronological_order`; leave it unchanged.

- [ ] **Step 2: Update the two existing tests that assert strictly-newer filtering**

In `test_incremental_collection_exceeds_initial_limit_and_reaches_old_checkpoint`, replace the three assertions after `written = ...` with:

```python
    assert written == limit + 2
    assert [row[3] for row in sink.rows] == [
        anchor - offset * step for offset in reversed(range(limit + 2))
    ]
    assert all(row[3] >= latest for row in sink.rows)
```

Rename `test_reconcile_with_checkpoint_filters_strictly_newer_and_stops_at_boundary_page` to `test_reconcile_with_checkpoint_rewrites_it_and_stops_at_boundary_page` and replace its assertions with:

```python
    assert written == 3
    assert [row[3] for row in sink.rows] == [
        datetime(2026, 9, 22, 6, 27, tzinfo=UTC),
        datetime(2026, 9, 22, 6, 28, tzinfo=UTC),
        datetime(2026, 9, 22, 6, 29, tzinfo=UTC),
    ]
    assert len(client.minute_calls) == 2
```

- [ ] **Step 3: Run the reconcile tests to verify they fail**

Run: `uv run pytest services/market-collector/tests/test_reconcile.py -q`
Expected: FAIL in the two new tests and the two updated tests (checkpoint bar not written).

- [ ] **Step 4: Change the filter in `reconcile.py`**

Replace `if latest is None or bar.ts > latest` with `if latest is None or bar.ts >= latest`.

- [ ] **Step 5: Run the reconcile tests to verify they pass**

Run: `uv run pytest services/market-collector/tests/test_reconcile.py -q`
Expected: PASS.

- [ ] **Step 6: Write the failing timeframe tests in `test_main.py`**

`NOW` is 12:00 KST, so midday runs now collect 1m only. In `test_archive_run_reconciles_each_symbol_after_loading_symbols` change `assert cli.archive_ohlcv(settings, NOW) == 4` to `== 2`, change `== ["1d", "1m"]` to `== ["1m"]`, and replace the `events_660` / `events_930` expectations with:

```python
    assert events_660 == [("reconcile_candles", "000660", "1m", "20260928", None)]
    assert events_930 == [
        ("reconcile_candles", "005930", "1m", "20260928", checkpoints[("005930", "1m")]),
    ]
```

In `test_archive_run_shards_stably_and_skips_removed_symbols` replace the `by_client` expectation with:

```python
    assert by_client == {
        "k1": [("000660", "1m"), ("035420", "1m")],
        "k2": [("005930", "1m")],
    }
```

Append:

```python
@pytest.mark.parametrize(
    ("local", "timeframes"),
    [
        (datetime(2026, 9, 28, 9, 0, 5, tzinfo=KST), ["1m"]),
        (datetime(2026, 9, 28, 15, 30, 5, tzinfo=KST), ["1m"]),
        (datetime(2026, 9, 28, 15, 35, 5, tzinfo=KST), ["1m", "1d"]),
    ],
)
def test_daily_bars_are_collected_only_from_the_close_run(monkeypatch, local, timeframes):
    settings = _settings(monkeypatch)
    seen = []

    class Store:
        def latest_bar_timestamps(self, target_symbols):
            return {}

    monkeypatch.setattr(cli, "load_symbols", lambda dsn, index_name: ["005930"])
    monkeypatch.setattr(cli.questdb, "connect", lambda conf: nullcontext(object()))
    monkeypatch.setattr(cli, "Store", lambda db: Store())
    monkeypatch.setattr(cli, "build_client", lambda account, mode: object())
    monkeypatch.setattr(cli, "ChartClient", lambda client, interval: client)
    monkeypatch.setattr(
        cli,
        "reconcile_candles",
        lambda client, store, symbol, timeframe, base_dt, latest: seen.append(timeframe) or 0,
    )

    cli.archive_ohlcv(settings, local.astimezone(UTC))

    assert seen == timeframes
```

Add `from market_collector.kiwoom.parse import KST` to the imports.

- [ ] **Step 7: Run to verify failure**

Run: `uv run pytest services/market-collector/tests/test_main.py -q`
Expected: FAIL (1d still collected at midday).

- [ ] **Step 8: Implement the timeframe choice in `__main__.py`**

Change the import to `from datetime import UTC, datetime, time`, add `CLOSE_RUN = time(15, 35)` below `log = get_logger(__name__)`, and in `archive_ohlcv` replace the `base_dt = ...` line with:

```python
    local = now.astimezone(KST)
    base_dt = local.strftime("%Y%m%d")
    timeframes = ("1m", "1d") if local.time() >= CLOSE_RUN else ("1m",)
```

and the inner loop header `for timeframe in ("1d", "1m"):` with `for timeframe in timeframes:`.

- [ ] **Step 9: Run the collector tests**

Run: `uv run pytest services/market-collector -q`
Expected: PASS.

- [ ] **Step 10: Change the timer to every minute**

Replace the whole of `infrastructure/systemd/ktb-market-collector.timer` with:

```ini
[Unit]
Description=Run KTB market OHLCV collection every minute during weekday market sessions

[Timer]
OnCalendar=Mon..Fri *-*-* 09..14:*:05 Asia/Seoul
OnCalendar=Mon..Fri *-*-* 15:00..30:05 Asia/Seoul
OnCalendar=Mon..Fri *-*-* 15:35:05 Asia/Seoul
AccuracySec=1s
Persistent=false
Unit=ktb-market-collector.service

[Install]
WantedBy=timers.target
```

Run: `systemd-analyze calendar 'Mon..Fri *-*-* 15:00..30:05 Asia/Seoul' --iterations=3`
Expected: three consecutive minutes at `:05`.

- [ ] **Step 11: Lint and commit**

```bash
uv run ruff check services/market-collector && uv run ruff format --check services/market-collector
git add services/market-collector infrastructure/systemd/ktb-market-collector.timer
git commit -m "feat: collect minute bars every minute and upsert the checkpoint bar"
```

---

### Task 2: market-streamer package, settings, and Valkey connection

**Files:**
- Create: `services/market-streamer/pyproject.toml`
- Create: `services/market-streamer/src/market_streamer/__init__.py` (empty)
- Create: `services/market-streamer/src/market_streamer/settings.py`
- Create: `services/market-streamer/src/market_streamer/valkey.py`
- Create: `services/market-streamer/tests/conftest.py`
- Test: `services/market-streamer/tests/test_settings.py`, `services/market-streamer/tests/test_valkey.py`
- Modify: `tach.toml`, `uv.lock`, `.github/workflows/ci-dev.yaml` (Valkey service + `KTB_TEST_VALKEY_URL`)

**Interfaces:**
- Produces: `market_streamer.settings.Settings` (fields below); `market_streamer.valkey.ElastiCacheIAMProvider(user: str, cache_name: str, region: str)` with `get_credentials() -> tuple[str, str]`; `market_streamer.valkey.connect_valkey(settings: Settings) -> redis.asyncio.Redis` (`decode_responses=True`); pytest fixture `valkey_url` in `tests/conftest.py`.

- [ ] **Step 1: Create the package**

`services/market-streamer/pyproject.toml`:

```toml
[project]
name = "market-streamer"
version = "0.1.0"
description = "Kiwoom realtime KOSPI 200 prices into Valkey"
requires-python = ">=3.13,<3.15"
dependencies = [
    "ktb-core",
    "botocore>=1.40",
    "kwcli",
    "psycopg[binary]>=3.3.6",
    "pydantic>=2.9",
    "pydantic-settings>=2.7",
    "redis>=6.4",
    "sqlalchemy>=2.0.54",
]

[project.scripts]
market-streamer = "market_streamer.__main__:main"

[tool.uv.sources]
ktb-core = { workspace = true }

[tool.deptry.per_rule_ignores]
DEP002 = ["psycopg"]

[build-system]
requires = ["uv_build>=0.12,<0.13"]
build-backend = "uv_build"
```

Create the empty `src/market_streamer/__init__.py`. In `tach.toml` add `"services/market-streamer/src",` to `source_roots` and append:

```toml
[[modules]]
path = "market_streamer"
depends_on = ["ktb_core"]
```

Run: `uv lock && uv sync --all-packages --group migrations`
Expected: lock updated, `redis` and `botocore` installed.

- [ ] **Step 2: Write the failing settings and Valkey tests**

`services/market-streamer/tests/conftest.py`:

```python
import os

import pytest
import redis


@pytest.fixture
def valkey_url():
    url = os.environ.get("KTB_TEST_VALKEY_URL")
    if not url:
        pytest.skip("KTB_TEST_VALKEY_URL is not set")
    client = redis.Redis.from_url(url)
    client.flushdb()
    client.close()
    return url
```

`services/market-streamer/tests/test_settings.py`:

```python
from datetime import time

import pytest
from market_streamer.settings import Settings
from pydantic import ValidationError

REQUIRED = {
    "MARKET_STREAMER_POSTGRES_DSN": "postgresql+psycopg://ktb:FAKE@localhost:5432/ktb",
    "MARKET_STREAMER_KIWOOM_APP_KEY": "app",
    "MARKET_STREAMER_KIWOOM_SECRET_KEY": "secret",
    "MARKET_STREAMER_VALKEY_HOST": "localhost",
}


def _populate(monkeypatch, **extra):
    for name, value in {**REQUIRED, **extra}.items():
        monkeypatch.setenv(name, value)


def test_defaults(monkeypatch):
    _populate(monkeypatch)

    settings = Settings()

    assert settings.kiwoom_mode == "real"
    assert settings.index_name == "KOSPI200"
    assert settings.stop_at == time(15, 40)
    assert settings.valkey_port == 6379
    assert settings.valkey_tls is False
    assert settings.valkey_auth_mode == "none"
    assert settings.aws_region == "ap-northeast-2"


def test_stop_at_is_parsed_from_hh_mm(monkeypatch):
    _populate(monkeypatch, MARKET_STREAMER_STOP_AT="15:45")

    assert Settings().stop_at == time(15, 45)


def test_iam_needs_username_and_cache_name(monkeypatch):
    _populate(monkeypatch, MARKET_STREAMER_VALKEY_AUTH_MODE="iam")

    with pytest.raises(ValidationError, match="valkey_username"):
        Settings()
```

`services/market-streamer/tests/test_valkey.py`:

```python
from urllib.parse import parse_qs

from market_streamer.settings import Settings
from market_streamer.valkey import ElastiCacheIAMProvider, connect_valkey


def test_iam_token_is_a_presigned_connect_url_for_the_serverless_cache(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.delenv("AWS_SESSION_TOKEN", raising=False)
    monkeypatch.delenv("AWS_PROFILE", raising=False)

    user, token = ElastiCacheIAMProvider("backend", "my-cache", "ap-northeast-2").get_credentials()

    assert user == "backend"
    assert token.startswith("my-cache/?")
    query = parse_qs(token.split("?", 1)[1])
    assert query["Action"] == ["connect"]
    assert query["User"] == ["backend"]
    assert query["ResourceType"] == ["ServerlessCache"]
    assert query["X-Amz-Expires"] == ["900"]
    assert "X-Amz-Signature" in query


def test_none_auth_mode_connects_without_a_credential_provider(monkeypatch):
    monkeypatch.setenv("MARKET_STREAMER_POSTGRES_DSN", "postgresql+psycopg://x@localhost/x")
    monkeypatch.setenv("MARKET_STREAMER_KIWOOM_APP_KEY", "app")
    monkeypatch.setenv("MARKET_STREAMER_KIWOOM_SECRET_KEY", "secret")
    monkeypatch.setenv("MARKET_STREAMER_VALKEY_HOST", "valkey")

    kwargs = connect_valkey(Settings()).connection_pool.connection_kwargs

    assert kwargs["host"] == "valkey"
    assert kwargs.get("credential_provider") is None
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest services/market-streamer -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'market_streamer.settings'`.

- [ ] **Step 4: Implement settings and Valkey connection**

`services/market-streamer/src/market_streamer/settings.py`:

```python
from datetime import time
from typing import Literal, Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MARKET_STREAMER_", extra="ignore", hide_input_in_errors=True
    )

    postgres_dsn: str
    kiwoom_app_key: str
    kiwoom_secret_key: str
    kiwoom_mode: Literal["real", "demo"] = "real"
    index_name: str = "KOSPI200"
    stop_at: time = time(15, 40)
    valkey_host: str
    valkey_port: int = 6379
    valkey_username: str | None = None
    valkey_cache_name: str | None = None
    valkey_tls: bool = False
    valkey_auth_mode: Literal["none", "iam"] = "none"
    aws_region: str = "ap-northeast-2"
    log_level: str = "INFO"

    @model_validator(mode="after")
    def _iam_needs_identity(self) -> Self:
        if self.valkey_auth_mode == "iam" and not (
            self.valkey_username and self.valkey_cache_name
        ):
            raise ValueError("iam auth needs valkey_username and valkey_cache_name")
        return self
```

`services/market-streamer/src/market_streamer/valkey.py`:

```python
from urllib.parse import urlencode

import botocore.session
from botocore.model import ServiceId
from botocore.signers import RequestSigner
from redis.asyncio import Redis
from redis.credentials import CredentialProvider

from market_streamer.settings import Settings


class ElastiCacheIAMProvider(CredentialProvider):
    def __init__(self, user: str, cache_name: str, region: str) -> None:
        self._user = user
        self._cache_name = cache_name
        self._region = region
        session = botocore.session.get_session()
        self._signer = RequestSigner(
            ServiceId("elasticache"),
            region,
            "elasticache",
            "v4",
            session.get_credentials(),
            session.get_component("event_emitter"),
        )

    def get_credentials(self) -> tuple[str, str]:
        query = urlencode(
            {"Action": "connect", "User": self._user, "ResourceType": "ServerlessCache"}
        )
        url = self._signer.generate_presigned_url(
            {
                "method": "GET",
                "url": f"https://{self._cache_name}/?{query}",
                "body": {},
                "headers": {},
                "context": {},
            },
            operation_name="connect",
            expires_in=900,
            region_name=self._region,
        )
        return self._user, url.removeprefix("https://")

    async def get_credentials_async(self) -> tuple[str, str]:
        return self.get_credentials()


def connect_valkey(settings: Settings) -> Redis:
    provider = None
    if settings.valkey_auth_mode == "iam" and settings.valkey_username and settings.valkey_cache_name:
        provider = ElastiCacheIAMProvider(
            settings.valkey_username, settings.valkey_cache_name, settings.aws_region
        )
    return Redis(
        host=settings.valkey_host,
        port=settings.valkey_port,
        ssl=settings.valkey_tls,
        credential_provider=provider,
        decode_responses=True,
    )
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest services/market-streamer -q`
Expected: PASS.

- [ ] **Step 6: Add Valkey to CI**

In `.github/workflows/ci-dev.yaml` under `lint-and-test.services`, after `questdb:` add:

```yaml
      valkey:
        image: valkey/valkey:8.1.4
        ports:
          - "6379:6379"
        options: >-
          --health-cmd "valkey-cli ping"
          --health-interval 5s
          --health-timeout 3s
          --health-retries 12
```

and under `lint-and-test.env` add `KTB_TEST_VALKEY_URL: redis://localhost:6379/0`. Confirm the tag exists with `docker pull valkey/valkey:8.1.4`; if it does not, use the newest `8.1.x` tag from Docker Hub in both CI and Compose (Task 10).

- [ ] **Step 7: Lint, tach, commit**

```bash
uv run ruff check services/market-streamer && uv run ruff format --check services/market-streamer && uv run tach check
git add services/market-streamer tach.toml uv.lock .github/workflows/ci-dev.yaml
git commit -m "feat: add market-streamer settings and Valkey connection"
```

---

### Task 3: market-streamer tick parsing and Valkey publishing

**Files:**
- Create: `services/market-streamer/src/market_streamer/stream.py`
- Test: `services/market-streamer/tests/test_stream.py`

**Interfaces:**
- Consumes: `valkey_url` fixture (Task 2).
- Produces in `market_streamer.stream`: `KST`; `parse_tick(entry: dict[str, Any], today: date) -> dict[str, object]`; `frame_prices(message: dict[str, Any], today: date) -> list[dict[str, object]]`; `async publish(valkey: Redis, prices: Iterable[dict[str, object]]) -> None`.

- [ ] **Step 1: Write the failing tests**

`services/market-streamer/tests/test_stream.py`:

```python
import asyncio
import json
from datetime import date

from market_streamer.stream import frame_prices, parse_tick, publish
from redis.asyncio import Redis

TODAY = date(2026, 10, 13)
TICK = {
    "type": "0B",
    "name": "주식체결",
    "item": "005930",
    "values": {"10": " -82000", "11": "-500", "12": "-0.61", "13": "+12345678", "20": "090123"},
}
PRICE = {
    "code": "005930",
    "price": 82000,
    "change": -500,
    "rate": -0.61,
    "volume": 12345678,
    "ts": "2026-10-13T09:01:23+09:00",
}


def test_parse_tick_strips_padding_and_direction_signs():
    assert parse_tick(TICK, TODAY) == PRICE


def test_frame_prices_skips_malformed_entries():
    message = {
        "trnm": "REAL",
        "data": [
            TICK,
            {"type": "0B", "item": "000660", "values": {"10": "   ", "11": "0"}},
            {"type": "0D", "item": "005930", "values": {}},
        ],
    }

    assert frame_prices(message, TODAY) == [PRICE]


def test_publish_sets_and_publishes_each_price(valkey_url):
    async def scenario():
        valkey = Redis.from_url(valkey_url, decode_responses=True)
        pubsub = valkey.pubsub()
        await pubsub.subscribe("price:005930")
        await pubsub.get_message(timeout=1)
        await publish(valkey, [PRICE])
        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=5)
        stored = await valkey.get("price:005930")
        await pubsub.aclose()
        await valkey.aclose()
        return message, stored

    message, stored = asyncio.run(scenario())

    assert json.loads(stored) == PRICE
    assert message is not None
    assert json.loads(message["data"]) == PRICE
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest services/market-streamer/tests/test_stream.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'market_streamer.stream'`.

- [ ] **Step 3: Implement parsing and publishing**

`services/market-streamer/src/market_streamer/stream.py`:

```python
import json
from collections.abc import Iterable
from datetime import date, datetime, timedelta, timezone
from typing import Any

from ktb_core.logging import get_logger
from redis.asyncio import Redis

KST = timezone(timedelta(hours=9), "KST")

log = get_logger(__name__)


def parse_tick(entry: dict[str, Any], today: date) -> dict[str, object]:
    values = entry["values"]
    clock = datetime.strptime(values["20"].strip(), "%H%M%S").time()
    return {
        "code": entry["item"],
        "price": abs(int(values["10"].strip())),
        "change": int(values["11"].strip()),
        "rate": float(values["12"].strip()),
        "volume": abs(int(values["13"].strip())),
        "ts": datetime.combine(today, clock, KST).isoformat(),
    }


def frame_prices(message: dict[str, Any], today: date) -> list[dict[str, object]]:
    prices = []
    for entry in message.get("data", []):
        if entry.get("type") != "0B":
            continue
        try:
            prices.append(parse_tick(entry, today))
        except (KeyError, TypeError, ValueError) as error:
            log.warning("tick_skipped", item=entry.get("item"), error=str(error))
    return prices


async def publish(valkey: Redis, prices: Iterable[dict[str, object]]) -> None:
    async with valkey.pipeline(transaction=False) as pipe:
        for price in prices:
            key = f"price:{price['code']}"
            payload = json.dumps(price)
            pipe.set(key, payload)
            pipe.publish(key, payload)
        await pipe.execute()
```

- [ ] **Step 4: Run to verify pass**

Run: `KTB_TEST_VALKEY_URL=redis://localhost:6379/0 uv run pytest services/market-streamer/tests/test_stream.py -q` (with `docker compose -f compose.dev.yaml up -d valkey` from Task 10, or any local Valkey; without it the Valkey test skips)
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
uv run ruff check services/market-streamer && uv run ruff format --check services/market-streamer
git add services/market-streamer
git commit -m "feat: parse Kiwoom 0B ticks and publish them to Valkey"
```

---

### Task 4: market-streamer run loop, reconnect, stop time, entry point

**Files:**
- Create: `services/market-streamer/src/market_streamer/symbols.py`
- Create: `services/market-streamer/src/market_streamer/__main__.py`
- Modify: `services/market-streamer/src/market_streamer/stream.py`
- Test: `services/market-streamer/tests/test_run.py`, `services/market-streamer/tests/test_symbols.py`

**Interfaces:**
- Consumes: `Settings`, `connect_valkey` (Task 2); `frame_prices`, `publish`, `KST` (Task 3).
- Produces: `market_streamer.symbols.load_codes(dsn: str, index_name: str) -> list[str]`; in `market_streamer.stream`: `WEBSOCKET_PATH = "/api/dostk/websocket"`, `MAX_BACKOFF = 30.0`, `async stream_prices(client: KiwoomWebSocketClient, valkey: Redis, codes: list[str]) -> None` (never returns normally), `seconds_until(stop_at: time, now: datetime) -> float`, `async run(settings: Settings, now: datetime) -> None`.

- [ ] **Step 1: Write the failing tests**

`services/market-streamer/tests/test_symbols.py`:

```python
import pytest
import sqlalchemy as sa
from market_streamer.symbols import load_codes


@pytest.fixture
def dsn(tmp_path):
    url = f"sqlite:///{tmp_path / 'ktb.sqlite'}"
    engine = sa.create_engine(url)
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE corporation_indices (stock_code TEXT, index_name TEXT)"))
        conn.execute(
            sa.text("INSERT INTO corporation_indices VALUES (:code, :index)"),
            [
                {"code": "005930", "index": "KOSPI200"},
                {"code": "000660", "index": "KOSPI200"},
                {"code": "999999", "index": "KRX300"},
            ],
        )
    engine.dispose()
    return url


def test_load_codes_reads_one_index_in_code_order(dsn):
    assert load_codes(dsn, "KOSPI200") == ["000660", "005930"]


def test_load_codes_fails_when_the_index_is_empty(dsn):
    with pytest.raises(RuntimeError, match="run market-syncer"):
        load_codes(dsn, "KOSDAQ150")
```

`services/market-streamer/tests/test_run.py`:

```python
import asyncio
import json
from datetime import datetime

import pytest
from kiwoom import KiwoomWebSocketClient
from market_streamer import stream
from market_streamer.settings import Settings
from market_streamer.stream import KST

LOGIN_OK = {"trnm": "LOGIN", "return_code": 0, "return_msg": "ok"}
REG_OK = {"trnm": "REG", "return_code": 0, "return_msg": "ok"}
REG_REJECTED = {"trnm": "REG", "return_code": 1, "return_msg": "rejected"}
REAL = {
    "trnm": "REAL",
    "data": [
        {
            "type": "0B",
            "item": "005930",
            "values": {"10": "-82000", "11": "-500", "12": "-0.61", "13": "100", "20": "090123"},
        }
    ],
}


class FakeConnectionClosedOK(Exception):
    pass


class FakeSocket:
    def __init__(self, frames):
        self.frames = [json.dumps(frame) for frame in frames]
        self.sent = []

    async def send(self, text):
        self.sent.append(json.loads(text))

    async def recv(self):
        if not self.frames:
            raise FakeConnectionClosedOK()
        return self.frames.pop(0)

    async def close(self):
        pass


class FakeAuth:
    mode = "real"

    def get_access_token(self):
        return "token"

    def recover_from_auth_failure(self):
        pass


def _client(sockets):
    pending = list(sockets)

    async def connect(uri, timeout):
        if not pending:
            raise asyncio.CancelledError
        return pending.pop(0)

    return KiwoomWebSocketClient(FakeAuth(), connect_factory=connect)


def _record(monkeypatch):
    published, delays = [], []

    async def fake_publish(valkey, prices):
        published.append(prices)

    async def fake_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(stream, "publish", fake_publish)
    monkeypatch.setattr(stream.asyncio, "sleep", fake_sleep)
    return published, delays


def test_stream_publishes_ticks_and_registers_again_after_disconnect(monkeypatch):
    published, delays = _record(monkeypatch)
    first, second = FakeSocket([LOGIN_OK, REG_OK, REAL]), FakeSocket([LOGIN_OK, REG_OK])
    codes = ["000660", "005930"]

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(stream.stream_prices(_client([first, second]), object(), codes))

    for socket in (first, second):
        assert socket.sent[0] == {"trnm": "LOGIN", "token": "token"}
        assert socket.sent[1]["trnm"] == "REG"
        assert socket.sent[1]["data"] == [{"item": codes, "type": ["0B"]}]
    assert [[price["code"] for price in prices] for prices in published] == [["005930"]]
    assert published[0][0]["ts"].endswith("T09:01:23+09:00")
    assert delays == [1.0, 1.0]


def test_rejected_registration_backs_off_exponentially(monkeypatch):
    _, delays = _record(monkeypatch)
    sockets = [FakeSocket([LOGIN_OK, REG_REJECTED]) for _ in range(6)]

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(stream.stream_prices(_client(sockets), object(), ["005930"]))

    assert delays == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0]


@pytest.mark.parametrize(
    "now",
    [
        datetime(2026, 10, 13, 15, 39, 59, 900000, tzinfo=KST),
        datetime(2026, 10, 13, 16, 0, tzinfo=KST),
    ],
)
def test_run_exits_cleanly_at_the_stop_time(monkeypatch, now):
    monkeypatch.setenv("MARKET_STREAMER_POSTGRES_DSN", "postgresql+psycopg://x@localhost/x")
    monkeypatch.setenv("MARKET_STREAMER_KIWOOM_APP_KEY", "app")
    monkeypatch.setenv("MARKET_STREAMER_KIWOOM_SECRET_KEY", "secret")
    monkeypatch.setenv("MARKET_STREAMER_VALKEY_HOST", "localhost")
    closed = []

    class FakeValkey:
        async def aclose(self):
            closed.append("valkey")

    async def forever(client, valkey, codes):
        await asyncio.Event().wait()

    monkeypatch.setattr(stream, "load_codes", lambda dsn, index_name: ["005930"])
    monkeypatch.setattr(stream, "connect_valkey", lambda settings: FakeValkey())
    monkeypatch.setattr(stream, "stream_prices", forever)

    asyncio.run(stream.run(Settings(), now))

    assert closed == ["valkey"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest services/market-streamer/tests/test_run.py services/market-streamer/tests/test_symbols.py -q`
Expected: FAIL (`load_codes`, `stream_prices`, `run` missing).

- [ ] **Step 3: Implement `symbols.py`**

```python
import sqlalchemy as sa


def load_codes(dsn: str, index_name: str) -> list[str]:
    engine = sa.create_engine(dsn)
    try:
        with engine.connect() as conn:
            codes = list(
                conn.execute(
                    sa.text(
                        "SELECT stock_code FROM corporation_indices"
                        " WHERE index_name = :index_name ORDER BY stock_code"
                    ),
                    {"index_name": index_name},
                ).scalars()
            )
    finally:
        engine.dispose()
    if not codes:
        raise RuntimeError(
            f"corporation_indices has no rows for index_name={index_name!r}; run market-syncer"
        )
    return codes
```

- [ ] **Step 4: Add the run loop to `stream.py`**

Change the imports at the top of `stream.py` to:

```python
import asyncio
import json
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from kiwoom import KiwoomAuth, KiwoomWebSocketClient
from kiwoom.core.secrets import StaticSecretProvider
from kiwoom.core.token_store import MemoryTokenStore
from kiwoom.realtime import build_reg_packet
from ktb_core.logging import get_logger
from redis.asyncio import Redis

from market_streamer.settings import Settings
from market_streamer.symbols import load_codes
from market_streamer.valkey import connect_valkey
```

Add below `log = get_logger(__name__)`:

```python
WEBSOCKET_PATH = "/api/dostk/websocket"
MAX_BACKOFF = 30.0
```

Append to `stream.py`:

```python
async def stream_prices(client: KiwoomWebSocketClient, valkey: Redis, codes: list[str]) -> None:
    delay = 1.0
    while True:
        try:
            await client.subscribe(api_url=WEBSOCKET_PATH, body=build_reg_packet(codes, ["0B"]))
            async for message in client.iter_messages():
                trnm = message.get("trnm")
                if trnm == "REG":
                    if int(message.get("return_code", 0)) != 0:
                        raise RuntimeError(f"REG rejected: {message.get('return_msg')}")
                    delay = 1.0
                    log.info("kiwoom_registered", codes=len(codes))
                elif trnm == "REAL":
                    prices = frame_prices(message, datetime.now(KST).date())
                    if prices:
                        await publish(valkey, prices)
            log.warning("kiwoom_stream_closed")
        except Exception:
            log.exception("kiwoom_stream_failed")
        finally:
            await client.close()
        await asyncio.sleep(delay)
        delay = min(delay * 2, MAX_BACKOFF)


def seconds_until(stop_at: time, now: datetime) -> float:
    local = now.astimezone(KST)
    return (datetime.combine(local.date(), stop_at, KST) - local).total_seconds()


async def run(settings: Settings, now: datetime) -> None:
    codes = load_codes(settings.postgres_dsn, settings.index_name)
    auth = KiwoomAuth(
        settings.kiwoom_mode,
        StaticSecretProvider(settings.kiwoom_app_key, settings.kiwoom_secret_key),
        MemoryTokenStore(),
    )
    client = KiwoomWebSocketClient(auth)
    valkey = connect_valkey(settings)
    try:
        async with asyncio.timeout(seconds_until(settings.stop_at, now)):
            await stream_prices(client, valkey, codes)
    except TimeoutError:
        log.info("market_streamer_stopped", stop_at=settings.stop_at.isoformat())
    finally:
        await client.close()
        await valkey.aclose()
```

If `from kiwoom.realtime import build_reg_packet` fails to resolve, import it from `kiwoom.realtime.packets` (both exist in kwcli 1.0.3).

- [ ] **Step 5: Add the entry point**

`services/market-streamer/src/market_streamer/__main__.py`:

```python
import asyncio
import logging
from datetime import UTC, datetime

from ktb_core.logging import get_logger, setup_logging, start_logging

from market_streamer.settings import Settings
from market_streamer.stream import run

log = get_logger(__name__)


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level, service_name="market-streamer")
    logging.getLogger("kiwoom").setLevel(logging.WARNING)
    with start_logging(log, codes_index=settings.index_name):
        asyncio.run(run(settings, datetime.now(UTC)))


if __name__ == "__main__":
    main()
```

The `kiwoom` logger is raised to WARNING because kwcli logs every received frame at INFO.

- [ ] **Step 6: Run to verify pass**

Run: `uv run pytest services/market-streamer -q`
Expected: PASS (Valkey tests skip without `KTB_TEST_VALKEY_URL`).

- [ ] **Step 7: Commit**

```bash
uv run ruff check services/market-streamer && uv run ruff format --check services/market-streamer && uv run tach check
git add services/market-streamer
git commit -m "feat: stream KOSPI 200 prices with reconnect and a daily stop time"
```

---

### Task 5: market-http package, settings, Valkey connection, app, health

**Files:**
- Create: `services/market-http/pyproject.toml`
- Create: `services/market-http/src/market_http/__init__.py` (empty)
- Create: `services/market-http/src/market_http/settings.py`
- Create: `services/market-http/src/market_http/valkey.py`
- Create: `services/market-http/src/market_http/app.py`
- Create: `services/market-http/src/market_http/__main__.py`
- Create: `services/market-http/src/market_http/controllers/__init__.py` (empty)
- Create: `services/market-http/src/market_http/controllers/dependencies.py`
- Create: `services/market-http/src/market_http/repositories/__init__.py` (empty)
- Create: `services/market-http/tests/conftest.py`
- Test: `services/market-http/tests/test_app.py`, `services/market-http/tests/test_settings.py`
- Modify: `tach.toml`, `uv.lock`

**Interfaces:**
- Produces: `market_http.settings.Settings` (`questdb_conf`, the same `valkey_*`/`aws_region` fields and validator as Task 2, `host="0.0.0.0"`, `port=8000`, `log_level`); `market_http.valkey.connect_valkey(settings) -> Redis`; `market_http.app.create_app(valkey: Redis, questdb_conf: str) -> FastAPI`; in `controllers/dependencies.py`: `Valkey`, `QuestDBConf`, `Codes` (`list[str]`), `CODE = r"^[0-9A-Z]{6}$"`; fixture `valkey_url`.

- [ ] **Step 1: Create the package**

`services/market-http/pyproject.toml`:

```toml
[project]
name = "market-http"
version = "0.1.0"
description = "Public HTTP API for current prices and OHLCV history"
requires-python = ">=3.13,<3.15"
dependencies = [
    "ktb-core",
    "botocore>=1.40",
    "fastapi>=0.142",
    "pandas>=2.2",
    "pydantic>=2.9",
    "pydantic-settings>=2.7",
    "questdb>=5.0",
    "redis>=6.4",
    "uvicorn>=0.54",
]

[project.scripts]
market-http = "market_http.__main__:main"

[tool.uv.sources]
ktb-core = { workspace = true }

[build-system]
requires = ["uv_build>=0.12,<0.13"]
build-backend = "uv_build"
```

Create the three empty `__init__.py` files. In `tach.toml` add `"services/market-http/src",` to `source_roots` and append:

```toml
[[modules]]
path = "market_http"
depends_on = ["ktb_core"]
```

Copy `services/market-streamer/tests/conftest.py` to `services/market-http/tests/conftest.py` unchanged.

Run: `uv lock && uv sync --all-packages --group migrations`

- [ ] **Step 2: Write the failing tests**

`services/market-http/tests/test_settings.py`:

```python
import pytest
from market_http.settings import Settings
from pydantic import ValidationError


def _populate(monkeypatch, **extra):
    env = {
        "MARKET_HTTP_QUESTDB_CONF": "ws::addr=localhost:9000;",
        "MARKET_HTTP_VALKEY_HOST": "localhost",
        **extra,
    }
    for name, value in env.items():
        monkeypatch.setenv(name, value)


def test_defaults(monkeypatch):
    _populate(monkeypatch)

    settings = Settings()

    assert settings.host == "0.0.0.0"
    assert settings.port == 8000
    assert settings.valkey_auth_mode == "none"


def test_iam_needs_username_and_cache_name(monkeypatch):
    _populate(monkeypatch, MARKET_HTTP_VALKEY_AUTH_MODE="iam")

    with pytest.raises(ValidationError, match="valkey_username"):
        Settings()
```

`services/market-http/tests/test_app.py`:

```python
from fastapi.testclient import TestClient
from market_http.app import create_app
from redis.asyncio import Redis


def test_health_touches_no_datastore():
    with TestClient(create_app(Redis(host="127.0.0.1", port=1), "unused")) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_cors_allows_any_origin_for_get():
    with TestClient(create_app(Redis(host="127.0.0.1", port=1), "unused")) as client:
        response = client.get("/health", headers={"Origin": "https://example.com"})

    assert response.headers["access-control-allow-origin"] == "*"
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest services/market-http -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 4: Implement settings, Valkey, dependencies, app, main**

`services/market-http/src/market_http/settings.py`:

```python
from typing import Literal, Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MARKET_HTTP_", extra="ignore", hide_input_in_errors=True
    )

    questdb_conf: str
    valkey_host: str
    valkey_port: int = 6379
    valkey_username: str | None = None
    valkey_cache_name: str | None = None
    valkey_tls: bool = False
    valkey_auth_mode: Literal["none", "iam"] = "none"
    aws_region: str = "ap-northeast-2"
    host: str = "0.0.0.0"
    port: int = Field(default=8000, gt=0, lt=65536)
    log_level: str = "INFO"

    @model_validator(mode="after")
    def _iam_needs_identity(self) -> Self:
        if self.valkey_auth_mode == "iam" and not (
            self.valkey_username and self.valkey_cache_name
        ):
            raise ValueError("iam auth needs valkey_username and valkey_cache_name")
        return self
```

`services/market-http/src/market_http/valkey.py`: copy `services/market-streamer/src/market_streamer/valkey.py` exactly, changing only `from market_streamer.settings import Settings` to `from market_http.settings import Settings`.

`services/market-http/src/market_http/controllers/dependencies.py`:

```python
from typing import Annotated

from fastapi import Depends, Query, Request
from redis.asyncio import Redis

CODE = r"^[0-9A-Z]{6}$"


def _valkey(request: Request) -> Redis:
    return request.app.state.valkey


def _questdb_conf(request: Request) -> str:
    return request.app.state.questdb_conf


def _codes(codes: Annotated[str, Query(pattern=r"^[0-9A-Z]{6}(,[0-9A-Z]{6})*$")]) -> list[str]:
    return list(dict.fromkeys(codes.split(",")))


Valkey = Annotated[Redis, Depends(_valkey)]
QuestDBConf = Annotated[str, Depends(_questdb_conf)]
Codes = Annotated[list[str], Depends(_codes)]
```

`services/market-http/src/market_http/app.py`:

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis


def create_app(valkey: Redis, questdb_conf: str) -> FastAPI:
    app = FastAPI(title="market-http")
    app.state.valkey = valkey
    app.state.questdb_conf = questdb_conf
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"])

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
```

`services/market-http/src/market_http/__main__.py`:

```python
import uvicorn
from ktb_core.logging import setup_logging

from market_http.app import create_app
from market_http.settings import Settings
from market_http.valkey import connect_valkey


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level, service_name="market-http")
    app = create_app(connect_valkey(settings), settings.questdb_conf)
    uvicorn.run(app, host=settings.host, port=settings.port, log_config=None)
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest services/market-http -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
uv run ruff check services/market-http && uv run ruff format --check services/market-http && uv run tach check
git add services/market-http tach.toml uv.lock
git commit -m "feat: add market-http app with Valkey connection and health check"
```

---

### Task 6: market-http `/bars/{code}` with timestamp cursors

**Files:**
- Create: `services/market-http/src/market_http/repositories/questdb.py`
- Create: `services/market-http/src/market_http/repositories/bars.py`
- Create: `services/market-http/src/market_http/controllers/bars.py`
- Modify: `services/market-http/src/market_http/app.py` (include router)
- Test: `services/market-http/tests/test_bars.py`

**Interfaces:**
- Consumes: `create_app`, `QuestDBConf`, `CODE` (Task 5).
- Produces: in `repositories/questdb.py`: `records(db, sql: str, binds: list[object]) -> list[dict[str, Any]]`, `as_utc(value: object) -> datetime`, `questdb_time(ts: datetime) -> str` (`YYYY-MM-DDTHH:MM:SS.ffffffZ` in UTC); in `repositories/bars.py`: `Timeframe = Literal["1m", "15m", "1h", "1d"]`, `Bar` model (`ts: datetime, open/high/low/close: float, volume: int`), `find_bars(conf: str, code: str, timeframe: Timeframe, limit: int, before: datetime | None) -> list[Bar]`; route `GET /bars/{code}` returning `{"items": [Bar], "next_cursor": str | None}`, newest first.

- [ ] **Step 1: Write the failing tests**

`services/market-http/tests/test_bars.py`:

```python
from base64 import urlsafe_b64encode
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from market_http.app import create_app
from market_http.repositories import bars as bars_module
from redis.asyncio import Redis

NEWEST = datetime(2026, 10, 13, 6, 30, tzinfo=UTC)


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def to_pandas(self):
        return pd.DataFrame(self.rows)


class FakeDB:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def query(self, sql, binds=None):
        self.queries.append((sql, binds))
        return FakeResult(self.rows)


def _rows(count):
    return [
        {
            "ts": (NEWEST - timedelta(minutes=offset)).replace(tzinfo=None),
            "open": 1.0,
            "high": 2.0,
            "low": 0.5,
            "close": 1.5,
            "volume": 10,
        }
        for offset in range(count)
    ]


@pytest.fixture
def client():
    with TestClient(create_app(Redis(host="127.0.0.1", port=1), "conf")) as client:
        yield client


@pytest.mark.parametrize(
    ("timeframe", "view", "session"),
    [("1m", "bars_1m", True), ("15m", "bars_15m", False), ("1h", "bars_1h", False),
     ("1d", "bars_1d", True)],
)  # fmt: skip
def test_bars_reads_the_timeframe_view_newest_first(monkeypatch, client, timeframe, view, session):
    db = FakeDB(_rows(3))
    monkeypatch.setattr(bars_module.questdb, "connect", lambda conf: db)

    response = client.get(f"/bars/005930?timeframe={timeframe}&limit=2")

    sql, binds = db.queries[0]
    assert f"FROM {view} " in sql
    assert ("session = 'regular'" in sql) is session
    assert "ORDER BY ts DESC" in sql
    assert binds == ["005930", 3]
    body = response.json()
    assert [datetime.fromisoformat(item["ts"]) for item in body["items"]] == [
        NEWEST,
        NEWEST - timedelta(minutes=1),
    ]
    assert body["next_cursor"] is not None


def test_cursor_returns_the_next_older_page(monkeypatch, client):
    first = FakeDB(_rows(3))
    monkeypatch.setattr(bars_module.questdb, "connect", lambda conf: first)
    cursor = client.get("/bars/005930?timeframe=1m&limit=2").json()["next_cursor"]
    second = FakeDB(_rows(1))
    monkeypatch.setattr(bars_module.questdb, "connect", lambda conf: second)

    body = client.get(f"/bars/005930?timeframe=1m&limit=2&cursor={cursor}").json()

    sql, binds = second.queries[0]
    assert "ts < cast($2 AS TIMESTAMP)" in sql
    assert binds == ["005930", "2026-10-13T06:29:00.000000Z", 3]
    assert body["next_cursor"] is None


@pytest.mark.parametrize(
    "query",
    [
        "timeframe=5m",
        "timeframe=1m&cursor=not-base64!",
        "timeframe=1m&cursor=" + urlsafe_b64encode(b"2026-10-13T06:29:00").decode(),
        "timeframe=1m&limit=0",
        "timeframe=1m&limit=1001",
    ],
)
def test_invalid_query_is_rejected(client, query):
    assert client.get(f"/bars/005930?{query}").status_code == 422


def test_malformed_code_is_rejected(client):
    assert client.get("/bars/5930?timeframe=1m").status_code == 422
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest services/market-http/tests/test_bars.py -q`
Expected: FAIL (404 / import error for `market_http.repositories.bars`).

- [ ] **Step 3: Implement the QuestDB helpers and bars repository**

`services/market-http/src/market_http/repositories/questdb.py`:

```python
from datetime import UTC, datetime
from typing import Any

import pandas as pd


def records(db: Any, sql: str, binds: list[object]) -> list[dict[str, Any]]:
    with db.query(sql, binds) as result:
        return result.to_pandas().to_dict("records")


def as_utc(value: object) -> datetime:
    ts = pd.Timestamp(value).to_pydatetime()
    return ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)


def questdb_time(ts: datetime) -> str:
    return ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
```

`services/market-http/src/market_http/repositories/bars.py`:

```python
from datetime import datetime
from typing import Literal

import questdb
from pydantic import BaseModel

from market_http.repositories.questdb import as_utc, questdb_time, records

Timeframe = Literal["1m", "15m", "1h", "1d"]

VIEWS = {"1m": "bars_1m", "15m": "bars_15m", "1h": "bars_1h", "1d": "bars_1d"}
SESSION_FILTERED = {"1m", "1d"}


class Bar(BaseModel):
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


def find_bars(
    conf: str, code: str, timeframe: Timeframe, limit: int, before: datetime | None
) -> list[Bar]:
    sql = f"SELECT ts, open, high, low, close, volume FROM {VIEWS[timeframe]} WHERE symbol = $1"
    binds: list[object] = [code]
    if timeframe in SESSION_FILTERED:
        sql += " AND session = 'regular'"
    if before is not None:
        binds.append(questdb_time(before))
        sql += f" AND ts < cast(${len(binds)} AS TIMESTAMP)"
    binds.append(limit)
    sql += f" ORDER BY ts DESC LIMIT ${len(binds)}"
    with questdb.connect(conf) as db:
        rows = records(db, sql, binds)
    return [
        Bar(
            ts=as_utc(row["ts"]),
            open=row["open"],
            high=row["high"],
            low=row["low"],
            close=row["close"],
            volume=int(row["volume"]),
        )
        for row in rows
    ]
```

- [ ] **Step 4: Implement the controller and register it**

`services/market-http/src/market_http/controllers/bars.py`:

```python
from base64 import urlsafe_b64decode, urlsafe_b64encode
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel

from market_http.controllers.dependencies import CODE, QuestDBConf
from market_http.repositories.bars import Bar, Timeframe, find_bars

router = APIRouter()


class Page(BaseModel):
    items: list[Bar]
    next_cursor: str | None


def _cursor(cursor: str | None = None) -> datetime | None:
    if cursor is None:
        return None
    try:
        ts = datetime.fromisoformat(urlsafe_b64decode(cursor).decode())
    except ValueError as error:
        raise HTTPException(422, "invalid cursor") from error
    if ts.tzinfo is None:
        raise HTTPException(422, "invalid cursor")
    return ts


@router.get("/bars/{code}")
def list_bars(
    code: Annotated[str, Path(pattern=CODE)],
    timeframe: Timeframe,
    conf: QuestDBConf,
    before: Annotated[datetime | None, Depends(_cursor)],
    limit: Annotated[int, Query(ge=1, le=1000)] = 300,
) -> Page:
    rows = find_bars(conf, code, timeframe, limit + 1, before)
    if len(rows) <= limit:
        return Page(items=rows, next_cursor=None)
    cursor = urlsafe_b64encode(rows[limit - 1].ts.isoformat().encode()).decode()
    return Page(items=rows[:limit], next_cursor=cursor)
```

In `app.py` add `from market_http.controllers import bars` and, after `add_middleware`, `app.include_router(bars.router)`.

`binascii.Error` and `UnicodeDecodeError` are both `ValueError` subclasses, so the single `except ValueError` covers bad base64 and bad bytes.

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest services/market-http -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
uv run ruff check services/market-http && uv run ruff format --check services/market-http
git add services/market-http
git commit -m "feat: serve OHLCV history with timestamp cursors from market-http"
```

---

### Task 7: market-http `/prices` with QuestDB fill on miss

**Files:**
- Create: `services/market-http/src/market_http/repositories/prices.py`
- Create: `services/market-http/src/market_http/controllers/prices.py`
- Modify: `services/market-http/src/market_http/app.py` (include router)
- Test: `services/market-http/tests/test_prices.py`

**Interfaces:**
- Consumes: `records`, `as_utc`, `questdb_time` (Task 6); `Valkey`, `QuestDBConf`, `Codes` (Task 5); `valkey_url` fixture.
- Produces in `repositories/prices.py`: `KST`; `Price` model (`code: str, price: int, change: int, rate: float, volume: int, ts: datetime`); `price_key(code: str) -> str`; `questdb_price(db, code: str) -> Price | None`; `questdb_prices(conf: str, codes: list[str]) -> list[Price]`; `async current_prices(valkey: Redis, conf: str, codes: list[str]) -> list[Price]`. Route `GET /prices` in `controllers/prices.py` (`router`).

- [ ] **Step 1: Write the failing tests**

`services/market-http/tests/test_prices.py`:

```python
import asyncio
from datetime import UTC, datetime

import pandas as pd
import pytest
import redis
from fastapi.testclient import TestClient
from market_http.app import create_app
from market_http.repositories import prices as prices_module
from market_http.repositories.prices import KST, Price, questdb_price
from redis.asyncio import Redis

STREAMED = Price(
    code="005930",
    price=82100,
    change=-400,
    rate=-0.48,
    volume=500,
    ts=datetime(2026, 10, 13, 9, 1, 23, tzinfo=KST),
)
STORED = Price(
    code="000660",
    price=200000,
    change=1000,
    rate=0.5,
    volume=900,
    ts=datetime(2026, 10, 12, 15, 30, tzinfo=KST),
)


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def to_pandas(self):
        return pd.DataFrame(self.rows)


class FakeDB:
    def __init__(self, answers):
        self.answers = list(answers)
        self.queries = []

    def query(self, sql, binds=None):
        self.queries.append((sql, binds))
        return FakeResult(self.answers.pop(0))


def test_questdb_price_derives_change_and_day_volume():
    db = FakeDB(
        [
            [{"ts": datetime(2026, 10, 13, 0, 1), "close": 82000.0}],
            [{"close": 82500.0}],
            [{"volume": 1234.0}],
        ]
    )

    price = questdb_price(db, "005930")

    assert price == Price(
        code="005930",
        price=82000,
        change=-500,
        rate=-0.61,
        volume=1234,
        ts=datetime(2026, 10, 13, 9, 1, tzinfo=KST),
    )
    assert [binds for _, binds in db.queries] == [
        ["005930"],
        ["005930", "2026-10-13T00:00:00.000000Z"],
        ["005930", "2026-10-12T15:00:00.000000Z"],
    ]


def test_questdb_price_without_previous_close_or_volume():
    db = FakeDB([[{"ts": datetime(2026, 10, 13, 0, 1), "close": 100.0}], [], [{"volume": None}]])

    price = questdb_price(db, "005930")

    assert price is not None
    assert (price.change, price.rate, price.volume) == (0, 0.0, 0)


def test_questdb_price_is_none_without_minute_bars():
    assert questdb_price(FakeDB([[]]), "005930") is None


def _app_client(valkey_url):
    return TestClient(create_app(Redis.from_url(valkey_url, decode_responses=True), "conf"))


def test_prices_reads_valkey_without_touching_questdb(monkeypatch, valkey_url):
    redis.Redis.from_url(valkey_url).set("price:005930", STREAMED.model_dump_json())
    monkeypatch.setattr(
        prices_module, "questdb_prices", lambda conf, codes: pytest.fail("QuestDB was queried")
    )

    with _app_client(valkey_url) as client:
        response = client.get("/prices?codes=005930")

    assert response.status_code == 200
    assert [Price.model_validate(item) for item in response.json()] == [STREAMED]


def test_prices_fills_a_miss_from_questdb_and_stores_it(monkeypatch, valkey_url):
    redis.Redis.from_url(valkey_url).set("price:005930", STREAMED.model_dump_json())
    asked = []
    monkeypatch.setattr(
        prices_module, "questdb_prices", lambda conf, codes: asked.append(codes) or [STORED]
    )

    with _app_client(valkey_url) as client:
        response = client.get("/prices?codes=005930,000660")

    assert asked == [["000660"]]
    assert [Price.model_validate(item) for item in response.json()] == [STREAMED, STORED]
    stored = redis.Redis.from_url(valkey_url, decode_responses=True).get("price:000660")
    assert Price.model_validate_json(stored) == STORED


def test_streamed_price_written_during_the_fill_wins(monkeypatch, valkey_url):
    raced = STORED.model_copy(update={"price": 201000})

    def fill(conf, codes):
        redis.Redis.from_url(valkey_url).set("price:000660", raced.model_dump_json())
        return [STORED]

    monkeypatch.setattr(prices_module, "questdb_prices", fill)

    with _app_client(valkey_url) as client:
        response = client.get("/prices?codes=000660")

    assert [Price.model_validate(item) for item in response.json()] == [raced]


def test_prices_omits_codes_without_any_data(monkeypatch, valkey_url):
    monkeypatch.setattr(prices_module, "questdb_prices", lambda conf, codes: [])

    with _app_client(valkey_url) as client:
        response = client.get("/prices?codes=123456")

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.parametrize("codes", ["5930", "005930,", "abcdef", ""])
def test_prices_rejects_malformed_codes(codes):
    with TestClient(create_app(Redis(host="127.0.0.1", port=1), "conf")) as client:
        assert client.get(f"/prices?codes={codes}").status_code == 422
```

`asyncio` and `UTC` are imported for Task 8, which appends to this file; if ruff flags them as unused now, drop them here and add them in Task 8.

- [ ] **Step 2: Run to verify failure**

Run: `KTB_TEST_VALKEY_URL=redis://localhost:6379/0 uv run pytest services/market-http/tests/test_prices.py -q`
Expected: FAIL (import error for `market_http.repositories.prices`).

- [ ] **Step 3: Implement the prices repository**

`services/market-http/src/market_http/repositories/prices.py`:

```python
import asyncio
from datetime import UTC, datetime, time, timedelta, timezone
from typing import Any

import pandas as pd
import questdb
from pydantic import BaseModel
from redis.asyncio import Redis

from market_http.repositories.questdb import as_utc, questdb_time, records

KST = timezone(timedelta(hours=9), "KST")

LATEST = "SELECT ts, close FROM bars WHERE timeframe = '1m' AND symbol = $1 LIMIT -1"
PREVIOUS_CLOSE = (
    "SELECT close FROM bars WHERE timeframe = '1d' AND session = 'regular'"
    " AND symbol = $1 AND ts < cast($2 AS TIMESTAMP) LIMIT -1"
)
DAY_VOLUME = (
    "SELECT sum(volume) AS volume FROM bars WHERE timeframe = '1m'"
    " AND symbol = $1 AND ts >= cast($2 AS TIMESTAMP)"
)


class Price(BaseModel):
    code: str
    price: int
    change: int
    rate: float
    volume: int
    ts: datetime


def price_key(code: str) -> str:
    return f"price:{code}"


def questdb_price(db: Any, code: str) -> Price | None:
    latest = records(db, LATEST, [code])
    if not latest:
        return None
    ts = as_utc(latest[0]["ts"]).astimezone(KST)
    price = int(latest[0]["close"])
    day = ts.date()
    previous = records(
        db, PREVIOUS_CLOSE, [code, questdb_time(datetime.combine(day, time(), UTC))]
    )
    volume = records(db, DAY_VOLUME, [code, questdb_time(datetime.combine(day, time(), KST))])
    previous_close = int(previous[0]["close"]) if previous else price
    change = price - previous_close
    day_volume = volume[0]["volume"] if volume else None
    return Price(
        code=code,
        price=price,
        change=change,
        rate=round(change / previous_close * 100, 2) if previous_close else 0.0,
        volume=int(day_volume) if pd.notna(day_volume) else 0,
        ts=ts,
    )


def questdb_prices(conf: str, codes: list[str]) -> list[Price]:
    with questdb.connect(conf) as db:
        return [price for code in codes if (price := questdb_price(db, code)) is not None]


async def current_prices(valkey: Redis, conf: str, codes: list[str]) -> list[Price]:
    values = dict(zip(codes, await valkey.mget([price_key(c) for c in codes]), strict=True))
    missing = [code for code, value in values.items() if value is None]
    if missing:
        filled = await asyncio.to_thread(questdb_prices, conf, missing)
        if filled:
            async with valkey.pipeline(transaction=False) as pipe:
                for price in filled:
                    pipe.set(price_key(price.code), price.model_dump_json(), nx=True)
                await pipe.execute()
            refreshed = await valkey.mget([price_key(c) for c in missing])
            values.update(zip(missing, refreshed, strict=True))
    return [Price.model_validate_json(value) for value in values.values() if value is not None]
```

Daily bars are stored at UTC midnight of their trading date (`parse_daily_ts`), so the previous close is the last regular daily bar before `<KST date>T00:00Z`. The day's volume starts at KST midnight.

- [ ] **Step 4: Implement the controller and register it**

`services/market-http/src/market_http/controllers/prices.py`:

```python
from fastapi import APIRouter

from market_http.controllers.dependencies import Codes, QuestDBConf, Valkey
from market_http.repositories.prices import Price, current_prices

router = APIRouter()


@router.get("/prices")
async def get_prices(valkey: Valkey, conf: QuestDBConf, codes: Codes) -> list[Price]:
    return await current_prices(valkey, conf, codes)
```

In `app.py` change the import to `from market_http.controllers import bars, prices` and add `app.include_router(prices.router)` before the bars router.

- [ ] **Step 5: Run to verify pass**

Run: `KTB_TEST_VALKEY_URL=redis://localhost:6379/0 uv run pytest services/market-http -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
uv run ruff check services/market-http && uv run ruff format --check services/market-http
git add services/market-http
git commit -m "feat: serve current prices from Valkey and fill misses from QuestDB"
```

---

### Task 8: market-http `/prices/stream` SSE

**Files:**
- Modify: `services/market-http/src/market_http/controllers/prices.py`
- Test: `services/market-http/tests/test_prices.py` (append)

**Interfaces:**
- Consumes: `current_prices`, `price_key`, `Price` (Task 7).
- Produces: route `GET /prices/stream` implemented by `async stream_prices(valkey: Redis, conf: str, codes: list[str]) -> AsyncIterable[ServerSentEvent]` in `controllers/prices.py`, emitting `event="price"` events.

- [ ] **Step 1: Write the failing test**

Append to `services/market-http/tests/test_prices.py`:

```python
from market_http.controllers.prices import stream_prices


def test_stream_subscribes_then_sends_snapshot_then_live_events(monkeypatch, valkey_url):
    redis.Redis.from_url(valkey_url).set("price:005930", STREAMED.model_dump_json())
    monkeypatch.setattr(prices_module, "questdb_prices", lambda conf, codes: [])
    live = STREAMED.model_copy(update={"price": 82200, "ts": datetime(2026, 10, 13, 0, 2, tzinfo=UTC)})

    async def scenario():
        valkey = Redis.from_url(valkey_url, decode_responses=True)
        events = stream_prices(valkey, "conf", ["005930"])
        snapshot = await anext(events)
        await valkey.publish("price:005930", live.model_dump_json())
        published = await asyncio.wait_for(anext(events), 5)
        await events.aclose()
        await valkey.aclose()
        return snapshot, published

    snapshot, published = asyncio.run(scenario())

    assert snapshot.event == "price"
    assert snapshot.data == STREAMED
    assert published.event == "price"
    assert Price.model_validate_json(published.raw_data) == live
```

Move the new import to the top of the file with the others.

- [ ] **Step 2: Run to verify failure**

Run: `KTB_TEST_VALKEY_URL=redis://localhost:6379/0 uv run pytest services/market-http/tests/test_prices.py -q`
Expected: FAIL with `ImportError: cannot import name 'stream_prices'`.

- [ ] **Step 3: Implement the stream route**

Replace `services/market-http/src/market_http/controllers/prices.py` with:

```python
from collections.abc import AsyncIterable

from fastapi import APIRouter
from fastapi.sse import EventSourceResponse, ServerSentEvent

from market_http.controllers.dependencies import Codes, QuestDBConf, Valkey
from market_http.repositories.prices import Price, current_prices, price_key

router = APIRouter()


@router.get("/prices")
async def get_prices(valkey: Valkey, conf: QuestDBConf, codes: Codes) -> list[Price]:
    return await current_prices(valkey, conf, codes)


@router.get("/prices/stream", response_class=EventSourceResponse)
async def stream_prices(
    valkey: Valkey, conf: QuestDBConf, codes: Codes
) -> AsyncIterable[ServerSentEvent]:
    pubsub = valkey.pubsub()
    try:
        await pubsub.subscribe(*(price_key(code) for code in codes))
        for price in await current_prices(valkey, conf, codes):
            yield ServerSentEvent(data=price, event="price")
        async for message in pubsub.listen():
            if message["type"] == "message":
                yield ServerSentEvent(raw_data=message["data"], event="price")
    finally:
        await pubsub.aclose()
```

`EventSourceResponse` already sends `: ping` every 15 s while the generator is idle; add no keep-alive. Subscribing before the snapshot means a tick published during the lookup is delivered after the snapshot instead of lost; clients order by `ts`.

- [ ] **Step 4: Run to verify pass**

Run: `KTB_TEST_VALKEY_URL=redis://localhost:6379/0 uv run pytest services/market-http -q`
Expected: PASS.

- [ ] **Step 5: Smoke-test the route by hand**

```bash
docker compose -f compose.dev.yaml up -d valkey questdb
MARKET_HTTP_QUESTDB_CONF='ws::addr=localhost:9000;' MARKET_HTTP_VALKEY_HOST=localhost MARKET_HTTP_PORT=8001 uv run market-http &
curl -N 'http://localhost:8001/prices/stream?codes=005930' &
docker compose -f compose.dev.yaml exec valkey valkey-cli PUBLISH price:005930 '{"code":"005930","price":1,"change":0,"rate":0.0,"volume":0,"ts":"2026-10-13T09:00:00+09:00"}'
```

Expected: `curl` prints `event: price` and the published JSON, then `: ping` lines every 15 s. Stop both background jobs afterwards.

- [ ] **Step 6: Commit**

```bash
uv run ruff check services/market-http && uv run ruff format --check services/market-http
git add services/market-http
git commit -m "feat: stream price events over SSE from market-http"
```

---

### Task 9: portfolio-builder reads bars from market-http

**Files:**
- Modify: `services/portfolio-builder/src/portfolio_builder/market.py`
- Modify: `services/portfolio-builder/src/portfolio_builder/settings.py`
- Modify: `services/portfolio-builder/src/portfolio_builder/__main__.py`
- Modify: `services/portfolio-builder/pyproject.toml`
- Modify: `services/portfolio-builder/tests/test_technicals.py`, `tests/test_universe.py`, `tests/test_settings.py`, `tests/test_main.py`
- Delete: `services/portfolio-builder/tests/test_universe_questdb.py`

**Interfaces:**
- Consumes: market-http `GET /bars/{code}?timeframe=&limit=300` (Task 6) returning `{"items": [{"ts","open","high","low","close","volume"}], "next_cursor"}` newest first.
- Produces: `portfolio_builder.market.HttpMarket(base_uri: str, engine: sa.Engine, timeout: float = 10.0)` implementing `Market.bars(symbol, timeframe) -> tuple[Bars, datetime | None]` and `Market.universe_closes() -> dict[str, Array]`; `Settings.market_http_base_uri: str` (required); `Settings.questdb_conf` removed.

- [ ] **Step 1: Replace the QuestDB market tests**

In `tests/test_technicals.py`: change `from portfolio_builder.market import QuestDBMarket` to `from portfolio_builder.market import HttpMarket`, delete the `FakeDB`/`FakeResult` helpers and the three tests `test_bars_reads_the_timeframe_view_with_questdb_latest_limit`, `test_bars_rejects_an_unknown_timeframe`, `test_universe_closes_groups_kospi200_members_by_symbol` (keep every other test and any helper they still use), and append:

```python
from io import BytesIO
from unittest.mock import patch


class Response(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def _page(rows):
    return Response(json.dumps({"items": rows, "next_cursor": None}).encode())


@pytest.mark.parametrize("timeframe", ["1m", "15m", "1h", "1d"])
def test_http_market_requests_300_bars_and_orders_them_oldest_first(timeframe):
    newest = {"ts": "2026-09-28T06:30:00Z", "open": 1.0, "high": 3.0, "low": 1.0,
              "close": 2.0, "volume": 30}
    older = {"ts": "2026-09-27T00:00:00Z", "open": 1.0, "high": 2.0, "low": 1.0,
             "close": 1.5, "volume": 20}  # fmt: skip
    with patch("portfolio_builder.market.urlopen", return_value=_page([newest, older])) as call:
        bars, as_of = HttpMarket("http://market", None).bars("005930", timeframe)

    request = call.call_args.args[0]
    assert request.full_url == f"http://market/bars/005930?timeframe={timeframe}&limit=300"
    assert bars.close.tolist() == [1.5, 2.0]
    assert bars.volume.dtype == np.float64
    assert as_of == AS_OF


def test_http_market_rejects_an_unknown_timeframe():
    with pytest.raises(KeyError):
        HttpMarket("http://market", None).bars("005930", "5m")


def test_http_market_returns_empty_bars_for_unknown_symbol():
    with patch("portfolio_builder.market.urlopen", return_value=_page([])):
        bars, as_of = HttpMarket("http://market", None).bars("999999", "1d")

    assert bars.close.size == 0
    assert as_of is None
```

Move the new imports to the top of the file.

Replace `tests/test_universe.py` with:

```python
import json
from io import BytesIO
from unittest.mock import patch

import numpy as np
import pytest
import sqlalchemy as sa
from portfolio_builder.market import HttpMarket


class Response(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


@pytest.fixture
def universe_engine():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE corporation_indices (stock_code TEXT, index_name TEXT)"))
    yield engine
    engine.dispose()


def test_empty_universe_makes_no_requests(universe_engine):
    with patch("portfolio_builder.market.urlopen", side_effect=AssertionError("requested")):
        assert HttpMarket("http://market", universe_engine).universe_closes() == {}


def test_universe_reads_daily_bars_per_kospi200_member(universe_engine):
    with universe_engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO corporation_indices VALUES (:code, 'KOSPI200')"),
            [{"code": "005930"}, {"code": "000660"}],
        )
        conn.execute(sa.text("INSERT INTO corporation_indices VALUES ('removed', 'KRX300')"))

    def respond(request, timeout):
        if "000660" in request.full_url:
            return Response(json.dumps({"items": [], "next_cursor": None}).encode())
        rows = [
            {"ts": "2026-09-28T00:00:00Z", "open": 1, "high": 1, "low": 1, "close": 2.0,
             "volume": 1},
            {"ts": "2026-09-27T00:00:00Z", "open": 1, "high": 1, "low": 1, "close": 1.0,
             "volume": 1},
        ]  # fmt: skip
        return Response(json.dumps({"items": rows, "next_cursor": None}).encode())

    with patch("portfolio_builder.market.urlopen", side_effect=respond) as call:
        universe = HttpMarket("http://market", universe_engine).universe_closes()

    urls = [c.args[0].full_url for c in call.call_args_list]
    assert urls == [
        "http://market/bars/000660?timeframe=1d&limit=300",
        "http://market/bars/005930?timeframe=1d&limit=300",
    ]
    assert list(universe) == ["005930"]
    np.testing.assert_array_equal(universe["005930"], [1.0, 2.0])
```

Delete `tests/test_universe_questdb.py`. In `tests/test_settings.py` and `tests/test_main.py` replace `"PORTFOLIO_BUILDER_QUESTDB_CONF": "ws::addr=localhost:9000;"` with `"PORTFOLIO_BUILDER_MARKET_HTTP_BASE_URI": "http://market-http:8000"`, and in `test_settings.py` replace `assert settings.questdb_conf == "ws::addr=localhost:9000;"` with `assert settings.market_http_base_uri == "http://market-http:8000"`. Search `tests/test_main.py` for `QuestDBMarket` or `questdb_conf` and rename to `HttpMarket` / `market_http_base_uri`.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest services/portfolio-builder -q`
Expected: FAIL (`HttpMarket` missing, settings field missing).

- [ ] **Step 3: Implement `HttpMarket`**

Replace `services/portfolio-builder/src/portfolio_builder/market.py` with:

```python
import json
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np
import sqlalchemy as sa

from portfolio_builder.measurement import Array, Bars

TIMEFRAMES = {"1m", "15m", "1h", "1d"}
LIMIT = 300
KOSPI200 = "KOSPI200"


class Market(ABC):
    @abstractmethod
    def bars(self, symbol: str, timeframe: str) -> tuple[Bars, datetime | None]: ...

    @abstractmethod
    def universe_closes(self) -> dict[str, Array]: ...


class HttpMarket(Market):
    def __init__(self, base_uri: str, engine: sa.Engine, timeout: float = 10.0) -> None:
        self._base_uri = base_uri.rstrip("/")
        self._engine = engine
        self._timeout = timeout

    def _oldest_first(self, symbol: str, timeframe: str) -> list[dict[str, Any]]:
        if timeframe not in TIMEFRAMES:
            raise KeyError(timeframe)
        query = urlencode({"timeframe": timeframe, "limit": LIMIT})
        request = Request(
            f"{self._base_uri}/bars/{symbol}?{query}", headers={"Accept": "application/json"}
        )
        with urlopen(request, timeout=self._timeout) as response:
            return list(reversed(json.loads(response.read())["items"]))

    def bars(self, symbol: str, timeframe: str) -> tuple[Bars, datetime | None]:
        rows = self._oldest_first(symbol, timeframe)

        def column(name: str) -> Array:
            return np.array([row[name] for row in rows], dtype=np.float64)

        bars = Bars(column("high"), column("low"), column("close"), column("volume"))
        return bars, (datetime.fromisoformat(rows[-1]["ts"]) if rows else None)

    def universe_closes(self) -> dict[str, Array]:
        with self._engine.connect() as conn:
            members = sorted(
                set(
                    conn.execute(
                        sa.text(
                            "SELECT stock_code FROM corporation_indices WHERE index_name = :name"
                        ),
                        {"name": KOSPI200},
                    ).scalars()
                )
            )
        closes = {code: self._oldest_first(code, "1d") for code in members}
        return {
            code: np.array([row["close"] for row in rows], dtype=np.float64)
            for code, rows in closes.items()
            if rows
        }
```

- [ ] **Step 4: Update settings, main, and dependencies**

In `settings.py` replace `questdb_conf: str` with `market_http_base_uri: str`. In `__main__.py` change the import to `from portfolio_builder.market import HttpMarket` and the tool line to `technicals_tool(engine, HttpMarket(settings.market_http_base_uri, engine)),`.

In `pyproject.toml` remove `"questdb>=5.0",`. Run `grep -rn "pandas\|import pd" services/portfolio-builder/src`; if nothing imports pandas, also remove `"pandas>=2.2",` and drop `"pandas"` from `DEP002` (leaving `DEP002 = ["psycopg"]`) and its comment line about `to_pandas()`.

- [ ] **Step 5: Run to verify pass**

Run: `uv lock && uv sync --all-packages --group migrations && uv run pytest services/portfolio-builder -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
uv run ruff check services/portfolio-builder && uv run ruff format --check services/portfolio-builder
git add services/portfolio-builder uv.lock
git commit -m "refactor: read portfolio-builder bars through market-http"
```

---

### Task 10: Images, Compose, systemd, CI/CD, and docs

**Files:**
- Create: `docker/market-streamer.Dockerfile`, `docker/market-http.Dockerfile`
- Create: `docker/requirements/market-streamer.txt`, `docker/requirements/market-http.txt` (generated)
- Modify: `docker/requirements/app.txt`, `docker/requirements/portfolio-builder.txt` (generated)
- Modify: `docker/app.Dockerfile`
- Modify: `compose.dev.yaml`, `compose.prod.yaml`
- Create: `infrastructure/systemd/ktb-market-streamer.service.in`, `infrastructure/systemd/ktb-market-streamer.timer`
- Modify: `.github/workflows/ci-dev.yaml`, `.github/workflows/cd-dev.yaml`, `.github/workflows/cd.yaml`
- Modify: `AGENTS.md`, `README.md`

**Interfaces:**
- Consumes: console scripts `market-streamer`, `market-http`; all settings from Tasks 2, 5, 9.

- [ ] **Step 1: Dockerfiles**

`docker/market-streamer.Dockerfile`:

```dockerfile
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    VIRTUAL_ENV=/app/.venv

WORKDIR /app
RUN uv venv "$VIRTUAL_ENV"

COPY docker/requirements/market-streamer.txt ./requirements.txt
RUN uv pip install --require-hashes --requirement requirements.txt

COPY pyproject.toml ./
COPY packages/core packages/core
COPY services/market-streamer services/market-streamer
RUN uv pip install --no-deps ./packages/core ./services/market-streamer

FROM python:3.13-slim-bookworm AS runtime

RUN useradd --create-home --uid 10001 app
WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

USER app
CMD ["market-streamer"]
```

`docker/market-http.Dockerfile`: the same with every `market-streamer` replaced by `market-http`, plus `EXPOSE 8000` before `CMD ["market-http"]`.

In `docker/app.Dockerfile` add `./services/market-http \` after `./services/market-collector \` and `./services/market-streamer \` after `./services/market-http \` (keep alphabetical order).

- [ ] **Step 2: Export requirements**

```bash
uv lock
uv export --package market-streamer --no-dev --no-emit-workspace --format requirements-txt -o docker/requirements/market-streamer.txt
uv export --package market-http --no-dev --no-emit-workspace --format requirements-txt -o docker/requirements/market-http.txt
uv export --package portfolio-builder --no-dev --no-emit-workspace --format requirements-txt -o docker/requirements/portfolio-builder.txt
uv export --all-packages --no-dev --group migrations --no-emit-workspace --format requirements-txt -o docker/requirements/app.txt
```

- [ ] **Step 3: Compose (dev)**

In `compose.dev.yaml` add after `questdb:`:

```yaml
  valkey:
    image: valkey/valkey:8.1.4
    ports:
      - "127.0.0.1:6379:6379"
    healthcheck:
      test: ["CMD", "valkey-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 12
```

Add after `market-collector:`:

```yaml
  market-streamer:
    profiles: ["jobs"]
    build:
      context: .
      dockerfile: docker/market-streamer.Dockerfile
    environment:
      MARKET_STREAMER_POSTGRES_DSN: postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}
      MARKET_STREAMER_KIWOOM_APP_KEY: ${MARKET_STREAMER_KIWOOM_APP_KEY:-}
      MARKET_STREAMER_KIWOOM_SECRET_KEY: ${MARKET_STREAMER_KIWOOM_SECRET_KEY:-}
      MARKET_STREAMER_KIWOOM_MODE: ${MARKET_STREAMER_KIWOOM_MODE:-real}
      MARKET_STREAMER_STOP_AT: ${MARKET_STREAMER_STOP_AT:-15:40}
      MARKET_STREAMER_VALKEY_HOST: valkey
      MARKET_STREAMER_LOG_LEVEL: ${MARKET_STREAMER_LOG_LEVEL:-INFO}
    depends_on:
      postgres:
        condition: service_healthy
      valkey:
        condition: service_healthy
    restart: "no"

  market-http:
    build:
      context: .
      dockerfile: docker/market-http.Dockerfile
    environment:
      MARKET_HTTP_QUESTDB_CONF: ws::addr=questdb:9000;
      MARKET_HTTP_VALKEY_HOST: valkey
      MARKET_HTTP_LOG_LEVEL: ${MARKET_HTTP_LOG_LEVEL:-INFO}
    ports:
      - "127.0.0.1:8001:8000"
    depends_on:
      questdb:
        condition: service_healthy
      valkey:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"]
      interval: 10s
      timeout: 3s
      retries: 6
      start_period: 10s
    restart: unless-stopped
```

In `portfolio-builder:` replace `PORTFOLIO_BUILDER_QUESTDB_CONF: ws::addr=questdb:9000;` with `PORTFOLIO_BUILDER_MARKET_HTTP_BASE_URI: http://market-http:8000`, and replace its `questdb:` dependency with `market-http: {condition: service_healthy}` written in block style like the others.

- [ ] **Step 4: Compose (prod)**

In `compose.prod.yaml`:

- `market-collector`: change `MARKET_COLLECTOR_REQUEST_INTERVAL: ${MARKET_COLLECTOR_REQUEST_INTERVAL:-1.3}` to `${MARKET_COLLECTOR_REQUEST_INTERVAL:-0.25}`.
- Add, after `news-http:`:

```yaml
  market-http:
    <<: *app
    command: ["market-http"]
    restart: unless-stopped
    mem_limit: 384m
    ports:
      - "8001:8000"
    environment:
      MARKET_HTTP_QUESTDB_CONF: ws::addr=questdb:9000;
      MARKET_HTTP_VALKEY_HOST: ${MARKET_HTTP_VALKEY_HOST:?MARKET_HTTP_VALKEY_HOST must be set}
      MARKET_HTTP_VALKEY_PORT: ${MARKET_HTTP_VALKEY_PORT:-6379}
      MARKET_HTTP_VALKEY_USERNAME: ${MARKET_HTTP_VALKEY_USERNAME:-}
      MARKET_HTTP_VALKEY_CACHE_NAME: ${MARKET_HTTP_VALKEY_CACHE_NAME:-}
      MARKET_HTTP_VALKEY_TLS: ${MARKET_HTTP_VALKEY_TLS:-true}
      MARKET_HTTP_VALKEY_AUTH_MODE: ${MARKET_HTTP_VALKEY_AUTH_MODE:-iam}
      MARKET_HTTP_AWS_REGION: ${MARKET_HTTP_AWS_REGION:-ap-northeast-2}
      MARKET_HTTP_LOG_LEVEL: ${MARKET_HTTP_LOG_LEVEL:-INFO}
    depends_on:
      questdb:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"]
      interval: 10s
      timeout: 3s
      retries: 6
      start_period: 10s
```

- Add, after `market-collector:`:

```yaml
  market-streamer:
    <<: *app
    profiles: ["jobs"]
    command: ["market-streamer"]
    mem_limit: 384m
    stop_grace_period: 1m
    environment:
      <<: *postgres-environment
      MARKET_STREAMER_KIWOOM_APP_KEY: ${MARKET_STREAMER_KIWOOM_APP_KEY:-}
      MARKET_STREAMER_KIWOOM_SECRET_KEY: ${MARKET_STREAMER_KIWOOM_SECRET_KEY:-}
      MARKET_STREAMER_KIWOOM_MODE: ${MARKET_STREAMER_KIWOOM_MODE:-real}
      MARKET_STREAMER_INDEX_NAME: ${MARKET_STREAMER_INDEX_NAME:-KOSPI200}
      MARKET_STREAMER_STOP_AT: ${MARKET_STREAMER_STOP_AT:-15:40}
      MARKET_STREAMER_VALKEY_HOST: ${MARKET_STREAMER_VALKEY_HOST:-}
      MARKET_STREAMER_VALKEY_PORT: ${MARKET_STREAMER_VALKEY_PORT:-6379}
      MARKET_STREAMER_VALKEY_USERNAME: ${MARKET_STREAMER_VALKEY_USERNAME:-}
      MARKET_STREAMER_VALKEY_CACHE_NAME: ${MARKET_STREAMER_VALKEY_CACHE_NAME:-}
      MARKET_STREAMER_VALKEY_TLS: ${MARKET_STREAMER_VALKEY_TLS:-true}
      MARKET_STREAMER_VALKEY_AUTH_MODE: ${MARKET_STREAMER_VALKEY_AUTH_MODE:-iam}
      MARKET_STREAMER_AWS_REGION: ${MARKET_STREAMER_AWS_REGION:-ap-northeast-2}
      MARKET_STREAMER_LOG_LEVEL: ${MARKET_STREAMER_LOG_LEVEL:-INFO}
    depends_on:
      postgres:
        condition: service_healthy
```

and add `MARKET_STREAMER_POSTGRES_DSN: postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}` to the `x-postgres-environment` anchor.

- `portfolio-builder`: replace its `PORTFOLIO_BUILDER_QUESTDB_CONF` line with `PORTFOLIO_BUILDER_MARKET_HTTP_BASE_URI: ${PORTFOLIO_BUILDER_MARKET_HTTP_BASE_URI:-http://market-http:8000}` and its `questdb:` dependency with `market-http: {condition: service_healthy}` in block style.

Run: `POSTGRES_USER=x POSTGRES_PASSWORD=x POSTGRES_DB=x QUESTDB_USER=x QUESTDB_PASSWORD=x APP_IMAGE=x MARKET_HTTP_VALKEY_HOST=x PORTFOLIO_REBALANCER_BACKEND_URL=x docker compose -f compose.prod.yaml --profile jobs config --quiet` (add any other `:?` variable it reports as missing)
Expected: exit 0.

- [ ] **Step 5: systemd units**

`infrastructure/systemd/ktb-market-streamer.service.in`:

```ini
[Unit]
Description=KTB realtime market price stream
Wants=network-online.target
After=network-online.target docker.service
Requires=docker.service
# 작업 결과를 AI_CWAgent/ScheduledJobFailure(JobName=market-streamer)로 발행합니다.
OnSuccess=ktb-scheduled-job-success@market-streamer.service
OnFailure=ktb-scheduled-job-failure@market-streamer.service
StartLimitIntervalSec=1h
StartLimitBurst=5

[Service]
Type=simple
User=@@DEPLOY_USER@@
WorkingDirectory=@@DEPLOY_DIR@@
ExecStart=/usr/bin/flock -n -E 0 @@DEPLOY_DIR@@/.market-streamer.lock /usr/bin/docker compose --env-file .env --env-file .image.env -f compose.prod.yaml run --rm -T market-streamer
Restart=on-failure
RestartMode=direct
RestartSec=1m
TimeoutStopSec=2m
KillMode=mixed
```

`infrastructure/systemd/ktb-market-streamer.timer`:

```ini
[Unit]
Description=Start the KTB realtime market price stream before the weekday open

[Timer]
OnCalendar=Mon..Fri *-*-* 08:50:00 Asia/Seoul
AccuracySec=1s
Persistent=true
Unit=ktb-market-streamer.service

[Install]
WantedBy=timers.target
```

`Persistent=true` starts the stream on boot if the host was down at 08:50; the process exits immediately when booted after `STOP_AT`.

Run: `sed -e 's|@@DEPLOY_USER@@|nobody|; s|@@DEPLOY_DIR@@|/tmp|g' infrastructure/systemd/ktb-market-streamer.service.in > /tmp/claude-ktb-market-streamer.service && systemd-analyze verify /tmp/claude-ktb-market-streamer.service infrastructure/systemd/ktb-market-streamer.timer` (warnings about the missing `ktb-scheduled-job-*@` units are expected outside the deploy host)

- [ ] **Step 6: CI and CD**

`.github/workflows/ci-dev.yaml`: add `market-streamer market-http` to the `for pkg in …` list in `verify-exported-requirements`, and `- market-streamer` and `- market-http` to the `build-images` matrix.

`.github/workflows/cd-dev.yaml` and `.github/workflows/cd.yaml`: add `ktb-market-streamer.timer \` to both the `systemctl enable --now` list and the `for timer in` verification list, and change `compose up -d --wait postgres questdb` (the one inside the remote deploy script, after `compose pull`) to `compose up -d --wait postgres questdb market-http`. Leave the earlier rollback-path `up -d --wait postgres questdb` lines unchanged.

- [ ] **Step 7: Docs**

`AGENTS.md`:

- Commands block: after the `market-syncer` line add
  `docker compose -f compose.dev.yaml up -d valkey market-http   # http://127.0.0.1:8001`
  `docker compose -f compose.dev.yaml up market-streamer          # needs MARKET_STREAMER_KIWOOM_* keys`
  and add `KTB_TEST_VALKEY_URL=redis://localhost:6379/0` to the `uv run pytest` test line.
- Environment table: delete the `PORTFOLIO_BUILDER_QUESTDB_CONF` row; add `| PORTFOLIO_BUILDER_MARKET_HTTP_BASE_URI | portfolio-builder (market-http base URI, e.g. http://market-http:8000) | required |`; add `| KTB_TEST_VALKEY_URL | Valkey tests (skipped when unset); they FLUSHDB, so use a dedicated instance | — |`; change the `MARKET_COLLECTOR_REQUEST_INTERVAL` default text to `` `1.3` (production Compose `0.25`) ``; add rows for every `MARKET_STREAMER_*` and `MARKET_HTTP_*` variable from the spec's two settings tables, each with "market-streamer" or "market-http" in the Used-by column and the spec's default.
- Member table: add `| services/market-streamer | service | weekdays 08:50 KST until STOP_AT; one Kiwoom 0B WebSocket | ktb-core |` and `| services/market-http | service | long-running public HTTP server (uvicorn) | ktb-core |`; change market-collector's trigger to `every minute 09:00–15:30 KST, plus 15:35 for daily bars`.
- Architecture bullets: in the first bullet, replace "portfolio-builder reads news and graph data through news-http, company reference data and portfolios from PostgreSQL, and market bars from QuestDB" with "portfolio-builder reads news and graph data through news-http, company reference data and portfolios from PostgreSQL, and market bars through market-http", and add "market-streamer writes the latest price per KOSPI 200 stock to Valkey (`SET` and `PUBLISH price:{code}`; design: `docs/superpowers/specs/2026-10-13-market-streamer-http-design.md`)". Replace the bullet "news-http is the only inbound HTTP surface and is read-only." heading with "**news-http and market-http are the inbound HTTP surfaces.**" and append: "market-http is public with no authentication; it serves `/prices`, `/prices/stream` (SSE) and `/bars/{code}`, reads QuestDB and Valkey, and writes Valkey only to fill a missing price with `SET NX`. In `compose.dev.yaml` it binds to `127.0.0.1:8001`." In the portfolio-builder bullet replace "plus QuestDB for prices" with "and reads bars through market-http".

`README.md`: mirror the same changes in Korean, in the same table format as the existing sections (`| 변수 | 필수 | 기본값 | 설명 |`): replace the `PORTFOLIO_BUILDER_QUESTDB_CONF` row with `| \`PORTFOLIO_BUILDER_MARKET_HTTP_BASE_URI\` | 필수 | | market-http API 주소 |`, change the collector interval row's default to `` `1.3` (운영 Compose `0.25`) ``, and add `### market-streamer (\`MARKET_STREAMER_\`)` and `### market-http (\`MARKET_HTTP_\`)` sections listing every variable from the spec's settings tables with a one-phrase Korean description (e.g. `실시간 체결가를 쓰는 Valkey 호스트`, `IAM 토큰을 서명할 AWS 리전`). Add `MS["market-streamer"]` and `MH["market-http"]` to the mermaid diagram at line 22 with edges `MS --> Valkey`, `MH --> Valkey`, `MH --> QuestDB`.

- [ ] **Step 8: Full verification**

```bash
uv sync --all-packages --locked --group migrations
uv run ruff check . && uv run ruff format --check . && uv run tach check
uv run tach check-external -e packages/market-analyzer,services
KTB_TEST_VALKEY_URL=redis://localhost:6379/0 uv run pytest
docker build -f docker/market-streamer.Dockerfile . && docker build -f docker/market-http.Dockerfile . && docker build -f docker/app.Dockerfile .
git diff --exit-code -- docker/requirements/
```

Expected: all pass; the last command shows no diff after re-running Step 2's exports.

- [ ] **Step 9: Commit**

```bash
git add docker compose.dev.yaml compose.prod.yaml infrastructure/systemd .github/workflows AGENTS.md README.md uv.lock
git commit -m "chore: deploy market-streamer and market-http"
```

---

## Before the first production run

These are from the spec's live-API checklist and need market hours, so they are not tasks:

1. Run `market-streamer` against the real key for a few minutes after 09:00 and confirm `0B` prices are integers and FID 20 is `HHMMSS` KST (`valkey-cli GET price:005930`).
2. Count distinct codes received: `valkey-cli --scan --pattern 'price:*' | wc -l` should reach 200 shortly after the open.
3. Production infrastructure outside this repo: the instance role needs `elasticache:Connect` on the serverless cache and its user, and the ALB target group points at host port `8001`.
4. Run `MGET`-free smoke: `GET /prices?codes=005930,000660` against the real cache returns both.
5. Security group admits host port 8001 only from the ALB security group.
6. prod `.env` defines `MARKET_HTTP_VALKEY_*` and `MARKET_STREAMER_VALKEY_*`/`KIWOOM_*` before the first deploy.
