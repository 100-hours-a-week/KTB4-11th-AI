# market-collector Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collect one year of 1-minute, 15-minute, 1-hour and daily candles for the KOSPI 200 from Kiwoom, compute eight technical indicators over regular-session candles, and store candles, indicators and daily theme data in QuestDB.

**Architecture:** A new `services/market-collector` owns everything except the QuestDB DDL, which lives in `infrastructure/questdb/` so that no service can create schema at boot. No timeframe is ever resampled from another: 15-minute, 1-hour and daily candles come from Kiwoom's chart endpoints, and 1-minute candles are aggregated from WebSocket trade ticks. Backfill pages backwards through `next-key` sequentially per symbol, parallel across five Kiwoom accounts, and persists a cursor per symbol and timeframe so an interrupted run resumes.

**Scope note — read this before following any task.** Tasks 1–15 are the plan **as it was written and executed**, and the code has since moved past them in ways the task text does not reflect. Where they disagree, the code and the spec are right and *Beyond this plan* at the end says why.

Three divergences matter most for anyone reading a task literally:

- **`universe/kospi200.csv` and its loader never survived.** The plan was written believing Kiwoom did not serve the KOSPI 200 constituent list. It does — `ka10101` with `mrkt_tp=2` carries `201`, and `ka20002` returns the constituents. Task 3's CSV is gone; `universe.py` fetches instead.
- **`universe/` is not a package.** It was collapsed into a single `universe.py`.
- **`theme_members.in_universe` does not exist.** Memberships are stored for the KOSPI 200 only, so the flag would be true on every row worth keeping.

Phase 1 also had no live path — every write went through REST. It has one now.

**Tech Stack:** Python 3.13 (CI also 3.14), uv workspace, `pydantic-settings`, `httpx`, `websockets`, `numpy`, `questdb` (ILP ingestion), `psycopg` (QuestDB reads over the Postgres wire protocol), `ktb-market-analyzer` (TA-Lib indicators), `ktb-core` (logging), pytest.

**Spec:** `docs/superpowers/specs/2026-09-22-market-collector-design.md`

## Global Constraints

- Python `>=3.13,<3.15`, matching every other workspace member.
- `packages/core` must not be modified. Import `ktb_core.logging.setup_logging` and nothing else from it.
- No service issues DDL. `CREATE TABLE` lives only in `infrastructure/questdb/schema/` and runs only through `infrastructure/questdb/apply.py`.
- Nothing in the test suite touches the network. Kiwoom is always a fake transport; QuestDB is never required for a unit test.
- All stored timestamps are timezone-aware UTC. Kiwoom returns KST (UTC+9, no daylight saving).
- Indicators are computed over `session='regular'` candles only. Extended-session rows carry OHLCV with null indicators.
- Candle history target is one year for every timeframe. Minute data cannot reach further back; daily is cut to match.
- Default inter-request interval is 1.3 seconds per Kiwoom account, the measured-safe value. `return_code=5` means the rate limit was exceeded and requires backoff.
- Minute-chart prices arrive sign-prefixed (`"+277500"`); daily prices do not (`"277500"`). The close is named `cur_prc` in both.
- Environment variables use the `MARKET_COLLECTOR_` prefix.
- Ruff line length 100, lint rules `E,F,I,UP,B`. `uv run ruff format` decides formatting.
- **`uv run ty check` must pass**, though CI no longer runs it. `dev` replaced `ci.yaml` with `ci-dev.yaml` and `ci-main.yaml` and dropped the type-check job along with the dependency-isolation and exported-requirements jobs; the tool is still pinned at `ty==0.0.82` in the dev group and this repository's code passes it. Run it with the `migrations` group synced or it reports two false `unresolved-import` errors for `alembic` and `sqlalchemy`.
- Before every commit run `uv run ruff check --fix . && uv run ruff format .`; the code blocks below may need import re-ordering or line wrapping.
- Commit messages are conventional-commit prefixed (`feat` / `fix` / `refactor` / `chore` / `docs`), in English, matching the existing history, and end with:
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`
- **Secrets:** never `cat`, print, log or commit the Kiwoom app or secret keys. Tests use fake values only; no step in this plan needs a real key.

---

## File Structure

**New, under `infrastructure/questdb/`:**

| File | Responsibility |
| --- | --- |
| `schema/bars.sql` | `CREATE TABLE IF NOT EXISTS` for `bars_1m`, `bars_15m`, `bars_1h`, `bars_1d` |
| `schema/themes.sql` | Same for `theme_snapshot` and `theme_members` |
| `apply.py` | Reads the `.sql` files and executes each statement against QuestDB |
| `tests/test_schema.py` | Asserts every table declares dedup keys and a partition clause |

**New, under `services/market-collector/`:**

| File | Responsibility |
| --- | --- |
| `pyproject.toml` | Package metadata, dependencies, `market-collector` console script |
| `src/market_collector/__init__.py` | Docstring only |
| `src/market_collector/settings.py` | `Settings`, including the five-account list |
| ~~`src/market_collector/universe/kospi200.csv`~~ | **Superseded.** Kiwoom serves the list; `universe.py` fetches it from `ka20002` |
| ~~`src/market_collector/universe/__init__.py`~~ | **Superseded** by a single `universe.py` |
| `src/market_collector/live.py` | *Added after Task 15.* WebSocket ticks into 1-minute candles |
| `src/market_collector/kiwoom/parse.py` | Sign-prefixed numbers, KST→UTC, session classification |
| `src/market_collector/kiwoom/auth.py` | Token issue and refresh, one per account |
| `src/market_collector/kiwoom/rest.py` | `ka10080`/`ka10081` with `cont-yn` paging and pacing |
| `src/market_collector/kiwoom/themes.py` | `ka90001`/`ka90002` |
| `src/market_collector/indicators.py` | Candle arrays → the eight indicator fields |
| `src/market_collector/store.py` | QuestDB ILP writes and Postgres-wire reads |
| `src/market_collector/cursor.py` | Per-symbol, per-timeframe backfill progress |
| `src/market_collector/backfill.py` | Orchestrates paging → indicators → store |
| `src/market_collector/themes.py` | Orchestrates the daily theme snapshot |
| `src/market_collector/__main__.py` | Subcommands `backfill`, `preopen`, `themes`; bare invocation validates and exits |

**Modified:**

| File | Change |
| --- | --- |
| `docker/market-collector.Dockerfile` | New, following `portfolio-builder.Dockerfile` |
| `docker/requirements/market-collector.txt` | New, generated by `uv export` |
| `.github/workflows/ci-dev.yaml` | Add `market-collector` to the `build-images` matrix |
| `.github/workflows/ci-main.yaml` | Add `market-collector` to the `build-and-push-images` matrix |
| `uv.lock` | Regenerated by `uv add` |

`parse.py` is separated from `rest.py` because the parsing quirks are where correctness is cheapest to pin down and they need no transport at all. `backfill.py` is separated from `rest.py` so that paging mechanics and collection policy can be tested apart.

---

### Task 1: Service scaffold and settings

**Files:**
- Create: `services/market-collector/pyproject.toml`
- Create: `services/market-collector/src/market_collector/__init__.py`
- Create: `services/market-collector/src/market_collector/settings.py`
- Create: `services/market-collector/src/market_collector/__main__.py`
- Test: `services/market-collector/tests/test_settings.py`
- Test: `services/market-collector/tests/test_main.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `market_collector.settings.Settings` with fields `log_level: str`, `questdb_dsn: str`, `questdb_ilp_host: str`, `questdb_ilp_port: int`, `kiwoom_accounts: list[KiwoomAccount]`, `request_interval: float`, `theme_date_tps: list[int]`, `cursor_path: str`. The spec also listed an indicator-window setting; it belonged to the live path and was deliberately absent here. The live path later shipped it as `MARKET_COLLECTOR_LIVE_WINDOW`. `KiwoomAccount` is a pydantic model with `app_key: str` and `secret_key: str`. `market_collector.__main__.main()` returns `None`.

- [ ] **Step 1: Write the failing settings test**

```python
# services/market-collector/tests/test_settings.py
import pytest
from market_collector.settings import Settings
from pydantic import ValidationError

QDB = "postgresql://admin:quest@localhost:8812/qdb"
ACCOUNTS = '[{"app_key":"k1","secret_key":"s1"},{"app_key":"k2","secret_key":"s2"}]'


def _populate(monkeypatch):
    monkeypatch.setenv("MARKET_COLLECTOR_QUESTDB_DSN", QDB)
    monkeypatch.setenv("MARKET_COLLECTOR_QUESTDB_ILP_HOST", "localhost")
    monkeypatch.setenv("MARKET_COLLECTOR_KIWOOM_ACCOUNTS", ACCOUNTS)


def test_loads_from_the_environment(monkeypatch):
    _populate(monkeypatch)

    settings = Settings()

    assert settings.questdb_dsn == QDB
    assert settings.questdb_ilp_host == "localhost"
    assert [a.app_key for a in settings.kiwoom_accounts] == ["k1", "k2"]


def test_defaults_match_the_measured_safe_values(monkeypatch):
    _populate(monkeypatch)

    settings = Settings()

    assert settings.log_level == "INFO"
    assert settings.questdb_ilp_port == 9009
    assert settings.request_interval == 1.3
    assert settings.theme_date_tps == [5, 20, 60]


@pytest.mark.parametrize(
    "missing",
    [
        "MARKET_COLLECTOR_QUESTDB_DSN",
        "MARKET_COLLECTOR_QUESTDB_ILP_HOST",
        "MARKET_COLLECTOR_KIWOOM_ACCOUNTS",
    ],
)
def test_every_required_field_is_required(monkeypatch, missing):
    _populate(monkeypatch)
    monkeypatch.delenv(missing)

    with pytest.raises(ValidationError):
        Settings()


def test_at_least_one_account_is_required(monkeypatch):
    _populate(monkeypatch)
    monkeypatch.setenv("MARKET_COLLECTOR_KIWOOM_ACCOUNTS", "[]")

    with pytest.raises(ValidationError):
        Settings()
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `uv run pytest services/market-collector/tests/test_settings.py -v`
Expected: collection error, `ModuleNotFoundError: No module named 'market_collector'`.

- [ ] **Step 3: Create the package metadata**

```toml
# services/market-collector/pyproject.toml
[project]
name = "market-collector"
version = "0.1.0"
description = "Kiwoom OHLCV and theme ingestion into QuestDB"
requires-python = ">=3.13,<3.15"
dependencies = [
    "ktb-core",
    "ktb-market-analyzer",
    "pydantic-settings>=2.7",
    "httpx>=0.28",
    "questdb>=5.0",
    "psycopg[binary]>=3.3.6",
]

[project.scripts]
market-collector = "market_collector.__main__:main"

[tool.uv.sources]
ktb-core = { workspace = true }
ktb-market-analyzer = { workspace = true }

[build-system]
requires = ["uv_build>=0.12,<0.13"]
build-backend = "uv_build"
```

- [ ] **Step 4: Write the module and settings**

```python
# services/market-collector/src/market_collector/__init__.py
"""Kiwoom OHLCV and theme ingestion into QuestDB."""
```

```python
# services/market-collector/src/market_collector/settings.py
"""Configuration for the market-collector."""

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class KiwoomAccount(BaseModel):
    app_key: str
    secret_key: str


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MARKET_COLLECTOR_",
        extra="ignore",
    )

    log_level: str = "INFO"
    questdb_dsn: str
    questdb_ilp_host: str
    questdb_ilp_port: int = 9009
    kiwoom_accounts: list[KiwoomAccount] = Field(min_length=1)
    request_interval: float = 1.3
    theme_date_tps: list[int] = [5, 20, 60]
    cursor_path: str = "var/market-collector/cursors.json"
```

```python
# services/market-collector/src/market_collector/__main__.py
"""Entry point for the market-collector."""

import logging
import sys

from ktb_core.logging import setup_logging

from market_collector.settings import Settings


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level)
    log = logging.getLogger(__name__)

    command = sys.argv[1] if len(sys.argv) > 1 else None
    if command is None:
        log.info("market-collector started")
        return

    log.error("unknown subcommand: %s", command)
    raise SystemExit(2)
```

- [ ] **Step 5: Install the new workspace member and run the tests**

Run: `uv sync --all-packages`
Run: `uv run pytest services/market-collector/tests/test_settings.py -v`
Expected: PASS, five tests.

- [ ] **Step 6: Write the entry-point test**

```python
# services/market-collector/tests/test_main.py
import json

import pytest
from market_collector.__main__ import main

QDB = "postgresql://admin:quest@localhost:8812/qdb"
ACCOUNTS = '[{"app_key":"k1","secret_key":"s1"}]'


def _populate(monkeypatch):
    monkeypatch.setenv("MARKET_COLLECTOR_QUESTDB_DSN", QDB)
    monkeypatch.setenv("MARKET_COLLECTOR_QUESTDB_ILP_HOST", "localhost")
    monkeypatch.setenv("MARKET_COLLECTOR_KIWOOM_ACCOUNTS", ACCOUNTS)


def test_bare_invocation_validates_and_exits(monkeypatch, capsys):
    _populate(monkeypatch)
    monkeypatch.setattr("sys.argv", ["market-collector"])

    main()

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["message"] == "market-collector started"


def test_unknown_subcommand_exits_nonzero(monkeypatch):
    _populate(monkeypatch)
    monkeypatch.setattr("sys.argv", ["market-collector", "nope"])

    with pytest.raises(SystemExit) as exc:
        main()

    assert exc.value.code == 2
```

- [ ] **Step 7: Run it**

Run: `uv run pytest services/market-collector/tests/ -v`
Expected: PASS, seven tests.

The bare invocation deliberately does nothing but validate settings, because CI runs every service image with `--network none` and expects exit 0. Real work happens behind subcommands.

- [ ] **Step 8: Commit**

```bash
git add services/market-collector uv.lock
git commit -m "feat: scaffold market-collector service and settings"
```

---

### Task 2: Kiwoom response parsing

**Files:**
- Create: `services/market-collector/src/market_collector/kiwoom/__init__.py`
- Create: `services/market-collector/src/market_collector/kiwoom/parse.py`
- Test: `services/market-collector/tests/test_parse.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `parse_price(raw: str) -> float`, `parse_volume(raw: str) -> int`, `parse_minute_ts(raw: str) -> datetime`, `parse_daily_ts(raw: str) -> datetime`, `classify_session(ts_utc: datetime) -> str`, `MinuteBar` and `DailyBar` frozen dataclasses with fields `ts: datetime`, `session: str`, `open: float`, `high: float`, `low: float`, `close: float`, `volume: int`, `trade_value: float | None`, and `parse_minute_bar(row: dict[str, str]) -> MinuteBar`, `parse_daily_bar(row: dict[str, str]) -> DailyBar`.

- [ ] **Step 1: Write the failing test**

```python
# services/market-collector/tests/test_parse.py
from datetime import UTC, datetime

import pytest
from market_collector.kiwoom.parse import (
    classify_session,
    parse_daily_bar,
    parse_daily_ts,
    parse_minute_bar,
    parse_minute_ts,
    parse_price,
    parse_volume,
)

# Verbatim responses recorded from the live API on 2026-09-22.
MINUTE_ROW = {
    "cur_prc": "+277500",
    "trde_qty": "38961",
    "cntr_tm": "20260922151900",
    "open_pric": "+277750",
    "high_pric": "+278000",
    "low_pric": "+277500",
    "acc_trde_qty": "15620193",
    "pred_pre": "+3500",
    "pred_pre_sig": "2",
}
DAILY_ROW = {
    "cur_prc": "277500",
    "trde_qty": "15620240",
    "trde_prica": "4366136",
    "dt": "20260922",
    "open_pric": "283000",
    "high_pric": "283500",
    "low_pric": "274500",
    "pred_pre": "+3500",
    "pred_pre_sig": "2",
    "trde_tern_rt": "+0.27",
}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("+277500", 277500.0), ("-27550", 27550.0), ("277500", 277500.0), ("0", 0.0)],
)
def test_price_ignores_the_sign_prefix(raw, expected):
    assert parse_price(raw) == expected


def test_price_rejects_an_empty_field():
    with pytest.raises(ValueError):
        parse_price("")


def test_volume_parses_a_plain_integer():
    assert parse_volume("38961") == 38961


def test_minute_timestamp_converts_kst_to_utc():
    assert parse_minute_ts("20260922151900") == datetime(2026, 9, 22, 6, 19, tzinfo=UTC)


def test_daily_timestamp_is_utc_midnight_of_the_trading_date():
    assert parse_daily_ts("20260922") == datetime(2026, 9, 22, 0, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("20260922090000", "regular"),
        ("20260922143000", "regular"),
        ("20260922153000", "regular"),
        ("20260922153100", "extended"),
        ("20260917170500", "extended"),
        ("20260922083500", "extended"),
    ],
)
def test_session_boundaries(raw, expected):
    assert classify_session(parse_minute_ts(raw)) == expected


def test_minute_bar_maps_cur_prc_to_close():
    bar = parse_minute_bar(MINUTE_ROW)

    assert bar.close == 277500.0
    assert bar.open == 277750.0
    assert bar.high == 278000.0
    assert bar.low == 277500.0
    assert bar.volume == 38961
    assert bar.ts == datetime(2026, 9, 22, 6, 19, tzinfo=UTC)
    assert bar.session == "regular"
    assert bar.trade_value is None


def test_daily_bar_carries_trade_value():
    bar = parse_daily_bar(DAILY_ROW)

    assert bar.close == 277500.0
    assert bar.open == 283000.0
    assert bar.volume == 15620240
    assert bar.trade_value == 4366136.0
    assert bar.ts == datetime(2026, 9, 22, 0, 0, tzinfo=UTC)
    assert bar.session == "regular"


def test_open_and_close_are_not_confused():
    bar = parse_minute_bar(MINUTE_ROW)

    assert bar.open != bar.close
```

The last test exists because `open_pric` and `cur_prc` are both prices on the same row and a transposed mapping would otherwise pass every other assertion.

- [ ] **Step 2: Run it and confirm it fails**

Run: `uv run pytest services/market-collector/tests/test_parse.py -v`
Expected: collection error, `ModuleNotFoundError: No module named 'market_collector.kiwoom'`.

- [ ] **Step 3: Write the implementation**

```python
# services/market-collector/src/market_collector/kiwoom/__init__.py
"""Kiwoom REST client pieces."""
```

```python
# services/market-collector/src/market_collector/kiwoom/parse.py
"""Turn Kiwoom's chart rows into typed candles.

Two quirks drive this module. Minute-chart prices arrive with a sign prefix
(``"+277500"``) while daily prices do not, and the close is named ``cur_prc`` in
both, which reads as "current price". Timestamps arrive in KST; everything
stored is UTC.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta, timezone

KST = timezone(timedelta(hours=9), "KST")

REGULAR_OPEN = time(9, 0)
REGULAR_CLOSE = time(15, 30)

__all__ = [
    "DailyBar",
    "MinuteBar",
    "classify_session",
    "parse_daily_bar",
    "parse_daily_ts",
    "parse_minute_bar",
    "parse_minute_ts",
    "parse_price",
    "parse_volume",
]


def parse_price(raw: str) -> float:
    text = raw.strip().lstrip("+-")
    if not text:
        raise ValueError(f"not a price: {raw!r}")
    return float(text)


def parse_volume(raw: str) -> int:
    text = raw.strip().lstrip("+-")
    if not text:
        raise ValueError(f"not a volume: {raw!r}")
    return int(text)


def parse_minute_ts(raw: str) -> datetime:
    naive = datetime.strptime(raw.strip(), "%Y%m%d%H%M%S")
    return naive.replace(tzinfo=KST).astimezone(UTC)


def parse_daily_ts(raw: str) -> datetime:
    naive = datetime.strptime(raw.strip(), "%Y%m%d")
    return naive.replace(tzinfo=UTC)


def classify_session(ts_utc: datetime) -> str:
    local = ts_utc.astimezone(KST).time()
    if REGULAR_OPEN <= local <= REGULAR_CLOSE:
        return "regular"
    return "extended"


@dataclass(frozen=True)
class MinuteBar:
    ts: datetime
    session: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    trade_value: float | None = None


@dataclass(frozen=True)
class DailyBar:
    ts: datetime
    session: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    trade_value: float | None = None


def parse_minute_bar(row: dict[str, str]) -> MinuteBar:
    ts = parse_minute_ts(row["cntr_tm"])
    return MinuteBar(
        ts=ts,
        session=classify_session(ts),
        open=parse_price(row["open_pric"]),
        high=parse_price(row["high_pric"]),
        low=parse_price(row["low_pric"]),
        close=parse_price(row["cur_prc"]),
        volume=parse_volume(row["trde_qty"]),
    )


def parse_daily_bar(row: dict[str, str]) -> DailyBar:
    ts = parse_daily_ts(row["dt"])
    return DailyBar(
        ts=ts,
        session="regular",
        open=parse_price(row["open_pric"]),
        high=parse_price(row["high_pric"]),
        low=parse_price(row["low_pric"]),
        close=parse_price(row["cur_prc"]),
        volume=parse_volume(row["trde_qty"]),
        trade_value=parse_price(row["trde_prica"]),
    )
```

`parse_daily_ts` deliberately returns UTC midnight of the trading date rather than converting 00:00 KST, so a daily row's partition matches the calendar date a reader expects.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest services/market-collector/tests/test_parse.py -v`
Expected: PASS, 17 tests.

- [ ] **Step 5: Commit**

```bash
git add services/market-collector
git commit -m "feat: parse Kiwoom chart rows into typed candles"
```

---

### Task 3: KOSPI 200 universe

**Files:**
- Create: `services/market-collector/src/market_collector/universe/__init__.py`
- Create: `services/market-collector/src/market_collector/universe/kospi200.csv`
- Test: `services/market-collector/tests/test_universe.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `load_kospi200() -> frozenset[str]` returning six-digit codes, and `load_from(path: Path) -> frozenset[str]`.

- [ ] **Step 1: Write the failing test**

```python
# services/market-collector/tests/test_universe.py
import pytest
from market_collector.universe import load_from, load_kospi200


def test_the_shipped_list_has_two_hundred_six_digit_codes():
    codes = load_kospi200()

    assert len(codes) == 200
    assert all(len(c) == 6 and c.isdigit() for c in codes)


def test_load_from_reads_a_two_column_csv(tmp_path):
    path = tmp_path / "u.csv"
    path.write_text("code,name\n005930,삼성전자\n000660,SK하이닉스\n", encoding="utf-8")

    assert load_from(path) == frozenset({"005930", "000660"})


def test_a_malformed_code_is_rejected(tmp_path):
    path = tmp_path / "u.csv"
    path.write_text("code,name\n5930,삼성전자\n", encoding="utf-8")

    with pytest.raises(ValueError, match="5930"):
        load_from(path)


def test_a_duplicate_code_is_rejected(tmp_path):
    path = tmp_path / "u.csv"
    path.write_text("code,name\n005930,삼성전자\n005930,삼성전자\n", encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate"):
        load_from(path)
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `uv run pytest services/market-collector/tests/test_universe.py -v`
Expected: collection error, `ModuleNotFoundError: No module named 'market_collector.universe'`.

- [ ] **Step 3: Obtain the constituent list**

> **Deferred.** The KRX export is not available yet, so ship the loader and a
> placeholder CSV holding only the header line, and skip the 200-count assertion.
> Mark the first test with
> `@pytest.mark.skip(reason="awaiting the KRX KOSPI 200 export")`. Every other task
> depends on `load_kospi200()` existing, not on its contents. Remove the skip and
> drop in the real CSV when the export arrives; the assertion is what proves it
> landed intact.

Kiwoom does not serve KOSPI 200 membership. `ka10099` returns all 2,486 KOSPI-listed stocks with no index field, and `ka10101`'s 31 sector codes contain 대형주/중형주/소형주, 코스피고배당50 and 코스피배당성장50 but no KOSPI 200. Approximating from `upSizeName` is wrong, because the 200 are selected from 300 size-class candidates by further rules.

Get the list from KRX 정보데이터시스템 (`http://data.krx.co.kr`) → 기본통계 → 지수 → 주가지수 → 지수구성종목, selecting 코스피 200, and export it. Save it as exactly two columns with a header:

```csv
code,name
005930,삼성전자
000660,SK하이닉스
```

Codes must be zero-padded to six digits — spreadsheet exports routinely strip the leading zero from `005930`, and the test in Step 1 fails loudly when that happens, which is the point.

- [ ] **Step 4: Write the loader**

```python
# services/market-collector/src/market_collector/universe/__init__.py
"""The KOSPI 200 constituent list.

Kiwoom exposes no index-membership endpoint, so this ships as a file reviewed
by hand. Constituents change twice a year.
"""

import csv
from pathlib import Path

__all__ = ["load_from", "load_kospi200"]

_DEFAULT = Path(__file__).with_name("kospi200.csv")


def load_from(path: Path) -> frozenset[str]:
    codes: list[str] = []
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            code = (row.get("code") or "").strip()
            if len(code) != 6 or not code.isdigit():
                raise ValueError(f"not a six-digit stock code: {code!r}")
            codes.append(code)

    seen = set()
    for code in codes:
        if code in seen:
            raise ValueError(f"duplicate stock code: {code}")
        seen.add(code)

    return frozenset(seen)


def load_kospi200() -> frozenset[str]:
    return load_from(_DEFAULT)
```

- [ ] **Step 5: Make the CSV reachable from the built package**

`uv_build` ships only `.py` files by default. Add the data file to the package in `services/market-collector/pyproject.toml`, immediately after the `[build-system]` block:

```toml
[tool.uv.build-backend]
source-include = ["src/market_collector/universe/kospi200.csv"]
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest services/market-collector/tests/test_universe.py -v`
Expected: PASS, four tests. If the first test fails on the count, the CSV export is incomplete — fix the file, not the test.

- [ ] **Step 7: Commit**

```bash
git add services/market-collector
git commit -m "feat: ship the KOSPI 200 constituent list with a validating loader"
```

---

### Task 4: Kiwoom authentication

**Files:**
- Create: `services/market-collector/src/market_collector/kiwoom/auth.py`
- Test: `services/market-collector/tests/test_auth.py`

**Interfaces:**
- Consumes: `market_collector.settings.KiwoomAccount`.
- Produces: `KiwoomAuthError(Exception)`, `TokenStore` with `__init__(self, account: KiwoomAccount, transport: Transport, now: Callable[[], datetime] = ...)` and `token(self) -> str`. `Transport` is a `Protocol` with `post(self, path: str, body: dict[str, object], headers: dict[str, str]) -> tuple[dict[str, str], dict[str, object]]` returning response headers and the JSON body.

- [ ] **Step 1: Write the failing test**

```python
# services/market-collector/tests/test_auth.py
from datetime import UTC, datetime, timedelta

import pytest
from market_collector.kiwoom.auth import KiwoomAuthError, TokenStore
from market_collector.settings import KiwoomAccount

ACCOUNT = KiwoomAccount(app_key="k", secret_key="s")


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, path, body, headers):
        self.calls.append((path, body, headers))
        return {}, self.responses.pop(0)


def _ok(token, expires):
    return {
        "return_code": 0,
        "return_msg": "정상적으로 처리되었습니다",
        "token": token,
        "token_type": "Bearer",
        "expires_dt": expires,
    }


def test_issues_a_token_and_sends_the_credentials():
    transport = FakeTransport(_ok("t1", "20260923000000"))
    store = TokenStore(ACCOUNT, transport, now=lambda: datetime(2026, 9, 22, 6, 0, tzinfo=UTC))

    assert store.token() == "t1"
    path, body, _ = transport.calls[0]
    assert path == "/oauth2/token"
    assert body == {"grant_type": "client_credentials", "appkey": "k", "secretkey": "s"}


def test_reuses_the_token_until_it_nears_expiry():
    transport = FakeTransport(_ok("t1", "20260923000000"))
    store = TokenStore(ACCOUNT, transport, now=lambda: datetime(2026, 9, 22, 6, 0, tzinfo=UTC))

    assert store.token() == "t1"
    assert store.token() == "t1"
    assert len(transport.calls) == 1


def test_refreshes_before_expiry_rather_than_after():
    clock = [datetime(2026, 9, 22, 6, 0, tzinfo=UTC)]
    transport = FakeTransport(_ok("t1", "20260922160000"), _ok("t2", "20260923160000"))
    store = TokenStore(ACCOUNT, transport, now=lambda: clock[0])

    assert store.token() == "t1"
    # 15:55 KST is 06:55 UTC; the token expires at 16:00 KST, inside the margin.
    clock[0] = datetime(2026, 9, 22, 6, 55, tzinfo=UTC)

    assert store.token() == "t2"
    assert len(transport.calls) == 2


def test_an_unregistered_ip_fails_immediately_and_says_so():
    body = {
        "return_code": 3,
        "return_msg": "인증에 실패했습니다[8050:IP가 등록되지 않았습니다.]",
    }
    store = TokenStore(ACCOUNT, FakeTransport(body))

    with pytest.raises(KiwoomAuthError, match="8050"):
        store.token()


def test_a_wrong_environment_key_fails_immediately():
    body = {
        "return_code": 2,
        "return_msg": "입력 값 오류입니다[8030:투자구분(실전/모의)이 달라서]",
    }
    store = TokenStore(ACCOUNT, FakeTransport(body))

    with pytest.raises(KiwoomAuthError, match="8030"):
        store.token()


def test_expiry_margin_is_five_minutes():
    assert TokenStore.REFRESH_MARGIN == timedelta(minutes=5)
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `uv run pytest services/market-collector/tests/test_auth.py -v`
Expected: collection error, no module named `market_collector.kiwoom.auth`.

- [ ] **Step 3: Write the implementation**

```python
# services/market-collector/src/market_collector/kiwoom/auth.py
"""Access-token lifecycle, one store per Kiwoom account.

Token failures are configuration failures, not transient ones: return_code 3
means this machine's IP is not allowlisted and return_code 2 means a live key
was used against the mock host or the reverse. Retrying either one only hides
it, so both raise.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol

from market_collector.kiwoom.parse import KST
from market_collector.settings import KiwoomAccount

__all__ = ["KiwoomAuthError", "TokenStore", "Transport"]


class KiwoomAuthError(Exception):
    pass


class Transport(Protocol):
    def post(
        self, path: str, body: dict[str, object], headers: dict[str, str]
    ) -> tuple[dict[str, str], dict[str, object]]: ...


def _utcnow() -> datetime:
    return datetime.now(UTC)


class TokenStore:
    REFRESH_MARGIN = timedelta(minutes=5)

    def __init__(
        self,
        account: KiwoomAccount,
        transport: Transport,
        now: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._account = account
        self._transport = transport
        self._now = now
        self._token: str | None = None
        self._expires_at: datetime | None = None

    def token(self) -> str:
        if self._token is not None and self._expires_at is not None:
            if self._now() + self.REFRESH_MARGIN < self._expires_at:
                return self._token
        return self._issue()

    def _issue(self) -> str:
        _, body = self._transport.post(
            "/oauth2/token",
            {
                "grant_type": "client_credentials",
                "appkey": self._account.app_key,
                "secretkey": self._account.secret_key,
            },
            {},
        )
        if body.get("return_code") != 0:
            raise KiwoomAuthError(
                f"token request failed: return_code={body.get('return_code')} "
                f"return_msg={body.get('return_msg')}"
            )

        token = body.get("token")
        if not isinstance(token, str) or not token:
            raise KiwoomAuthError(f"token missing from response: {body!r}")

        self._token = token
        self._expires_at = self._parse_expiry(body.get("expires_dt"))
        return token

    @staticmethod
    def _parse_expiry(raw: object) -> datetime:
        if not isinstance(raw, str) or len(raw) != 14:
            raise KiwoomAuthError(f"unusable expires_dt: {raw!r}")
        naive = datetime.strptime(raw, "%Y%m%d%H%M%S")
        return naive.replace(tzinfo=KST).astimezone(UTC)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest services/market-collector/tests/test_auth.py -v`
Expected: PASS, six tests.

- [ ] **Step 5: Commit**

```bash
git add services/market-collector
git commit -m "feat: manage Kiwoom access tokens per account"
```

---

### Task 5: Chart paging client

**Files:**
- Create: `services/market-collector/src/market_collector/kiwoom/rest.py`
- Test: `services/market-collector/tests/test_rest.py`

**Interfaces:**
- Consumes: `TokenStore`, `Transport` from `market_collector.kiwoom.auth`.
- Produces: `KiwoomRateLimited(Exception)`, `KiwoomRequestError(Exception)`, `Page` (`NamedTuple` with `rows: list[dict[str, str]]`, `next_key: str | None`, `has_more: bool`), `HttpxTransport(base_url: str)`, and `ChartClient` with `__init__(self, tokens: TokenStore, transport: Transport, interval: float = 1.3, sleep: Callable[[float], None] = time.sleep)`, `minute_page(self, symbol: str, tic_scope: int, next_key: str | None = None) -> Page` and `daily_page(self, symbol: str, base_dt: str, next_key: str | None = None) -> Page`.

- [ ] **Step 1: Write the failing test**

```python
# services/market-collector/tests/test_rest.py
import pytest
from market_collector.kiwoom.auth import TokenStore
from market_collector.kiwoom.rest import ChartClient, KiwoomRateLimited, KiwoomRequestError
from market_collector.settings import KiwoomAccount

ACCOUNT = KiwoomAccount(app_key="k", secret_key="s")
TOKEN_OK = {
    "return_code": 0,
    "token": "t1",
    "token_type": "Bearer",
    "expires_dt": "20270101000000",
}
MINUTE_ROW = {
    "cur_prc": "+277500",
    "trde_qty": "38961",
    "cntr_tm": "20260922151900",
    "open_pric": "+277750",
    "high_pric": "+278000",
    "low_pric": "+277500",
}


class FakeTransport:
    """Replays scripted (headers, body) pairs and records every call."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, path, body, headers):
        self.calls.append((path, dict(body), dict(headers)))
        return self.responses.pop(0)


def _client(*chart_responses, interval=1.3):
    transport = FakeTransport(({}, TOKEN_OK), *chart_responses)
    slept = []
    client = ChartClient(
        TokenStore(ACCOUNT, transport),
        transport,
        interval=interval,
        sleep=slept.append,
    )
    return client, transport, slept


def test_minute_page_sends_the_documented_parameters():
    body = {"return_code": 0, "stk_min_pole_chart_qry": [MINUTE_ROW]}
    client, transport, _ = _client(({"cont-yn": "N"}, body))

    page = client.minute_page("005930", 15)

    assert len(page.rows) == 1
    path, sent, headers = transport.calls[1]
    assert path == "/api/dostk/chart"
    assert sent == {"stk_cd": "005930", "tic_scope": "15", "upd_stkpc_tp": "1"}
    assert headers["api-id"] == "ka10080"
    assert headers["authorization"] == "Bearer t1"


def test_continuation_headers_are_echoed_back_on_the_next_call():
    first = ({"cont-yn": "Y", "next-key": "NK1"}, {"return_code": 0, "stk_min_pole_chart_qry": [MINUTE_ROW]})
    second = ({"cont-yn": "N"}, {"return_code": 0, "stk_min_pole_chart_qry": [MINUTE_ROW]})
    client, transport, _ = _client(first, second)

    page1 = client.minute_page("005930", 1)
    assert page1.has_more is True
    assert page1.next_key == "NK1"

    page2 = client.minute_page("005930", 1, next_key=page1.next_key)
    assert page2.has_more is False
    _, _, headers = transport.calls[2]
    assert headers["cont-yn"] == "Y"
    assert headers["next-key"] == "NK1"


def test_the_first_call_does_not_sleep_but_the_second_does():
    body = {"return_code": 0, "stk_min_pole_chart_qry": [MINUTE_ROW]}
    client, _, slept = _client(({"cont-yn": "N"}, body), ({"cont-yn": "N"}, body))

    client.minute_page("005930", 1)
    assert slept == []

    client.minute_page("005930", 1)
    assert slept == [1.3]


def test_daily_page_uses_the_other_api_id_and_array():
    row = {
        "dt": "20260922",
        "cur_prc": "277500",
        "open_pric": "283000",
        "high_pric": "283500",
        "low_pric": "274500",
        "trde_qty": "15620240",
        "trde_prica": "4366136",
    }
    body = {"return_code": 0, "stk_dt_pole_chart_qry": [row]}
    client, transport, _ = _client(({"cont-yn": "N"}, body))

    page = client.daily_page("005930", "20260922")

    assert page.rows == [row]
    _, sent, headers = transport.calls[1]
    assert headers["api-id"] == "ka10081"
    assert sent == {"stk_cd": "005930", "base_dt": "20260922", "upd_stkpc_tp": "1"}


def test_rate_limiting_raises_its_own_error():
    body = {"return_code": 5, "return_msg": "허용된 요청 개수를 초과하였습니다"}
    client, _, _ = _client(({}, body))

    with pytest.raises(KiwoomRateLimited):
        client.minute_page("005930", 1)


def test_any_other_error_code_raises_the_generic_error():
    body = {"return_code": 2, "return_msg": "입력 값 오류입니다"}
    client, _, _ = _client(({}, body))

    with pytest.raises(KiwoomRequestError, match="return_code=2"):
        client.minute_page("005930", 1)


def test_an_exhausted_history_is_an_empty_page_not_an_error():
    body = {"return_code": 0, "stk_min_pole_chart_qry": []}
    client, _, _ = _client(({"cont-yn": "N"}, body))

    page = client.minute_page("005930", 1, next_key="NK9")

    assert page.rows == []
    assert page.has_more is False
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `uv run pytest services/market-collector/tests/test_rest.py -v`
Expected: collection error, no module named `market_collector.kiwoom.rest`.

- [ ] **Step 3: Write the implementation**

```python
# services/market-collector/src/market_collector/kiwoom/rest.py
"""Paging client for Kiwoom's chart endpoints.

Both endpoints are POST /api/dostk/chart and are told apart by the ``api-id``
header. Continuation is carried in response headers, not the body, and is
echoed back on the following request. Measured page sizes are 900 records for
ka10080 and 600 for ka10081, and history can only be walked backwards — there
is no date-jump parameter on ka10080, so ``dt`` is not sent.
"""

import time
from collections.abc import Callable
from typing import Any, NamedTuple

import httpx

from market_collector.kiwoom.auth import TokenStore, Transport

__all__ = [
    "ChartClient",
    "HttpxTransport",
    "KiwoomRateLimited",
    "KiwoomRequestError",
    "Page",
]

CHART_PATH = "/api/dostk/chart"
MINUTE_API_ID = "ka10080"
DAILY_API_ID = "ka10081"
MINUTE_ARRAY = "stk_min_pole_chart_qry"
DAILY_ARRAY = "stk_dt_pole_chart_qry"
RATE_LIMITED = 5


class KiwoomRateLimited(Exception):
    pass


class KiwoomRequestError(Exception):
    pass


class Page(NamedTuple):
    rows: list[dict[str, str]]
    next_key: str | None
    has_more: bool


class HttpxTransport:
    def __init__(self, base_url: str = "https://api.kiwoom.com") -> None:
        self._client = httpx.Client(base_url=base_url, timeout=30.0)

    def post(
        self, path: str, body: dict[str, object], headers: dict[str, str]
    ) -> tuple[dict[str, str], dict[str, object]]:
        response = self._client.post(
            path,
            json=body,
            headers={"Content-Type": "application/json;charset=UTF-8", **headers},
        )
        response.raise_for_status()
        return dict(response.headers), response.json()

    def close(self) -> None:
        self._client.close()


class ChartClient:
    def __init__(
        self,
        tokens: TokenStore,
        transport: Transport,
        interval: float = 1.3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._tokens = tokens
        self._transport = transport
        self._interval = interval
        self._sleep = sleep
        self._paced = False

    def minute_page(self, symbol: str, tic_scope: int, next_key: str | None = None) -> Page:
        return self._page(
            MINUTE_API_ID,
            MINUTE_ARRAY,
            {"stk_cd": symbol, "tic_scope": str(tic_scope), "upd_stkpc_tp": "1"},
            next_key,
        )

    def daily_page(self, symbol: str, base_dt: str, next_key: str | None = None) -> Page:
        return self._page(
            DAILY_API_ID,
            DAILY_ARRAY,
            {"stk_cd": symbol, "base_dt": base_dt, "upd_stkpc_tp": "1"},
            next_key,
        )

    def _page(
        self,
        api_id: str,
        array_field: str,
        body: dict[str, object],
        next_key: str | None,
    ) -> Page:
        if self._paced:
            self._sleep(self._interval)
        self._paced = True

        headers = {"authorization": f"Bearer {self._tokens.token()}", "api-id": api_id}
        if next_key:
            headers["cont-yn"] = "Y"
            headers["next-key"] = next_key

        response_headers, payload = self._transport.post(CHART_PATH, body, headers)
        code = payload.get("return_code")
        if code == RATE_LIMITED:
            raise KiwoomRateLimited(str(payload.get("return_msg")))
        if code != 0:
            raise KiwoomRequestError(
                f"return_code={code} return_msg={payload.get('return_msg')}"
            )

        rows = self._rows(payload, array_field)
        cont = response_headers.get("cont-yn")
        returned_key = response_headers.get("next-key") or None
        has_more = bool(rows) and cont == "Y" and returned_key is not None
        return Page(rows=rows, next_key=returned_key, has_more=has_more)

    @staticmethod
    def _rows(payload: dict[str, Any], array_field: str) -> list[dict[str, str]]:
        raw = payload.get(array_field)
        if raw is None:
            return []
        if not isinstance(raw, list):
            raise KiwoomRequestError(f"{array_field} is not a list: {type(raw)!r}")
        return raw
```

Pacing is applied *before* every request after the first, so a caller that loops never has to remember to sleep and the very first request is not delayed. `has_more` requires all three of a non-empty page, `cont-yn: Y` and a `next-key`, because an exhausted history is signalled by `cont-yn` flipping to `N` and must terminate the loop rather than raise.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest services/market-collector/tests/test_rest.py -v`
Expected: PASS, seven tests.

- [ ] **Step 5: Commit**

```bash
git add services/market-collector
git commit -m "feat: page Kiwoom chart endpoints with continuation headers"
```

---

### Task 6: Theme client

**Files:**
- Create: `services/market-collector/src/market_collector/kiwoom/themes.py`
- Test: `services/market-collector/tests/test_kiwoom_themes.py`

**Interfaces:**
- Consumes: `TokenStore`, `Transport`, `Page`, `KiwoomRateLimited`, `KiwoomRequestError`.
- Produces: `ThemeGroup` frozen dataclass with `code: str`, `name: str`, `date_tp: int`, `dt_prft_rt: float | None`, `change_rate: float | None`, `stock_count: int`, `rising_count: int`, `falling_count: int`, `main_stocks: str`; `ThemeMember` frozen dataclass with `theme_code: str`, `symbol: str`, `stock_name: str`; `ThemeClient` with `__init__(self, tokens, transport, interval=1.3, sleep=time.sleep)`, `groups(self, date_tp: int) -> list[ThemeGroup]` (pages internally until exhausted) and `members(self, theme_code: str, date_tp: int) -> list[ThemeMember]`.

- [ ] **Step 1: Write the failing test**

```python
# services/market-collector/tests/test_kiwoom_themes.py
from market_collector.kiwoom.auth import TokenStore
from market_collector.kiwoom.themes import ThemeClient
from market_collector.settings import KiwoomAccount

ACCOUNT = KiwoomAccount(app_key="k", secret_key="s")
TOKEN_OK = {"return_code": 0, "token": "t1", "token_type": "Bearer",
            "expires_dt": "20270101000000"}

# Verbatim from the live API on 2026-09-22.
GROUP = {
    "thema_grp_cd": "103",
    "thema_nm": "태양광_발전/설치/운영",
    "stk_num": "3",
    "flu_sig": "5",
    "flu_rt": "-1.20",
    "rising_stk_num": "1",
    "fall_stk_num": "2",
    "dt_prft_rt": "+297.10",
    "main_stk": "에스에너지, 한화솔루션",
}
MEMBER = {
    "stk_cd": "009830",
    "stk_nm": "한화솔루션",
    "cur_prc": "-27550",
    "flu_rt": "-0.18",
    "acc_trde_qty": "949407",
}


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, path, body, headers):
        self.calls.append((path, dict(body), dict(headers)))
        return self.responses.pop(0)


def _client(*responses):
    transport = FakeTransport(({}, TOKEN_OK), *responses)
    client = ThemeClient(TokenStore(ACCOUNT, transport), transport, sleep=lambda _: None)
    return client, transport


def test_groups_parses_a_single_page():
    body = {"return_code": 0, "thema_grp": [GROUP]}
    client, transport = _client(({"cont-yn": "N"}, body))

    groups = client.groups(date_tp=10)

    assert len(groups) == 1
    group = groups[0]
    assert group.code == "103"
    assert group.name == "태양광_발전/설치/운영"
    assert group.date_tp == 10
    assert group.dt_prft_rt == 297.10
    assert group.change_rate == -1.20
    assert group.stock_count == 3
    assert group.rising_count == 1
    assert group.falling_count == 2
    assert group.main_stocks == "에스에너지, 한화솔루션"

    path, sent, headers = transport.calls[1]
    assert path == "/api/dostk/thme"
    assert headers["api-id"] == "ka90001"
    assert sent["date_tp"] == "10"
    assert sent["qry_tp"] == "0"
    assert sent["stex_tp"] == "1"


def test_change_rate_keeps_its_sign_but_dt_prft_rt_does_too():
    body = {"return_code": 0, "thema_grp": [{**GROUP, "flu_rt": "+1.20",
                                             "dt_prft_rt": "-12.50"}]}
    client, _ = _client(({"cont-yn": "N"}, body))

    group = client.groups(date_tp=10)[0]

    assert group.change_rate == 1.20
    assert group.dt_prft_rt == -12.50


def test_groups_follows_continuation_to_the_end():
    page1 = ({"cont-yn": "Y", "next-key": "NK1"},
             {"return_code": 0, "thema_grp": [GROUP]})
    page2 = ({"cont-yn": "N"},
             {"return_code": 0, "thema_grp": [{**GROUP, "thema_grp_cd": "552"}]})
    client, transport = _client(page1, page2)

    groups = client.groups(date_tp=10)

    assert [g.code for g in groups] == ["103", "552"]
    assert transport.calls[2][2]["next-key"] == "NK1"


def test_members_parses_and_tags_the_theme_code():
    body = {"return_code": 0, "thema_comp_stk": [MEMBER]}
    client, transport = _client(({"cont-yn": "N"}, body))

    members = client.members("557", date_tp=10)

    assert len(members) == 1
    assert members[0].theme_code == "557"
    assert members[0].symbol == "009830"
    assert members[0].stock_name == "한화솔루션"

    _, sent, headers = transport.calls[1]
    assert headers["api-id"] == "ka90002"
    assert sent == {"date_tp": "10", "thema_grp_cd": "557", "stex_tp": "1"}


def test_an_unparseable_rate_becomes_none_rather_than_raising():
    body = {"return_code": 0, "thema_grp": [{**GROUP, "dt_prft_rt": ""}]}
    client, _ = _client(({"cont-yn": "N"}, body))

    assert client.groups(date_tp=10)[0].dt_prft_rt is None
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `uv run pytest services/market-collector/tests/test_kiwoom_themes.py -v`
Expected: collection error, no module named `market_collector.kiwoom.themes`.

- [ ] **Step 3: Write the implementation**

```python
# services/market-collector/src/market_collector/kiwoom/themes.py
"""Theme groups and their constituents.

Measured on 2026-09-22: ka90001 returns 100 groups per page and 142 in total.
Results are ordered by dt_prft_rt, so which groups land on page one depends on
date_tp — the caller must page to the end to see every theme.

dt_prft_rt is kept under its upstream name because its semantics are
unconfirmed: it reads +299.34 at date_tp=3 and +68.45 at date_tp=120 for the
same theme, which no plain N-day return explains.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass

from market_collector.kiwoom.auth import TokenStore, Transport
from market_collector.kiwoom.rest import KiwoomRateLimited, KiwoomRequestError

__all__ = ["ThemeClient", "ThemeGroup", "ThemeMember"]

THEME_PATH = "/api/dostk/thme"
GROUPS_API_ID = "ka90001"
MEMBERS_API_ID = "ka90002"
RATE_LIMITED = 5


def _rate(raw: object) -> float | None:
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text or text in {"+", "-"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _count(raw: object) -> int:
    if not isinstance(raw, str) or not raw.strip():
        return 0
    return int(raw.strip())


@dataclass(frozen=True)
class ThemeGroup:
    code: str
    name: str
    date_tp: int
    dt_prft_rt: float | None
    change_rate: float | None
    stock_count: int
    rising_count: int
    falling_count: int
    main_stocks: str


@dataclass(frozen=True)
class ThemeMember:
    theme_code: str
    symbol: str
    stock_name: str


class ThemeClient:
    def __init__(
        self,
        tokens: TokenStore,
        transport: Transport,
        interval: float = 1.3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._tokens = tokens
        self._transport = transport
        self._interval = interval
        self._sleep = sleep
        self._paced = False

    def groups(self, date_tp: int) -> list[ThemeGroup]:
        body = {
            "qry_tp": "0",
            "stk_cd": "",
            "thema_nm": "",
            "date_tp": str(date_tp),
            "flu_pl_amt_tp": "1",
            "stex_tp": "1",
        }
        collected: list[ThemeGroup] = []
        next_key: str | None = None
        while True:
            rows, next_key, has_more = self._call(GROUPS_API_ID, body, "thema_grp", next_key)
            collected.extend(
                ThemeGroup(
                    code=row["thema_grp_cd"],
                    name=row["thema_nm"],
                    date_tp=date_tp,
                    dt_prft_rt=_rate(row.get("dt_prft_rt")),
                    change_rate=_rate(row.get("flu_rt")),
                    stock_count=_count(row.get("stk_num")),
                    rising_count=_count(row.get("rising_stk_num")),
                    falling_count=_count(row.get("fall_stk_num")),
                    main_stocks=row.get("main_stk", ""),
                )
                for row in rows
            )
            if not has_more:
                return collected

    def members(self, theme_code: str, date_tp: int) -> list[ThemeMember]:
        body = {"date_tp": str(date_tp), "thema_grp_cd": theme_code, "stex_tp": "1"}
        collected: list[ThemeMember] = []
        next_key: str | None = None
        while True:
            rows, next_key, has_more = self._call(
                MEMBERS_API_ID, body, "thema_comp_stk", next_key
            )
            collected.extend(
                ThemeMember(
                    theme_code=theme_code,
                    symbol=row["stk_cd"],
                    stock_name=row.get("stk_nm", ""),
                )
                for row in rows
            )
            if not has_more:
                return collected

    def _call(
        self, api_id: str, body: dict[str, object], array_field: str, next_key: str | None
    ) -> tuple[list[dict[str, str]], str | None, bool]:
        if self._paced:
            self._sleep(self._interval)
        self._paced = True

        headers = {"authorization": f"Bearer {self._tokens.token()}", "api-id": api_id}
        if next_key:
            headers["cont-yn"] = "Y"
            headers["next-key"] = next_key

        response_headers, payload = self._transport.post(THEME_PATH, body, headers)
        code = payload.get("return_code")
        if code == RATE_LIMITED:
            raise KiwoomRateLimited(str(payload.get("return_msg")))
        if code != 0:
            raise KiwoomRequestError(
                f"return_code={code} return_msg={payload.get('return_msg')}"
            )

        rows = payload.get(array_field) or []
        if not isinstance(rows, list):
            raise KiwoomRequestError(f"{array_field} is not a list")
        returned_key = response_headers.get("next-key") or None
        has_more = bool(rows) and response_headers.get("cont-yn") == "Y" and returned_key is not None
        return rows, returned_key, has_more
```

`_rate` returns `None` instead of raising on an unparseable figure because a single malformed theme must not abort a 142-theme snapshot; the column is nullable for exactly this reason.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest services/market-collector/tests/test_kiwoom_themes.py -v`
Expected: PASS, five tests.

- [ ] **Step 5: Commit**

```bash
git add services/market-collector
git commit -m "feat: collect Kiwoom theme groups and constituents"
```

---

### Task 7: QuestDB schema and its apply job

**Files:**
- Create: `infrastructure/questdb/schema/bars.sql`
- Create: `infrastructure/questdb/schema/themes.sql`
- Create: `infrastructure/questdb/apply.py`
- Test: `infrastructure/questdb/tests/test_schema.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `statements(sql: str) -> list[str]`, `schema_files() -> list[Path]`, `apply(dsn: str) -> int` returning the number of statements executed. The six table names and their column lists become the contract that Task 9's writer targets.

- [ ] **Step 1: Write the failing test**

```python
# infrastructure/questdb/tests/test_schema.py
import importlib.util
import pathlib
import re

import pytest

REPO_ROOT = next(
    parent
    for parent in pathlib.Path(__file__).resolve().parents
    if (parent / "alembic.ini").is_file()
)
QUESTDB_DIR = REPO_ROOT / "infrastructure" / "questdb"

_spec = importlib.util.spec_from_file_location("questdb_apply", QUESTDB_DIR / "apply.py")
apply_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(apply_mod)

BAR_TABLES = ["bars_1m", "bars_15m", "bars_1h", "bars_1d"]
THEME_TABLES = ["theme_snapshot", "theme_members"]
INDICATORS = [
    "rsi",
    "macd",
    "macd_signal",
    "macd_histogram",
    "stochastic_k",
    "stochastic_d",
    "roc",
    "williams_r",
]


def _all_sql() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in apply_mod.schema_files())


def _statement_for(table: str) -> str:
    for statement in apply_mod.statements(_all_sql()):
        if re.search(rf"CREATE TABLE IF NOT EXISTS\s+{table}\b", statement):
            return statement
    raise AssertionError(f"no CREATE TABLE for {table}")


def test_every_expected_table_is_declared():
    for table in BAR_TABLES + THEME_TABLES:
        assert _statement_for(table)


def test_every_table_is_idempotent_walled_and_deduplicated():
    for table in BAR_TABLES + THEME_TABLES:
        statement = _statement_for(table)
        assert "IF NOT EXISTS" in statement
        assert "WAL" in statement
        assert "DEDUP UPSERT KEYS" in statement
        assert "PARTITION BY" in statement


def test_candle_tables_dedup_on_timestamp_and_symbol():
    for table in BAR_TABLES:
        statement = _statement_for(table)
        keys = re.search(r"DEDUP UPSERT KEYS\s*\(([^)]*)\)", statement).group(1)
        assert [k.strip() for k in keys.split(",")] == ["ts", "symbol"]


def test_theme_tables_dedup_on_their_own_keys():
    snapshot = re.search(
        r"DEDUP UPSERT KEYS\s*\(([^)]*)\)", _statement_for("theme_snapshot")
    ).group(1)
    assert [k.strip() for k in snapshot.split(",")] == ["ts", "theme_code", "date_tp"]

    members = re.search(
        r"DEDUP UPSERT KEYS\s*\(([^)]*)\)", _statement_for("theme_members")
    ).group(1)
    assert [k.strip() for k in members.split(",")] == ["ts", "theme_code", "symbol"]


def test_candle_tables_carry_every_indicator_column():
    for table in BAR_TABLES:
        statement = _statement_for(table)
        for column in INDICATORS:
            assert re.search(rf"\b{column}\s+DOUBLE", statement), f"{table}.{column}"


def test_partition_granularity_matches_candle_density():
    assert "PARTITION BY DAY" in _statement_for("bars_1m")
    assert "PARTITION BY MONTH" in _statement_for("bars_15m")
    assert "PARTITION BY MONTH" in _statement_for("bars_1h")
    assert "PARTITION BY YEAR" in _statement_for("bars_1d")


def test_statements_splits_and_drops_comments_and_blanks():
    sql = """
    -- a comment
    CREATE TABLE a (x INT);

    -- another
    CREATE TABLE b (y INT);
    """

    assert apply_mod.statements(sql) == ["CREATE TABLE a (x INT)", "CREATE TABLE b (y INT)"]


def test_no_dsn_is_baked_into_the_schema():
    assert "postgresql://" not in _all_sql()


def test_the_dsn_is_read_from_the_environment(monkeypatch):
    assert apply_mod.DSN_ENV == "KTB_QUESTDB_DSN"

    monkeypatch.delenv(apply_mod.DSN_ENV, raising=False)
    with pytest.raises(SystemExit):
        apply_mod.main()
```

`apply.py`'s docstring carries an example DSN, exactly as
`infrastructure/postgres/migrations/env.py` already does, so a substring ban on
`postgresql://` in that file would fail on documentation. What matters is that no
connection is hardcoded, which is what asserting on `DSN_ENV` and the missing-variable
exit actually tests.

- [ ] **Step 2: Run it and confirm it fails**

Run: `uv run pytest infrastructure/questdb/tests/test_schema.py -v`
Expected: collection error — `infrastructure/questdb/apply.py` does not exist.

- [ ] **Step 3: Write the candle schema**

```sql
-- infrastructure/questdb/schema/bars.sql
-- Candle tables, one per timeframe. Partition granularity follows candle
-- density: about 408 one-minute candles per trading day per symbol versus one
-- daily candle. DEDUP UPSERT KEYS is load-bearing — the live path (phase 2)
-- rewrites the in-progress candle many times per minute and the post-close
-- reconciliation overwrites what it wrote.

CREATE TABLE IF NOT EXISTS bars_1m (
    ts TIMESTAMP,
    symbol SYMBOL INDEX,
    session SYMBOL,
    open DOUBLE,
    high DOUBLE,
    low DOUBLE,
    close DOUBLE,
    volume LONG,
    trade_value DOUBLE,
    rsi DOUBLE,
    macd DOUBLE,
    macd_signal DOUBLE,
    macd_histogram DOUBLE,
    stochastic_k DOUBLE,
    stochastic_d DOUBLE,
    roc DOUBLE,
    williams_r DOUBLE,
    src SYMBOL
) TIMESTAMP(ts) PARTITION BY DAY WAL DEDUP UPSERT KEYS(ts, symbol);

CREATE TABLE IF NOT EXISTS bars_15m (
    ts TIMESTAMP,
    symbol SYMBOL INDEX,
    session SYMBOL,
    open DOUBLE,
    high DOUBLE,
    low DOUBLE,
    close DOUBLE,
    volume LONG,
    trade_value DOUBLE,
    rsi DOUBLE,
    macd DOUBLE,
    macd_signal DOUBLE,
    macd_histogram DOUBLE,
    stochastic_k DOUBLE,
    stochastic_d DOUBLE,
    roc DOUBLE,
    williams_r DOUBLE,
    src SYMBOL
) TIMESTAMP(ts) PARTITION BY MONTH WAL DEDUP UPSERT KEYS(ts, symbol);

CREATE TABLE IF NOT EXISTS bars_1h (
    ts TIMESTAMP,
    symbol SYMBOL INDEX,
    session SYMBOL,
    open DOUBLE,
    high DOUBLE,
    low DOUBLE,
    close DOUBLE,
    volume LONG,
    trade_value DOUBLE,
    rsi DOUBLE,
    macd DOUBLE,
    macd_signal DOUBLE,
    macd_histogram DOUBLE,
    stochastic_k DOUBLE,
    stochastic_d DOUBLE,
    roc DOUBLE,
    williams_r DOUBLE,
    src SYMBOL
) TIMESTAMP(ts) PARTITION BY MONTH WAL DEDUP UPSERT KEYS(ts, symbol);

CREATE TABLE IF NOT EXISTS bars_1d (
    ts TIMESTAMP,
    symbol SYMBOL INDEX,
    session SYMBOL,
    open DOUBLE,
    high DOUBLE,
    low DOUBLE,
    close DOUBLE,
    volume LONG,
    trade_value DOUBLE,
    rsi DOUBLE,
    macd DOUBLE,
    macd_signal DOUBLE,
    macd_histogram DOUBLE,
    stochastic_k DOUBLE,
    stochastic_d DOUBLE,
    roc DOUBLE,
    williams_r DOUBLE,
    src SYMBOL
) TIMESTAMP(ts) PARTITION BY YEAR WAL DEDUP UPSERT KEYS(ts, symbol);
```

- [ ] **Step 4: Write the theme schema**

```sql
-- infrastructure/questdb/schema/themes.sql
-- Theme groups and their constituents.
--
-- theme_snapshot keys on date_tp as well as theme, because the same theme
-- yields a different dt_prft_rt per period and every collected period is kept.
-- The column keeps its upstream name: its semantics are unconfirmed, and a
-- name like period_return would invite a consumer to reason on a guess.
--
-- theme_members records every membership, including symbols outside the
-- KOSPI 200, so that stock_count and dt_prft_rt stay interpretable. Kiwoom
-- computes them over all members. in_universe says whether candles exist for
-- the symbol, which lets a consumer tell "no data" from "no signal".

CREATE TABLE IF NOT EXISTS theme_snapshot (
    ts TIMESTAMP,
    theme_code SYMBOL INDEX,
    theme_name SYMBOL,
    date_tp INT,
    dt_prft_rt DOUBLE,
    change_rate DOUBLE,
    stock_count INT,
    rising_count INT,
    falling_count INT,
    main_stocks STRING
) TIMESTAMP(ts) PARTITION BY MONTH WAL DEDUP UPSERT KEYS(ts, theme_code, date_tp);

CREATE TABLE IF NOT EXISTS theme_members (
    ts TIMESTAMP,
    theme_code SYMBOL INDEX,
    symbol SYMBOL INDEX,
    stock_name SYMBOL,
    in_universe BOOLEAN
) TIMESTAMP(ts) PARTITION BY MONTH WAL DEDUP UPSERT KEYS(ts, theme_code, symbol);
```

- [ ] **Step 5: Write the apply job**

```python
# infrastructure/questdb/apply.py
"""Apply the QuestDB schema.

This is an operational asset, not library code — nothing imports it. It lives
here rather than inside market-collector so that no service can reach a
CREATE TABLE at boot. Services read and write rows; nothing mutates schema as
a side effect of starting.

QuestDB has no Alembic dialect worth targeting and narrow ALTER support, so
the schema is idempotent CREATE TABLE IF NOT EXISTS rather than versioned
migrations.

Usage: KTB_QUESTDB_DSN=postgresql://admin:quest@localhost:8812/qdb \\
           uv run --group migrations python infrastructure/questdb/apply.py
"""

import os
import pathlib
import sys

DSN_ENV = "KTB_QUESTDB_DSN"
SCHEMA_DIR = pathlib.Path(__file__).parent / "schema"


def schema_files() -> list[pathlib.Path]:
    return sorted(SCHEMA_DIR.glob("*.sql"))


def statements(sql: str) -> list[str]:
    lines = [line for line in sql.splitlines() if not line.strip().startswith("--")]
    return [chunk.strip() for chunk in "\n".join(lines).split(";") if chunk.strip()]


def apply(dsn: str) -> int:
    import psycopg

    executed = 0
    with psycopg.connect(dsn, autocommit=True) as connection:
        for path in schema_files():
            for statement in statements(path.read_text(encoding="utf-8")):
                with connection.cursor() as cursor:
                    cursor.execute(statement)
                executed += 1
    return executed


def main() -> None:
    dsn = os.environ.get(DSN_ENV)
    if not dsn:
        raise SystemExit(
            f"{DSN_ENV} is not set. Example: "
            f"{DSN_ENV}=postgresql://admin:quest@localhost:8812/qdb"
        )
    count = apply(dsn)
    print(f"applied {count} statements from {len(schema_files())} files")


if __name__ == "__main__":
    sys.exit(main())
```

`psycopg` is imported inside `apply` so that the unit tests can load this module and exercise `statements` and `schema_files` without the `migrations` dependency group installed.

- [ ] **Step 6: Run the unit tests**

Run: `uv run pytest infrastructure/questdb/tests/test_schema.py -v`
Expected: PASS, nine tests.

- [ ] **Step 7: Apply it against the development QuestDB**

This step exists because the unit tests check the text, not QuestDB's opinion of it. `open` and `close` are ordinary identifiers in QuestDB, but that is worth proving once rather than discovering during the backfill.

```bash
docker compose -f compose.dev.yaml up -d questdb
# wait for the healthcheck to pass
docker compose -f compose.dev.yaml ps questdb
KTB_QUESTDB_DSN=postgresql://admin:quest@localhost:8812/qdb \
  uv run --group migrations python infrastructure/questdb/apply.py
```

Expected: `applied 6 statements from 2 files`.

Then confirm idempotence and the dedup declaration:

```bash
KTB_QUESTDB_DSN=postgresql://admin:quest@localhost:8812/qdb \
  uv run --group migrations python infrastructure/questdb/apply.py
```

Expected: the same output, no error.

```bash
curl -s -G 'http://localhost:9000/exec' \
  --data-urlencode "query=SELECT table_name, dedup, partitionBy FROM tables() ORDER BY table_name"
```

Expected: six rows, `dedup` true for all of them, and `partitionBy` reading `DAY` for `bars_1m`, `MONTH` for `bars_15m`, `bars_1h`, `theme_snapshot`, `theme_members`, and `YEAR` for `bars_1d`.

If QuestDB rejects a column name, quote it in the DDL and add the quoted form to the writer in Task 9 — do not rename the column, because the eight indicator names are a contract with `ktb_market_analyzer.DESCRIPTIONS`.

- [ ] **Step 8: Commit**

```bash
git add infrastructure/questdb
git commit -m "feat: own the QuestDB candle and theme schema"
```

---

### Task 8: Indicator computation

**Files:**
- Create: `services/market-collector/src/market_collector/indicators.py`
- Test: `services/market-collector/tests/test_indicators.py`

**Interfaces:**
- Consumes: `ktb_market_analyzer` (`rsi`, `macd`, `stochastic`, `roc`, `williams_r`, `DESCRIPTIONS`).
- Produces: `INDICATOR_FIELDS: tuple[str, ...]`, `indicator_series(high, low, close) -> dict[str, npt.NDArray[np.float64]]` keyed by `INDICATOR_FIELDS`, and `indicators_for_latest(high, low, close) -> dict[str, float | None]`.

The spec's in-memory `IndicatorWindow` is **not** built here. Phase 1 computes indicators over a whole collected series in one pass and never holds a live window, so building one now would be untested-in-anger code shipped for a caller that does not exist yet. It belongs to Phase 2 along with the property test that the window and the batch agree.

- [ ] **Step 1: Write the failing test**

```python
# services/market-collector/tests/test_indicators.py
import numpy as np
import pytest
from ktb_market_analyzer import DESCRIPTIONS
from market_collector.indicators import (
    INDICATOR_FIELDS,
    indicator_series,
    indicators_for_latest,
)


def _prices(n: int):
    rng = np.random.default_rng(7)
    close = 70000 + np.cumsum(rng.normal(0, 200, n))
    high = close + np.abs(rng.normal(0, 150, n))
    low = close - np.abs(rng.normal(0, 150, n))
    return high, low, close


def test_the_field_set_is_exactly_the_published_descriptions():
    assert set(INDICATOR_FIELDS) == set(DESCRIPTIONS)


def test_series_returns_one_array_per_field_aligned_with_the_input():
    high, low, close = _prices(400)

    series = indicator_series(high, low, close)

    assert set(series) == set(INDICATOR_FIELDS)
    for field, values in series.items():
        assert values.shape == close.shape, field


def test_latest_matches_the_last_element_of_the_series():
    high, low, close = _prices(400)

    series = indicator_series(high, low, close)
    latest = indicators_for_latest(high, low, close)

    for field in INDICATOR_FIELDS:
        assert latest[field] == pytest.approx(series[field][-1])


def test_nan_becomes_none_so_that_null_reaches_the_database():
    high, low, close = _prices(5)

    latest = indicators_for_latest(high, low, close)

    assert latest["rsi"] is None
    assert latest["macd"] is None


def test_an_empty_series_yields_all_none():
    empty = np.array([], dtype=np.float64)

    assert indicators_for_latest(empty, empty, empty) == dict.fromkeys(INDICATOR_FIELDS)
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `uv run pytest services/market-collector/tests/test_indicators.py -v`
Expected: collection error, no module named `market_collector.indicators`.

- [ ] **Step 3: Write the implementation**

```python
# services/market-collector/src/market_collector/indicators.py
"""The eight indicator fields, computed over a candle window.

Indicators are recomputed over the whole series rather than updated from their
own previous values. Three of the five underlying functions need a rolling
high/low range or an earlier close regardless, and RSI and MACD would need
hidden state columns because inverting them recovers only a ratio and one
equation in two unknowns. Recomputing all eight for 200 symbols over a
300-candle window measures 1.7 ms, so the incremental version would trade a
hand-written reimplementation of TA-Lib's recursions for microseconds.
"""

import math

import numpy as np
import numpy.typing as npt
from ktb_market_analyzer import macd, roc, rsi, stochastic, williams_r

__all__ = [
    "INDICATOR_FIELDS",
    "indicator_series",
    "indicators_for_latest",
]

INDICATOR_FIELDS: tuple[str, ...] = (
    "rsi",
    "macd",
    "macd_signal",
    "macd_histogram",
    "stochastic_k",
    "stochastic_d",
    "roc",
    "williams_r",
)

Array = npt.NDArray[np.float64]


def indicator_series(high: Array, low: Array, close: Array) -> dict[str, Array]:
    macd_result = macd(close)
    stochastic_result = stochastic(high, low, close)
    return {
        "rsi": rsi(close),
        "macd": macd_result.macd,
        "macd_signal": macd_result.signal,
        "macd_histogram": macd_result.histogram,
        "stochastic_k": stochastic_result.k,
        "stochastic_d": stochastic_result.d,
        "roc": roc(close),
        "williams_r": williams_r(high, low, close),
    }


def _clean(value: float) -> float | None:
    return None if math.isnan(value) or math.isinf(value) else float(value)


def indicators_for_latest(high: Array, low: Array, close: Array) -> dict[str, float | None]:
    if close.size == 0:
        return dict.fromkeys(INDICATOR_FIELDS)
    series = indicator_series(high, low, close)
    return {field: _clean(series[field][-1]) for field in INDICATOR_FIELDS}
```

`_clean` maps `NaN` to `None` because TA-Lib emits `NaN` during warm-up and QuestDB should hold a null there, not a sentinel a consumer might average.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest services/market-collector/tests/test_indicators.py -v`
Expected: PASS, five tests.

- [ ] **Step 5: Commit**

```bash
git add services/market-collector
git commit -m "feat: compute the eight indicator fields over a candle window"
```

---

### Task 9: QuestDB store

**Files:**
- Create: `services/market-collector/src/market_collector/store.py`
- Test: `services/market-collector/tests/test_store.py`

**Interfaces:**
- Consumes: `INDICATOR_FIELDS` from `market_collector.indicators`.
- Produces: `TIMEFRAME_TABLES: dict[str, str]` mapping `"1m"`, `"15m"`, `"1h"`, `"1d"` to table names; `CandleRow` frozen dataclass with `ts: datetime`, `symbol: str`, `session: str`, `open: float`, `high: float`, `low: float`, `close: float`, `volume: int`, `trade_value: float | None`, `indicators: dict[str, float | None]`, `src: str`; `RowSink` `Protocol` with `row(self, table: str, *, symbols: dict[str, str], columns: dict[str, object], at: datetime) -> None` and `flush(self) -> None`; `Store` with `__init__(self, sink: RowSink)`, `write_candles(self, timeframe: str, rows: Iterable[CandleRow]) -> int`, `write_theme_groups(self, ts: datetime, groups: Iterable[ThemeGroup]) -> int`, `write_theme_members(self, ts: datetime, members: Iterable[ThemeMember], universe: frozenset[str]) -> int`; `questdb_sink(host: str, port: int)` context manager; `read_regular_candles(dsn: str, timeframe: str, symbol: str) -> list[tuple[datetime, float, float, float]]`.

- [ ] **Step 1: Write the failing test**

```python
# services/market-collector/tests/test_store.py
from datetime import UTC, datetime

import pytest
from market_collector.indicators import INDICATOR_FIELDS
from market_collector.kiwoom.themes import ThemeGroup, ThemeMember
from market_collector.store import TIMEFRAME_TABLES, CandleRow, Store

TS = datetime(2026, 9, 22, 6, 19, tzinfo=UTC)


class FakeSink:
    def __init__(self):
        self.rows = []
        self.flushes = 0

    def row(self, table, *, symbols, columns, at):
        self.rows.append((table, dict(symbols), dict(columns), at))

    def flush(self):
        self.flushes += 1


def _candle(**overrides):
    base = dict(
        ts=TS,
        symbol="005930",
        session="regular",
        open=277750.0,
        high=278000.0,
        low=277500.0,
        close=277500.0,
        volume=38961,
        trade_value=None,
        indicators=dict.fromkeys(INDICATOR_FIELDS, 1.0),
        src="rest",
    )
    base.update(overrides)
    return CandleRow(**base)


def test_every_timeframe_maps_to_a_table():
    assert TIMEFRAME_TABLES == {
        "1m": "bars_1m",
        "15m": "bars_15m",
        "1h": "bars_1h",
        "1d": "bars_1d",
    }


def test_candle_writes_split_symbols_from_columns():
    sink = FakeSink()

    written = Store(sink).write_candles("1m", [_candle()])

    assert written == 1
    table, symbols, columns, at = sink.rows[0]
    assert table == "bars_1m"
    assert symbols == {"symbol": "005930", "session": "regular", "src": "rest"}
    assert columns["open"] == 277750.0
    assert columns["close"] == 277500.0
    assert columns["volume"] == 38961
    assert at == TS


def test_none_valued_columns_are_omitted_so_questdb_stores_null():
    sink = FakeSink()
    indicators = dict.fromkeys(INDICATOR_FIELDS, None)
    indicators["rsi"] = 55.5

    Store(sink).write_candles("1m", [_candle(indicators=indicators, trade_value=None)])

    _, _, columns, _ = sink.rows[0]
    assert columns["rsi"] == 55.5
    assert "macd" not in columns
    assert "trade_value" not in columns


def test_extended_rows_carry_ohlcv_and_no_indicators():
    sink = FakeSink()
    row = _candle(session="extended", indicators=dict.fromkeys(INDICATOR_FIELDS, None))

    Store(sink).write_candles("1m", [row])

    _, symbols, columns, _ = sink.rows[0]
    assert symbols["session"] == "extended"
    assert columns["close"] == 277500.0
    assert not any(field in columns for field in INDICATOR_FIELDS)


def test_an_unknown_timeframe_is_rejected_before_any_write():
    sink = FakeSink()

    with pytest.raises(KeyError, match="4h"):
        Store(sink).write_candles("4h", [_candle()])

    assert sink.rows == []


def test_writes_are_flushed_once_per_batch():
    sink = FakeSink()

    Store(sink).write_candles("1m", [_candle(), _candle()])

    assert len(sink.rows) == 2
    assert sink.flushes == 1


def test_theme_groups_are_written_with_date_tp_in_the_columns():
    sink = FakeSink()
    group = ThemeGroup(
        code="103",
        name="태양광_발전/설치/운영",
        date_tp=10,
        dt_prft_rt=297.10,
        change_rate=-1.20,
        stock_count=3,
        rising_count=1,
        falling_count=2,
        main_stocks="에스에너지, 한화솔루션",
    )

    Store(sink).write_theme_groups(TS, [group])

    table, symbols, columns, at = sink.rows[0]
    assert table == "theme_snapshot"
    assert symbols == {"theme_code": "103", "theme_name": "태양광_발전/설치/운영"}
    assert columns["date_tp"] == 10
    assert columns["dt_prft_rt"] == 297.10
    assert columns["stock_count"] == 3
    assert columns["main_stocks"] == "에스에너지, 한화솔루션"
    assert at == TS


def test_theme_members_are_tagged_against_the_universe():
    sink = FakeSink()
    members = [
        ThemeMember(theme_code="557", symbol="005930", stock_name="삼성전자"),
        ThemeMember(theme_code="557", symbol="033170", stock_name="시그네틱스"),
    ]

    Store(sink).write_theme_members(TS, members, frozenset({"005930"}))

    flags = {row[1]["symbol"]: row[2]["in_universe"] for row in sink.rows}
    assert flags == {"005930": True, "033170": False}
    assert sink.rows[0][0] == "theme_members"
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `uv run pytest services/market-collector/tests/test_store.py -v`
Expected: collection error, no module named `market_collector.store`.

- [ ] **Step 3: Write the implementation**

```python
# services/market-collector/src/market_collector/store.py
"""Write candles and theme data to QuestDB, and read candles back.

Writes go over the InfluxDB line protocol, which is the ingestion path; reads
go over the Postgres wire protocol on port 8812. They are two distinct paths
and the distinction is easy to lose, so they are named apart here.

A column whose value is None is omitted from the row rather than sent. The
line protocol has no null literal, and omitting the column is what leaves it
null in storage — which is exactly what an extended-session candle's indicator
columns need.
"""

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from market_collector.indicators import INDICATOR_FIELDS
from market_collector.kiwoom.themes import ThemeGroup, ThemeMember

__all__ = [
    "TIMEFRAME_TABLES",
    "CandleRow",
    "RowSink",
    "Store",
    "questdb_sink",
    "read_regular_candles",
]

TIMEFRAME_TABLES: dict[str, str] = {
    "1m": "bars_1m",
    "15m": "bars_15m",
    "1h": "bars_1h",
    "1d": "bars_1d",
}

THEME_SNAPSHOT_TABLE = "theme_snapshot"
THEME_MEMBERS_TABLE = "theme_members"


@dataclass(frozen=True)
class CandleRow:
    ts: datetime
    symbol: str
    session: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    trade_value: float | None
    indicators: dict[str, float | None]
    src: str


class RowSink(Protocol):
    def row(
        self,
        table: str,
        *,
        symbols: dict[str, str],
        columns: dict[str, object],
        at: datetime,
    ) -> None: ...

    def flush(self) -> None: ...


def _without_nones(columns: dict[str, object]) -> dict[str, object]:
    return {name: value for name, value in columns.items() if value is not None}


class Store:
    def __init__(self, sink: RowSink) -> None:
        self._sink = sink

    def write_candles(self, timeframe: str, rows: Iterable[CandleRow]) -> int:
        if timeframe not in TIMEFRAME_TABLES:
            raise KeyError(f"unknown timeframe: {timeframe}")
        table = TIMEFRAME_TABLES[timeframe]

        written = 0
        for candle in rows:
            columns: dict[str, object] = {
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": candle.volume,
                "trade_value": candle.trade_value,
            }
            for field in INDICATOR_FIELDS:
                columns[field] = candle.indicators.get(field)

            self._sink.row(
                table,
                symbols={
                    "symbol": candle.symbol,
                    "session": candle.session,
                    "src": candle.src,
                },
                columns=_without_nones(columns),
                at=candle.ts,
            )
            written += 1

        self._sink.flush()
        return written

    def write_theme_groups(self, ts: datetime, groups: Iterable[ThemeGroup]) -> int:
        written = 0
        for group in groups:
            self._sink.row(
                THEME_SNAPSHOT_TABLE,
                symbols={"theme_code": group.code, "theme_name": group.name},
                columns=_without_nones(
                    {
                        "date_tp": group.date_tp,
                        "dt_prft_rt": group.dt_prft_rt,
                        "change_rate": group.change_rate,
                        "stock_count": group.stock_count,
                        "rising_count": group.rising_count,
                        "falling_count": group.falling_count,
                        "main_stocks": group.main_stocks,
                    }
                ),
                at=ts,
            )
            written += 1
        self._sink.flush()
        return written

    def write_theme_members(
        self, ts: datetime, members: Iterable[ThemeMember], universe: frozenset[str]
    ) -> int:
        written = 0
        for member in members:
            self._sink.row(
                THEME_MEMBERS_TABLE,
                symbols={
                    "theme_code": member.theme_code,
                    "symbol": member.symbol,
                    "stock_name": member.stock_name,
                },
                columns={"in_universe": member.symbol in universe},
                at=ts,
            )
            written += 1
        self._sink.flush()
        return written


class _QuestDbSink:
    def __init__(self, sender: object) -> None:
        self._sender = sender

    def row(
        self,
        table: str,
        *,
        symbols: dict[str, str],
        columns: dict[str, object],
        at: datetime,
    ) -> None:
        from questdb.ingress import TimestampNanos

        self._sender.row(  # type: ignore[attr-defined]
            table,
            symbols=symbols,
            columns=columns,
            at=TimestampNanos.from_datetime(at),
        )

    def flush(self) -> None:
        self._sender.flush()  # type: ignore[attr-defined]


@contextmanager
def questdb_sink(host: str, port: int) -> Iterator[RowSink]:
    from questdb.ingress import Sender

    with Sender("http", host, port) as sender:
        yield _QuestDbSink(sender)


def read_regular_candles(
    dsn: str, timeframe: str, symbol: str
) -> list[tuple[datetime, float, float, float]]:
    """Regular-session candles for one symbol, oldest first, as (ts, high, low, close)."""
    if timeframe not in TIMEFRAME_TABLES:
        raise KeyError(f"unknown timeframe: {timeframe}")

    import psycopg

    table = TIMEFRAME_TABLES[timeframe]
    query = (
        f"SELECT ts, high, low, close FROM {table} "
        "WHERE symbol = %s AND session = 'regular' ORDER BY ts ASC"
    )
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute(query, (symbol,))
        return [(ts, high, low, close) for ts, high, low, close in cursor.fetchall()]
```

`questdb` and `psycopg` are imported inside the functions that need them so the unit tests, which inject `FakeSink`, never load a native extension.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest services/market-collector/tests/test_store.py -v`
Expected: PASS, eight tests.

- [ ] **Step 5: Commit**

```bash
git add services/market-collector
git commit -m "feat: write candles and theme rows to QuestDB over ILP"
```

---

### Task 10: Backfill cursors

**Files:**
- Create: `services/market-collector/src/market_collector/cursor.py`
- Test: `services/market-collector/tests/test_cursor.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `Cursor` frozen dataclass with `next_key: str | None`, `oldest: str | None`, `pages: int`, `done: bool`; `CursorStore` with `__init__(self, path: Path)`, `get(self, symbol: str, timeframe: str) -> Cursor`, `advance(self, symbol: str, timeframe: str, next_key: str | None, oldest: str | None) -> Cursor`, `finish(self, symbol: str, timeframe: str) -> Cursor`, `pending(self, symbols: Iterable[str], timeframes: Iterable[str]) -> list[tuple[str, str]]`.

- [ ] **Step 1: Write the failing test**

```python
# services/market-collector/tests/test_cursor.py
import json

from market_collector.cursor import Cursor, CursorStore


def test_an_unknown_pair_starts_from_the_beginning(tmp_path):
    store = CursorStore(tmp_path / "c.json")

    assert store.get("005930", "1m") == Cursor(next_key=None, oldest=None, pages=0, done=False)


def test_advancing_records_the_key_and_counts_the_page(tmp_path):
    store = CursorStore(tmp_path / "c.json")

    store.advance("005930", "1m", "NK1", "20260921104300")
    cursor = store.advance("005930", "1m", "NK2", "20260917170500")

    assert cursor.next_key == "NK2"
    assert cursor.oldest == "20260917170500"
    assert cursor.pages == 2
    assert cursor.done is False


def test_state_survives_a_new_store_on_the_same_file(tmp_path):
    path = tmp_path / "c.json"
    CursorStore(path).advance("005930", "1m", "NK1", "20260921104300")

    assert CursorStore(path).get("005930", "1m").next_key == "NK1"


def test_finishing_marks_done_and_clears_the_key(tmp_path):
    store = CursorStore(tmp_path / "c.json")
    store.advance("005930", "1m", "NK1", "20260921104300")

    cursor = store.finish("005930", "1m")

    assert cursor.done is True
    assert cursor.next_key is None


def test_pending_lists_only_unfinished_pairs(tmp_path):
    store = CursorStore(tmp_path / "c.json")
    store.finish("005930", "1m")

    pending = store.pending(["005930", "000660"], ["1m", "1d"])

    assert ("005930", "1m") not in pending
    assert ("005930", "1d") in pending
    assert ("000660", "1m") in pending
    assert len(pending) == 3


def test_timeframes_are_tracked_independently(tmp_path):
    store = CursorStore(tmp_path / "c.json")

    store.advance("005930", "1m", "NKm", "20260921104300")

    assert store.get("005930", "1d").next_key is None


def test_the_file_is_readable_json(tmp_path):
    path = tmp_path / "c.json"
    store = CursorStore(path)
    store.advance("005930", "1m", "NK1", "20260921104300")

    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["005930|1m"]["next_key"] == "NK1"


def test_a_corrupt_file_is_replaced_rather_than_crashing_the_job(tmp_path):
    path = tmp_path / "c.json"
    path.write_text("{not json", encoding="utf-8")

    store = CursorStore(path)

    assert store.get("005930", "1m").pages == 0
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `uv run pytest services/market-collector/tests/test_cursor.py -v`
Expected: collection error, no module named `market_collector.cursor`.

- [ ] **Step 3: Write the implementation**

```python
# services/market-collector/src/market_collector/cursor.py
"""Per-symbol, per-timeframe backfill progress.

ka10080 ignores any date parameter, so a symbol's history can only be walked
backwards one page at a time with next-key. A run that dies partway through
cannot restart from a date, which makes persisting the key the difference
between resuming and starting over.

State is a single JSON file because it is small, human-readable when a run
goes wrong, and needs no service to be up.
"""

import json
import os
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

__all__ = ["Cursor", "CursorStore"]


@dataclass(frozen=True)
class Cursor:
    next_key: str | None = None
    oldest: str | None = None
    pages: int = 0
    done: bool = False


class CursorStore:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._state: dict[str, dict[str, object]] = self._load()

    def _load(self) -> dict[str, dict[str, object]]:
        if not self._path.is_file():
            return {}
        try:
            loaded = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}
        return loaded if isinstance(loaded, dict) else {}

    @staticmethod
    def _key(symbol: str, timeframe: str) -> str:
        return f"{symbol}|{timeframe}"

    def get(self, symbol: str, timeframe: str) -> Cursor:
        raw = self._state.get(self._key(symbol, timeframe))
        if not isinstance(raw, dict):
            return Cursor()
        return Cursor(
            next_key=raw.get("next_key"),
            oldest=raw.get("oldest"),
            pages=int(raw.get("pages", 0)),
            done=bool(raw.get("done", False)),
        )

    def _put(self, symbol: str, timeframe: str, cursor: Cursor) -> Cursor:
        self._state[self._key(symbol, timeframe)] = asdict(cursor)
        self._write()
        return cursor

    def advance(
        self, symbol: str, timeframe: str, next_key: str | None, oldest: str | None
    ) -> Cursor:
        current = self.get(symbol, timeframe)
        return self._put(
            symbol,
            timeframe,
            Cursor(
                next_key=next_key,
                oldest=oldest or current.oldest,
                pages=current.pages + 1,
                done=False,
            ),
        )

    def finish(self, symbol: str, timeframe: str) -> Cursor:
        current = self.get(symbol, timeframe)
        return self._put(
            symbol,
            timeframe,
            Cursor(next_key=None, oldest=current.oldest, pages=current.pages, done=True),
        )

    def pending(
        self, symbols: Iterable[str], timeframes: Iterable[str]
    ) -> list[tuple[str, str]]:
        frames = list(timeframes)
        return [
            (symbol, timeframe)
            for symbol in symbols
            for timeframe in frames
            if not self.get(symbol, timeframe).done
        ]

    def _write(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(self._path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, self._path)
```

The write is to a temporary file followed by `os.replace`, so a run killed mid-write leaves the previous complete state rather than a truncated file. A corrupt file is treated as no progress rather than an error, because losing a cursor costs a re-walk while crashing on startup costs the whole run.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest services/market-collector/tests/test_cursor.py -v`
Expected: PASS, eight tests.

- [ ] **Step 5: Commit**

```bash
git add services/market-collector
git commit -m "feat: persist backfill cursors so an interrupted run resumes"
```

---

### Task 11: Backfill one symbol and timeframe

**Files:**
- Create: `services/market-collector/src/market_collector/backfill.py`
- Test: `services/market-collector/tests/test_backfill.py`

**Interfaces:**
- Consumes: `ChartClient`, `Page`, `parse_minute_bar`, `parse_daily_bar`, `Store`, `CandleRow`, `CursorStore`, `indicator_series`, `comment_series_for`, `INDICATOR_FIELDS`, `COMMENT_FIELDS`.
- Produces: `TIC_SCOPES: dict[str, int]`, `DEFAULT_DEPTHS: dict[str, int]`, `to_candle_rows(bars, symbol, src="rest", with_indicators=True) -> list[CandleRow]`, `collect(client, symbol, timeframe, cursors, base_dt, depth, max_pages=None) -> list[MinuteBar | DailyBar]`, `backfill_one(client, store, cursors, symbol, timeframe, base_dt, depth, with_indicators=False, max_pages=None) -> int`.

### v1 scope, which differs from what this plan originally described

Three decisions were taken after the plan was written and before this task ran.

**Collection depth is a bar count, per timeframe, and nothing uses a date cutoff.**

```python
DEFAULT_DEPTHS: dict[str, int] = {"1m": 8000, "15m": 300, "1h": 300, "1d": 300}
```

8,000 one-minute candles is about a month — roughly 20 trading days at ~408 candles each, counting the extended-session candles Kiwoom includes. The other three stay at 300. Terminating on a count rather than a date is what lets all four timeframes share one code path: an earlier draft bounded 1m by calendar age and the rest by bar count, which meant two termination rules and a date-to-trading-day conversion nobody can do exactly.

Per symbol this is about 13 pages — roughly 9 for 1m at 900 records a page, and 1 each for the others. The plan's earlier arithmetic of 124 pages and 3.3 minutes per symbol is stale, so a smoke run's `max_pages` bound should be chosen against 13, not 124.

**Backfilled history stores OHLCV only. Indicators and verdicts are attached only to candles that arrive after the service is running.** Hence `with_indicators=False` as `backfill_one`'s default — Task 13 flips it from a setting. `to_candle_rows` keeps `with_indicators=True` as its own default because the pre-open and live paths always want them; only the backfill opts out.

This is why 300 is enough. The depths are sized to be **input** to the newest calculation, not a series to store: the longest lookback among the eight fields is MACD at 33, and even `SMA(200)` — if an LLM tool asks for one later — needs 199 of the 300. Nothing here needs a long stored history of indicator values.

**Verdicts are stored beside values.** `CandleRow` carries both, and `COMMENT_FIELDS` has 7 entries against `INDICATOR_FIELDS`' 8 because `macd_signal` has no verdict.

- [ ] **Step 1: Write the failing tests**

`test_backfill.py` covers, in order: `TIC_SCOPES` mapping the three minute timeframes to their `tic_scope` values; `DEFAULT_DEPTHS` holding the four depths above; `collect` following continuation until the depth is reached; bars returned oldest first; duplicate timestamps dropped across overlapping pages; **`collect` stopping once `depth` bars are collected even when the API offers more**; `collect` marking the cursor done when the history ends on its own; `collect` skipping a pair already marked done; `max_pages` bounding a smoke run; indicators computed only on regular-session rows; **`with_indicators=False` producing rows whose indicator and verdict dicts are entirely `None`**; and `backfill_one` writing to the timeframe's table with `src="rest"`.

Two tests carry the weight, because they pin decisions rather than mechanics:

```python
def test_collect_stops_once_the_depth_is_reached(tmp_path):
    pages = [
        Page([_minute_row(i, 277000 + i) for i in range(900)], "NK1", True),
        Page([_minute_row(i, 276000 + i) for i in range(900)], "NK2", True),
    ]
    client = FakeClient(pages)
    cursors = CursorStore(tmp_path / "c.json")

    bars = collect(client, "005930", "1m", cursors, BASE_DT, depth=1000)

    # The second page completes rather than being truncated mid-page: already
    # fetched data is not thrown away, and dedup makes a re-run safe.
    assert len(bars) >= 1000
    assert len(client.minute_calls) == 2
    assert cursors.get("005930", "1m").done is True


def test_backfill_without_indicators_stores_ohlcv_alone(tmp_path):
    sink = FakeSink()
    pages = [Page([_minute_row(0, 277000)], None, False)]

    backfill_one(
        FakeClient(pages), Store(sink), CursorStore(tmp_path / "c.json"),
        "005930", "1m", BASE_DT, depth=300, with_indicators=False,
    )

    _, symbols, columns, _ = sink.rows[0]
    assert columns["close"] == 277000.0
    assert not any(field in columns for field in INDICATOR_FIELDS)
    assert not any(f"{field}_comment" in symbols for field in COMMENT_FIELDS)
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest services/market-collector/tests/test_backfill.py -q`
Expected: collection error, no module named `market_collector.backfill`.

- [ ] **Step 3: Write `collect`**

One loop for all four timeframes, differing only in which endpoint it pages. It accumulates into a dict keyed by timestamp — which drops the duplicates overlapping pages produce — and stops when that dict reaches `depth`, when the page reports no continuation, or when `max_pages` is hit.

**Do not truncate the final page to land exactly on `depth`.** Already-fetched candles are not worth discarding, and dedup makes writing a few extra harmless. `depth` is a floor on what is collected, not a ceiling.

Advance the cursor on every page, and mark it done when the depth or the history end is reached. `max_pages` is a smoke-run bound and deliberately does **not** mark done.

- [ ] **Step 4: Write `to_candle_rows`**

Compute the indicator series over the regular-session candles as one contiguous array, then map the results back onto their original positions. Extended-session candles are excluded from the array entirely rather than masked afterwards, because leaving them in would change the period count every indicator is defined over.

With `with_indicators=False`, skip the computation and give every row empty indicator and verdict dicts.

**Treat a non-finite value as unjudged for both the value and its verdict.** `NaN` becomes `None` on the value, and the analyzer's verdict rules already return `None` for `NaN` — but they check `isnan` only, not `isinf`. So if an infinity ever appeared, the value would be cleaned to `None` while the verdict stayed a real label, and a consumer would read a verdict for a row with no number. Clean both on `math.isfinite`, not just `isnan`. It takes a close of exactly zero to reach this with real prices, which is why it is a guard rather than a fix.

- [ ] **Step 5: Write `backfill_one`**

Collect, convert, write, log the count. Returns the number of rows written.

- [ ] **Step 6: Run the tests**

Run: `uv run pytest services/market-collector/tests/test_backfill.py -q`
Expected: PASS.

- [ ] **Step 7: Verify a real round trip against QuestDB**

The unit tests never touch a database. This proves the written rows come back.

**Docker registry pulls were blocked in this environment when Task 7 ran** — the daemon answered but `docker compose up -d questdb` never got past "Pulling", behind a pull-through proxy. Try it; if it still fails, **do not skip silently and do not report DONE**. Complete every other step, report **DONE_WITH_CONCERNS** naming this one, and say exactly which commands you could not run. A report claiming the round trip passed when it did not is the one outcome that must not happen.

```bash
docker compose -f compose.dev.yaml up -d questdb
KTB_QUESTDB_DSN=postgresql://admin:quest@localhost:8812/qdb \
  uv run --group migrations python infrastructure/questdb/apply.py
```

Then, with credentials from `.env` exported, a bounded real walk from a Python shell:

```python
from market_collector.backfill import backfill_one
from market_collector.cursor import CursorStore
from market_collector.kiwoom.auth import TokenStore
from market_collector.kiwoom.rest import ChartClient, HttpxTransport
from market_collector.settings import Settings
from market_collector.store import Store, questdb_sink, read_regular_candles

settings = Settings()
transport = HttpxTransport()
client = ChartClient(TokenStore(settings.kiwoom_accounts[0], transport), transport)
cursors = CursorStore("var/smoke/cursors.json")

with questdb_sink(settings.questdb_ilp_host, settings.questdb_ilp_port) as sink:
    written = backfill_one(
        client, Store(sink), cursors, "005930", "1h",
        base_dt="20260925", depth=300, with_indicators=True, max_pages=1,
    )
print("written", written)
print("read back", len(read_regular_candles(settings.questdb_dsn, "1h", "005930")))
```

Expected: `written` around 900 and `read back` a non-zero count slightly smaller, because extended-session candles are excluded from the read. If `read back` is 0, the session classification or the ILP timestamp is wrong — check that `at` carries a timezone-aware UTC datetime.

Confirm indicators landed and dedup works:

```bash
curl -s -G 'http://localhost:9000/exec' --data-urlencode \
  "query=SELECT count(), count(rsi), count(rsi_comment) FROM bars_1h WHERE symbol='005930'"
```

Expected: the indicator and verdict counts are lower than the row count, because the oldest bars and the extended-session rows are null there. Re-running the same walk must leave `count()` unchanged — that is the dedup key doing its job.

- [ ] **Step 8: Commit**

```bash
git add services/market-collector
git commit -m "feat: backfill one symbol and timeframe to a bar-count depth"
```

---

### Task 12: Daily theme snapshot

**Files:**
- Create: `services/market-collector/src/market_collector/themes.py`
- Test: `services/market-collector/tests/test_themes_job.py`

**Interfaces:**
- Consumes: `ThemeClient`, `ThemeGroup`, `ThemeMember`, `Store`, `load_kospi200`.
- Produces: `snapshot(client, store, universe, date_tps, now) -> tuple[int, int]` returning the counts of snapshot rows and membership rows written.

- [ ] **Step 1: Write the failing test**

```python
# services/market-collector/tests/test_themes_job.py
from datetime import UTC, datetime

from market_collector.kiwoom.themes import ThemeGroup, ThemeMember
from market_collector.store import Store
from market_collector.themes import snapshot

NOW = datetime(2026, 9, 22, 7, 0, tzinfo=UTC)


def _group(code, date_tp):
    return ThemeGroup(
        code=code,
        name=f"theme-{code}",
        date_tp=date_tp,
        dt_prft_rt=1.0,
        change_rate=0.5,
        stock_count=2,
        rising_count=1,
        falling_count=1,
        main_stocks="a, b",
    )


class FakeThemeClient:
    def __init__(self, codes):
        self.codes = codes
        self.group_calls = []
        self.member_calls = []

    def groups(self, date_tp):
        self.group_calls.append(date_tp)
        return [_group(code, date_tp) for code in self.codes]

    def members(self, theme_code, date_tp):
        self.member_calls.append((theme_code, date_tp))
        return [
            ThemeMember(theme_code=theme_code, symbol="005930", stock_name="삼성전자"),
            ThemeMember(theme_code=theme_code, symbol="033170", stock_name="시그네틱스"),
        ]


class FakeSink:
    def __init__(self):
        self.rows = []
        self.flushes = 0

    def row(self, table, *, symbols, columns, at):
        self.rows.append((table, dict(symbols), dict(columns), at))

    def flush(self):
        self.flushes += 1


def test_groups_are_collected_once_per_period():
    client = FakeThemeClient(["103", "557"])
    sink = FakeSink()

    snapshots, _ = snapshot(client, Store(sink), frozenset(), [5, 20], NOW)

    assert client.group_calls == [5, 20]
    assert snapshots == 4
    snapshot_rows = [r for r in sink.rows if r[0] == "theme_snapshot"]
    assert {r[2]["date_tp"] for r in snapshot_rows} == {5, 20}


def test_members_are_collected_once_per_theme_not_once_per_period():
    client = FakeThemeClient(["103", "557"])

    _, members = snapshot(client, Store(FakeSink()), frozenset(), [5, 20], NOW)

    assert sorted(code for code, _ in client.member_calls) == ["103", "557"]
    assert members == 4


def test_membership_is_tagged_against_the_universe():
    sink = FakeSink()

    snapshot(FakeThemeClient(["103"]), Store(sink), frozenset({"005930"}), [5], NOW)

    member_rows = [r for r in sink.rows if r[0] == "theme_members"]
    flags = {r[1]["symbol"]: r[2]["in_universe"] for r in member_rows}
    assert flags == {"005930": True, "033170": False}


def test_every_row_shares_one_snapshot_timestamp():
    sink = FakeSink()

    snapshot(FakeThemeClient(["103", "557"]), Store(sink), frozenset(), [5, 20], NOW)

    assert {r[3] for r in sink.rows} == {NOW}


def test_a_theme_with_no_universe_members_still_gets_a_snapshot_row():
    sink = FakeSink()

    snapshots, _ = snapshot(FakeThemeClient(["103"]), Store(sink), frozenset(), [5], NOW)

    assert snapshots == 1
    assert any(r[0] == "theme_snapshot" for r in sink.rows)
```

The last test is the one that encodes the decision from the spec: theme metrics stay interpretable even when nothing in the theme is drillable, because Kiwoom computes them over all members.

- [ ] **Step 2: Run it and confirm it fails**

Run: `uv run pytest services/market-collector/tests/test_themes_job.py -v`
Expected: collection error, no module named `market_collector.themes`.

- [ ] **Step 3: Write the implementation**

```python
# services/market-collector/src/market_collector/themes.py
"""The daily theme snapshot.

Groups are collected once per configured period, because dt_prft_rt differs by
period and the response ordering means a different set of themes reaches page
one for each. Memberships are collected once per theme rather than once per
period: they do not depend on the period, and 142 requests is already the
larger half of this job.

Every row in one run shares a single timestamp, so a reader can select one
snapshot without a range query.
"""

import logging
from collections.abc import Iterable, Sequence
from datetime import datetime

from market_collector.kiwoom.themes import ThemeClient
from market_collector.store import Store

__all__ = ["snapshot"]

log = logging.getLogger(__name__)


def snapshot(
    client: ThemeClient,
    store: Store,
    universe: frozenset[str],
    date_tps: Sequence[int],
    now: datetime,
) -> tuple[int, int]:
    snapshot_rows = 0
    codes: list[str] = []
    seen: set[str] = set()

    for date_tp in date_tps:
        groups = client.groups(date_tp)
        snapshot_rows += store.write_theme_groups(now, groups)
        for group in groups:
            if group.code not in seen:
                seen.add(group.code)
                codes.append(group.code)

    log.info("collected %d theme rows over %d periods", snapshot_rows, len(date_tps))

    member_rows = 0
    reference_period = date_tps[0] if date_tps else 10
    for code in codes:
        members: Iterable = client.members(code, reference_period)
        member_rows += store.write_theme_members(now, members, universe)

    log.info("collected %d memberships over %d themes", member_rows, len(codes))
    return snapshot_rows, member_rows
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest services/market-collector/tests/test_themes_job.py -v`
Expected: PASS, five tests.

- [ ] **Step 5: Commit**

```bash
git add services/market-collector
git commit -m "feat: snapshot theme groups and memberships daily"
```

---

### Task 13: Subcommands and account sharding

**Files:**
- Modify: `services/market-collector/src/market_collector/backfill.py` (add `refresh_recent`)
- Modify: `services/market-collector/src/market_collector/__main__.py` (replace the dispatch written in Task 1)
- Test: `services/market-collector/tests/test_refresh.py`
- Test: `services/market-collector/tests/test_cli.py`

**Interfaces:**
- Consumes: everything from Tasks 1–12.
- Produces: `backfill.refresh_recent(client, store, symbol, timeframe, base_dt, since) -> int`; `__main__.shard(symbols, buckets) -> list[list[str]]`, `__main__.previous_session_start(now) -> datetime`, `__main__.run_backfill(settings, today, max_pages=None) -> int`, `__main__.run_preopen(settings, now) -> int`, `__main__.run_themes(settings, now) -> tuple[int, int]`, and a `main()` dispatching `backfill`, `preopen`, `themes`.

- [ ] **Step 1: Write the failing test for the incremental refresh**

```python
# services/market-collector/tests/test_refresh.py
from datetime import UTC, datetime

from market_collector.backfill import refresh_recent
from market_collector.indicators import INDICATOR_FIELDS
from market_collector.kiwoom.rest import Page
from market_collector.store import Store


def _row(minute_index, close):
    hour, minute = divmod(minute_index, 60)
    stamp = f"20260922{9 + hour:02d}{minute:02d}00"
    return {
        "cntr_tm": stamp,
        "cur_prc": f"+{close}",
        "open_pric": f"+{close}",
        "high_pric": f"+{close + 100}",
        "low_pric": f"+{close - 100}",
        "trde_qty": "1000",
    }


class FakeClient:
    def __init__(self, page):
        self.page = page
        self.calls = []

    def minute_page(self, symbol, tic_scope, next_key=None):
        self.calls.append((symbol, tic_scope, next_key))
        return self.page


class FakeSink:
    def __init__(self):
        self.rows = []
        self.flushes = 0

    def row(self, table, *, symbols, columns, at):
        self.rows.append((table, dict(symbols), dict(columns), at))

    def flush(self):
        self.flushes += 1


def test_only_one_page_is_fetched():
    page = Page([_row(i, 277000 + i) for i in range(390)], "NK1", True)
    client = FakeClient(page)

    refresh_recent(client, Store(FakeSink()), "005930", "1m", "20260922",
                   since=datetime(2026, 9, 22, 0, 0, tzinfo=UTC))

    assert len(client.calls) == 1


def test_only_rows_at_or_after_since_are_written():
    page = Page([_row(i, 277000 + i) for i in range(390)], None, False)
    sink = FakeSink()

    written = refresh_recent(
        client=FakeClient(page),
        store=Store(sink),
        symbol="005930",
        timeframe="1m",
        base_dt="20260922",
        since=datetime(2026, 9, 22, 5, 0, tzinfo=UTC),  # 14:00 KST
    )

    assert written == len(sink.rows)
    assert written < 390
    assert all(row[3] >= datetime(2026, 9, 22, 5, 0, tzinfo=UTC) for row in sink.rows)


def test_written_rows_have_a_warm_indicator_because_the_page_carries_history():
    page = Page([_row(i, 277000 + i) for i in range(390)], None, False)
    sink = FakeSink()

    refresh_recent(
        FakeClient(page), Store(sink), "005930", "1m", "20260922",
        since=datetime(2026, 9, 22, 6, 0, tzinfo=UTC),  # 15:00 KST
    )

    last = sink.rows[-1][2]
    assert any(field in last for field in INDICATOR_FIELDS)


def test_nothing_new_writes_nothing():
    page = Page([_row(0, 277000)], None, False)
    sink = FakeSink()

    written = refresh_recent(
        FakeClient(page), Store(sink), "005930", "1m", "20260922",
        since=datetime(2026, 9, 23, 0, 0, tzinfo=UTC),
    )

    assert written == 0
    assert sink.rows == []
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `uv run pytest services/market-collector/tests/test_refresh.py -v`
Expected: `ImportError: cannot import name 'refresh_recent'`.

- [ ] **Step 3: Add `refresh_recent` to `backfill.py`**

Append to `services/market-collector/src/market_collector/backfill.py`, and add `"refresh_recent"` to `__all__`:

```python
def refresh_recent(
    client: ChartClient,
    store: Store,
    symbol: str,
    timeframe: str,
    base_dt: str,
    since: datetime,
) -> int:
    """Fetch the newest page and write only the candles at or after ``since``.

    Indicators are computed over the whole page but only the tail is written.
    One page is 900 candles for the minute endpoints and 600 for the daily
    one, while the tail of a single session is at most about 450 candles, so
    every written candle has well over the 300-candle warm-up behind it. Writing
    the whole page instead would overwrite good indicator values from the
    backfill with nulls, because the head of the page has no warm-up.
    """
    is_daily = timeframe == "1d"
    page = (
        client.daily_page(symbol, base_dt)
        if is_daily
        else client.minute_page(symbol, TIC_SCOPES[timeframe])
    )
    parse = parse_daily_bar if is_daily else parse_minute_bar
    bars = sorted({parse(row).ts: parse(row) for row in page.rows}.values(), key=lambda b: b.ts)
    if not bars:
        return 0

    rows = [row for row in to_candle_rows(bars, symbol) if row.ts >= since]
    if not rows:
        return 0
    return store.write_candles(timeframe, rows)
```

- [ ] **Step 4: Run the refresh tests**

Run: `uv run pytest services/market-collector/tests/test_refresh.py -v`
Expected: PASS, four tests.

- [ ] **Step 5: Write the failing CLI test**

```python
# services/market-collector/tests/test_cli.py
from datetime import UTC, datetime

import pytest
from market_collector import __main__ as cli

QDB = "postgresql://admin:quest@localhost:8812/qdb"
ACCOUNTS = '[{"app_key":"k1","secret_key":"s1"},{"app_key":"k2","secret_key":"s2"}]'


def _populate(monkeypatch):
    monkeypatch.setenv("MARKET_COLLECTOR_QUESTDB_DSN", QDB)
    monkeypatch.setenv("MARKET_COLLECTOR_QUESTDB_ILP_HOST", "localhost")
    monkeypatch.setenv("MARKET_COLLECTOR_KIWOOM_ACCOUNTS", ACCOUNTS)


def test_shard_spreads_symbols_evenly():
    buckets = cli.shard([f"{i:06d}" for i in range(10)], 3)

    assert [len(b) for b in buckets] == [4, 3, 3]
    assert sorted(s for b in buckets for s in b) == [f"{i:06d}" for i in range(10)]


def test_shard_never_returns_more_buckets_than_symbols():
    assert cli.shard(["005930"], 5) == [["005930"]]


def test_shard_rejects_zero_buckets():
    with pytest.raises(ValueError):
        cli.shard(["005930"], 0)


def test_previous_session_start_is_the_prior_kst_midnight_in_utc():
    # A realistic pre-open moment: 2026-09-22 23:00 UTC is 08:00 KST on the 23rd,
    # so the session to write is the 22nd, starting at 2026-09-22 00:00 KST.
    start = cli.previous_session_start(datetime(2026, 9, 22, 23, 0, tzinfo=UTC))

    assert start == datetime(2026, 9, 21, 15, 0, tzinfo=UTC)  # 2026-09-22 00:00 KST


@pytest.mark.parametrize("command", ["backfill", "preopen", "themes"])
def test_each_subcommand_dispatches_to_its_runner(monkeypatch, command, capsys):
    _populate(monkeypatch)
    monkeypatch.setattr("sys.argv", ["market-collector", command])
    called = []
    monkeypatch.setattr(cli, "run_backfill", lambda *a, **k: called.append("backfill") or 0)
    monkeypatch.setattr(cli, "run_preopen", lambda *a, **k: called.append("preopen") or 0)
    monkeypatch.setattr(cli, "run_themes", lambda *a, **k: called.append("themes") or (0, 0))

    cli.main()

    assert called == [command]


def test_backfill_accepts_a_page_bound(monkeypatch):
    _populate(monkeypatch)
    monkeypatch.setattr("sys.argv", ["market-collector", "backfill", "--max-pages", "2"])
    seen = {}
    monkeypatch.setattr(
        cli, "run_backfill", lambda settings, today, max_pages=None: seen.update(
            max_pages=max_pages
        ) or 0
    )

    cli.main()

    assert seen["max_pages"] == 2
```

### Defects and additions carried into this task

Four things reached this task from elsewhere. None were in the original plan.

**1. `Settings.questdb_ilp_port` defaults to the wrong port.** It is `9009`, but `store.py`
connects with `Protocol.Http` and QuestDB serves HTTP ILP on **9000** — 9009 is the TCP ILP
port, which `compose.dev.yaml` does not even expose. Verified against the running server:
9009 is closed, and a real `Sender(Protocol.Http, host, 9009)` fails with "Could not detect
server's line protocol version". Every real run would fail to connect.

Change the default to `9000` and add a test pinning it, with a comment saying which
protocol the port belongs to — the two numbers are easy to swap back because both are
"the QuestDB port" to a reader who has not hit this.

**2. Two settings v1 needs, which no earlier task added** because nothing read them yet:

```
MARKET_COLLECTOR_BACKFILL_DEPTHS          per-timeframe bar counts
                                          default {"1m": 8000, "15m": 300, "1h": 300, "1d": 300}
MARKET_COLLECTOR_INDICATORS_ON_BACKFILL   compute indicators and verdicts over backfilled
                                          history; default False
```

`backfill.DEFAULT_DEPTHS` already holds those four numbers. Import it as the setting's
default rather than retyping them, so the two cannot drift.

The toggle governs **history only**. `run_backfill` passes it through as
`with_indicators`; `run_preopen` always computes regardless, because new candles are the
whole reason the indicators exist.

**3. `HttpxTransport` opens an `httpx.Client` and nothing closes it.** It exposes `close()`
but has no context-manager support, and this task builds one transport per account inside
long-running workers — so the connection pool leaks for the process lifetime. Close each
transport when its worker finishes. Do not add `__enter__`/`__exit__` to `HttpxTransport`
itself; that is Task 5's file and a separate change.

**4. `tach.toml` does not know about this service.** A separate branch
(`feat/6/news-scraper`, PR #19) introduces `tach` with `root_module = "forbid"` and an
explicit module list. It has not merged yet, so the file may be absent when you run — check
first. **If `tach.toml` exists**, add `market_collector` to `source_roots` and a module
entry:

```toml
[[modules]]
path = "market_collector"
depends_on = ["ktb_core", "ktb_market_analyzer"]
```

and confirm `uv run tach check` passes. If the file does not exist, skip this and say so in
your report — do not create it.

- [ ] **Step 6: Replace `__main__.py`**

```python
# services/market-collector/src/market_collector/__main__.py
"""Entry point for the market-collector.

A bare invocation validates settings and exits 0, because CI runs every
service image with --network none and expects that. Real work sits behind
subcommands.
"""

import argparse
import logging
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from ktb_core.logging import setup_logging

from market_collector.backfill import backfill_one, refresh_recent
from market_collector.cursor import CursorStore
from market_collector.kiwoom.auth import TokenStore
from market_collector.kiwoom.rest import ChartClient, HttpxTransport
from market_collector.kiwoom.parse import KST
from market_collector.kiwoom.themes import ThemeClient
from market_collector.settings import Settings
from market_collector.store import Store, questdb_sink
from market_collector.themes import snapshot
from market_collector.universe import load_kospi200

TIMEFRAMES = ("1m", "15m", "1h", "1d")
HISTORY_DAYS = 366

log = logging.getLogger(__name__)


def shard(symbols: Sequence[str], buckets: int) -> list[list[str]]:
    if buckets < 1:
        raise ValueError(f"buckets must be positive: {buckets}")
    groups: list[list[str]] = [[] for _ in range(min(buckets, len(symbols)) or 1)]
    for index, symbol in enumerate(symbols):
        groups[index % len(groups)].append(symbol)
    return [group for group in groups if group]


def previous_session_start(now: datetime) -> datetime:
    """Midnight KST of the day before ``now``, expressed in UTC."""
    local = now.astimezone(KST)
    previous = (local - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return previous.astimezone(UTC)


def _client(settings: Settings, index: int) -> ChartClient:
    transport = HttpxTransport()
    account = settings.kiwoom_accounts[index]
    return ChartClient(
        TokenStore(account, transport), transport, interval=settings.request_interval
    )


def run_backfill(settings: Settings, today: datetime, max_pages: int | None = None) -> int:
    symbols = sorted(load_kospi200())
    cutoff = today - timedelta(days=HISTORY_DAYS)
    base_dt = today.astimezone(KST).strftime("%Y%m%d")
    groups = shard(symbols, len(settings.kiwoom_accounts))

    cursors = CursorStore(settings.cursor_path)

    def worker(index: int, bucket: list[str]) -> int:
        client = _client(settings, index)
        written = 0
        with questdb_sink(settings.questdb_ilp_host, settings.questdb_ilp_port) as sink:
            store = Store(sink)
            for symbol in bucket:
                for timeframe in TIMEFRAMES:
                    written += backfill_one(
                        client, store, cursors, symbol, timeframe, base_dt, cutoff, max_pages
                    )
        return written

    with ThreadPoolExecutor(max_workers=len(groups)) as pool:
        totals = list(pool.map(lambda pair: worker(*pair), enumerate(groups)))

    total = sum(totals)
    log.info("backfill wrote %d candles across %d symbols", total, len(symbols))
    return total


def run_preopen(settings: Settings, now: datetime) -> int:
    symbols = sorted(load_kospi200())
    since = previous_session_start(now)
    base_dt = now.astimezone(KST).strftime("%Y%m%d")
    groups = shard(symbols, len(settings.kiwoom_accounts))

    def worker(index: int, bucket: list[str]) -> int:
        client = _client(settings, index)
        written = 0
        with questdb_sink(settings.questdb_ilp_host, settings.questdb_ilp_port) as sink:
            store = Store(sink)
            for symbol in bucket:
                for timeframe in TIMEFRAMES:
                    written += refresh_recent(
                        client, store, symbol, timeframe, base_dt, since
                    )
        return written

    with ThreadPoolExecutor(max_workers=len(groups)) as pool:
        totals = list(pool.map(lambda pair: worker(*pair), enumerate(groups)))

    total = sum(totals)
    log.info("preopen wrote %d candles since %s", total, since.isoformat())
    return total


def run_themes(settings: Settings, now: datetime) -> tuple[int, int]:
    transport = HttpxTransport()
    client = ThemeClient(
        TokenStore(settings.kiwoom_accounts[0], transport),
        transport,
        interval=settings.request_interval,
    )
    universe = load_kospi200()
    with questdb_sink(settings.questdb_ilp_host, settings.questdb_ilp_port) as sink:
        return snapshot(client, Store(sink), universe, settings.theme_date_tps, now)


def main() -> None:
    parser = argparse.ArgumentParser(prog="market-collector")
    sub = parser.add_subparsers(dest="command")
    backfill_parser = sub.add_parser("backfill", help="walk one year of history")
    backfill_parser.add_argument("--max-pages", type=int, default=None)
    sub.add_parser("preopen", help="write the previous session, extended included")
    sub.add_parser("themes", help="snapshot theme groups and memberships")

    args = parser.parse_args()
    settings = Settings()
    setup_logging(settings.log_level)

    now = datetime.now(UTC)
    if args.command == "backfill":
        run_backfill(settings, now, args.max_pages)
    elif args.command == "preopen":
        run_preopen(settings, now)
    elif args.command == "themes":
        run_themes(settings, now)
    else:
        logging.getLogger(__name__).info("market-collector started")


if __name__ == "__main__":
    main()
```

Each worker builds its own `ChartClient`, and therefore its own `TokenStore` and pacing state, because the rate limit is per account and sharing a client would serialise all five.

The `CursorStore`, by contrast, is constructed **once, outside the workers, and shared**. One store per worker would give each thread its own in-memory copy of the whole cursor dict, and every write serialises that copy — so the last worker to finish a symbol would silently erase the entries the others had written, which is the precise progress loss this file exists to prevent. `CursorStore` is thread-safe for this reason; see Task 10.

- [ ] **Step 7: Update the Task 1 entry-point test for argparse**

`argparse` exits with code 2 on an unknown subcommand by raising `SystemExit`, which the Task 1 test already expects, but the message now comes from argparse. Replace the second test in `services/market-collector/tests/test_main.py`:

```python
def test_unknown_subcommand_exits_nonzero(monkeypatch, capsys):
    _populate(monkeypatch)
    monkeypatch.setattr("sys.argv", ["market-collector", "nope"])

    with pytest.raises(SystemExit) as exc:
        main()

    assert exc.value.code == 2
    assert "invalid choice" in capsys.readouterr().err
```

- [ ] **Step 8: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS, no failures.

Run: `uv run ruff check . && uv run ruff format --check . && uv run ty check`
Expected: all clean. If `ty` complains about the `questdb` or `psycopg` stubs, the fix is a narrow `# type: ignore[...]` on that line, not loosening the configuration.

- [ ] **Step 9: Commit**

```bash
git add services/market-collector
git commit -m "feat: add backfill, preopen and themes subcommands"
```

---

### Task 14: Image and CI integration

**Files:**
- Create: `docker/market-collector.Dockerfile`
- Create: `docker/requirements/market-collector.txt` (generated)
- Modify: `.github/workflows/ci-dev.yaml` (add to the `build-images` matrix)
- Modify: `.github/workflows/ci-main.yaml` (add to the `build-and-push-images` matrix)

**Interfaces:**
- Consumes: the `market-collector` console script from Task 1.
- Produces: nothing importable. CI gains a fourth service in two matrices.

**The CI this task integrates with was restructured on `dev` after this plan was written.** `ci.yaml` no longer exists; it is now `ci-dev.yaml` (pull requests and pushes to `dev`) and `ci-main.yaml` (pushes to `main`, which also pushes images to a registry). Services are a `strategy.matrix`, not a shell loop, so adding one is a single list entry in each file rather than an edit at three line numbers.

Four things the old `ci.yaml` did are gone, and none of them are this task's job to restore. They are recorded here so their absence is known rather than assumed:

| Dropped | What it checked |
| --- | --- |
| `uv run ty check` | Type errors. Still pinned in the dev group and still worth running locally; simply no longer a gate. |
| `verify-dependency-isolation` | That a service imports only what it declares. |
| `verify-exported-requirements` | That `docker/requirements/*.txt` still matches `uv.lock`. Those files still exist and the Dockerfiles still install from them — they are now unverified. |
| Running each image with `--network none` | That a one-shot service exits 0 without dialling anything. `build-images` now builds only. |

Task 1's design still holds: a bare `market-collector` invocation validates settings and exits 0 without dialling anything. What changed is that nothing in CI proves it any more, so Step 4 below does it by hand.

- [ ] **Step 1: Write the Dockerfile**

```dockerfile
# docker/market-collector.Dockerfile
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    VIRTUAL_ENV=/app/.venv

WORKDIR /app
RUN uv venv "$VIRTUAL_ENV"

COPY docker/requirements/market-collector.txt ./requirements.txt
RUN uv pip install --require-hashes --requirement requirements.txt

COPY pyproject.toml ./
COPY packages/core packages/core
COPY packages/market-analyzer packages/market-analyzer
COPY services/market-collector services/market-collector
RUN uv pip install --no-deps ./packages/core ./packages/market-analyzer ./services/market-collector

FROM python:3.13-slim-bookworm AS runtime

RUN useradd --create-home --uid 10001 app
WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

USER app
CMD ["market-collector"]
```

- [ ] **Step 2: Export the pinned requirements**

Run:

```bash
uv export --package market-collector --no-dev --no-emit-workspace \
  --format requirements-txt -o docker/requirements/market-collector.txt
```

Expected: the file exists and contains hashes. `ta-lib` appears because `ktb-market-analyzer` requires it.

Commit this file. Nothing in CI regenerates or checks it any more, so a stale export will only surface as a broken image build.

- [ ] **Step 3: Build the image**

Run: `docker build -f docker/market-collector.Dockerfile -t ktb-market-collector .`
Expected: success.

`ta-lib` needs the native TA-Lib C library. **Check `docker/portfolio-builder.Dockerfile` first** — it already depends on `ktb-market-analyzer`, so whatever it does about TA-Lib is the established answer and should be copied rather than reinvented. If it does nothing special, the wheel is self-contained and so is yours.

- [ ] **Step 4: Verify the image exits 0 with no network**

CI no longer does this, so do it here and put the output in your report.

```bash
docker run --rm --network none \
  -e MARKET_COLLECTOR_QUESTDB_DSN="postgresql://unused@unused.invalid:8812/unused" \
  -e MARKET_COLLECTOR_QUESTDB_ILP_HOST="unused.invalid" \
  -e MARKET_COLLECTOR_KIWOOM_ACCOUNTS='[{"app_key":"x","secret_key":"y"}]' \
  ktb-market-collector
```

Expected: one JSON log line with `"message":"market-collector started"` and exit code 0. A bare invocation must never dial anything.

- [ ] **Step 5: Add the service to both CI matrices**

In `.github/workflows/ci-dev.yaml`, under `build-images`, add one entry:

```yaml
    strategy:
      matrix:
        service:
          - news-preprocessor
          - news-clusterer
          - portfolio-builder
          - market-collector
```

Do the same in `.github/workflows/ci-main.yaml` under `build-and-push-images`. Both files list the services independently, so missing one means the image builds on pull requests but is never pushed on `main`, or the reverse.

Change nothing else in either file. The dropped jobs in the table above are not this task's to restore.

- [ ] **Step 6: Reproduce locally what CI will run**

Run:

```bash
uv sync --all-packages --locked --group migrations
uv run ruff check --output-format=github .
uv run ruff format --check .
uv run pytest
```

Expected: all clean. That is exactly `ci-dev.yaml`'s `lint-and-test` job.

Also run `uv run ty check` even though CI dropped it — this repository's code was written to pass it and the tool is still pinned in the dev group.

- [ ] **Step 7: Check the isolation property by hand**

CI used to assert this and no longer does, so confirm it once here:

```bash
uv run --isolated --package market-collector --locked --no-dev \
  python -c "import market_collector.__main__"
```

Expected: no output, exit 0. A failure means a dependency is used but not declared in `services/market-collector/pyproject.toml`.

- [ ] **Step 8: Commit**

```bash
git add docker .github/workflows uv.lock
git commit -m "ci: build the market-collector image on dev and main"
```

---

### Task 15: Rate-limit backoff

**Files:**
- Modify: `services/market-collector/src/market_collector/kiwoom/rest.py`
- Modify: `services/market-collector/src/market_collector/kiwoom/themes.py`
- Test: `services/market-collector/tests/test_backoff.py`

**Interfaces:**
- Consumes: `KiwoomRateLimited` from `market_collector.kiwoom.rest`.
- Produces: `ChartClient.__init__` and `ThemeClient.__init__` both gain `max_retries: int = 5` and `backoff_base: float = 2.0`. `KiwoomRateLimited` still escapes once the retries are exhausted.

**Ordering note:** this task and Task 14 are independent. Task 14 only touches the image and CI, while this one touches the clients. If the deadline squeezes, do this one first — a backfill without backoff is the riskier thing to be missing.

Backoff lives inside the clients rather than in a wrapper, because the rate limiter it answers to is the client's own pacing and because every caller needs it. A 200-symbol walk that dies on the first `return_code=5` after ninety minutes is not an acceptable failure mode, and the measured walk saw zero rate-limit responses at 1.3 s, which means any that do appear are rare and worth waiting out.

- [ ] **Step 1: Write the failing test**

```python
# services/market-collector/tests/test_backoff.py
import pytest
from market_collector.kiwoom.auth import TokenStore
from market_collector.kiwoom.rest import ChartClient, KiwoomRateLimited
from market_collector.kiwoom.themes import ThemeClient
from market_collector.settings import KiwoomAccount

ACCOUNT = KiwoomAccount(app_key="k", secret_key="s")
TOKEN_OK = {"return_code": 0, "token": "t1", "token_type": "Bearer",
            "expires_dt": "20270101000000"}
LIMITED = ({}, {"return_code": 5, "return_msg": "허용된 요청 개수를 초과하였습니다"})
MINUTE_ROW = {
    "cntr_tm": "20260922151900",
    "cur_prc": "+277500",
    "open_pric": "+277750",
    "high_pric": "+278000",
    "low_pric": "+277500",
    "trde_qty": "1000",
}
CHART_OK = ({"cont-yn": "N"}, {"return_code": 0, "stk_min_pole_chart_qry": [MINUTE_ROW]})
GROUP_OK = (
    {"cont-yn": "N"},
    {"return_code": 0, "thema_grp": [{
        "thema_grp_cd": "103", "thema_nm": "t", "stk_num": "1", "flu_rt": "0.0",
        "rising_stk_num": "1", "fall_stk_num": "0", "dt_prft_rt": "1.0", "main_stk": "a",
    }]},
)


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    def post(self, path, body, headers):
        self.calls += 1
        return self.responses.pop(0)


def test_a_rate_limited_response_is_retried_and_then_succeeds():
    transport = FakeTransport(({}, TOKEN_OK), LIMITED, LIMITED, CHART_OK)
    slept = []
    # interval=0.0 so the per-request pacing sleeps are zero and filter out below;
    # a retry re-enters the attempt and therefore paces again, which is intended.
    client = ChartClient(
        TokenStore(ACCOUNT, transport),
        transport,
        interval=0.0,
        sleep=slept.append,
        backoff_base=2.0,
    )

    page = client.minute_page("005930", 1)

    assert len(page.rows) == 1
    assert [wait for wait in slept if wait] == [2.0, 4.0]


def test_backoff_gives_up_after_max_retries_and_raises():
    transport = FakeTransport(({}, TOKEN_OK), LIMITED, LIMITED, LIMITED)
    client = ChartClient(
        TokenStore(ACCOUNT, transport), transport, sleep=lambda _: None, max_retries=2
    )

    with pytest.raises(KiwoomRateLimited):
        client.minute_page("005930", 1)

    assert transport.calls == 4  # one token call plus three attempts


def test_a_non_rate_limit_error_is_not_retried():
    bad = ({}, {"return_code": 2, "return_msg": "입력 값 오류입니다"})
    transport = FakeTransport(({}, TOKEN_OK), bad)
    client = ChartClient(TokenStore(ACCOUNT, transport), transport, sleep=lambda _: None)

    with pytest.raises(Exception):
        client.minute_page("005930", 1)

    assert transport.calls == 2


def test_the_theme_client_backs_off_the_same_way():
    transport = FakeTransport(({}, TOKEN_OK), LIMITED, GROUP_OK)
    slept = []
    client = ThemeClient(
        TokenStore(ACCOUNT, transport),
        transport,
        interval=0.0,
        sleep=slept.append,
        backoff_base=3.0,
    )

    groups = client.groups(date_tp=10)

    assert len(groups) == 1
    assert [wait for wait in slept if wait] == [3.0]
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `uv run pytest services/market-collector/tests/test_backoff.py -v`
Expected: `TypeError: ChartClient.__init__() got an unexpected keyword argument 'backoff_base'`.

- [ ] **Step 3: Add backoff to `ChartClient`**

Change `ChartClient.__init__` in `rest.py` to accept the two new arguments:

```python
    def __init__(
        self,
        tokens: TokenStore,
        transport: Transport,
        interval: float = 1.3,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = 5,
        backoff_base: float = 2.0,
    ) -> None:
        self._tokens = tokens
        self._transport = transport
        self._interval = interval
        self._sleep = sleep
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._paced = False
```

Then split `_page` so the retry loop wraps a single attempt. Replace the body of `_page` with:

```python
    def _page(
        self,
        api_id: str,
        array_field: str,
        body: dict[str, object],
        next_key: str | None,
    ) -> Page:
        for attempt in range(self._max_retries + 1):
            try:
                return self._attempt(api_id, array_field, body, next_key)
            except KiwoomRateLimited:
                if attempt == self._max_retries:
                    raise
                self._sleep(self._backoff_base ** (attempt + 1))
        raise AssertionError("unreachable")

    def _attempt(
        self,
        api_id: str,
        array_field: str,
        body: dict[str, object],
        next_key: str | None,
    ) -> Page:
        if self._paced:
            self._sleep(self._interval)
        self._paced = True

        headers = {"authorization": f"Bearer {self._tokens.token()}", "api-id": api_id}
        if next_key:
            headers["cont-yn"] = "Y"
            headers["next-key"] = next_key

        response_headers, payload = self._transport.post(CHART_PATH, body, headers)
        code = payload.get("return_code")
        if code == RATE_LIMITED:
            raise KiwoomRateLimited(str(payload.get("return_msg")))
        if code != 0:
            raise KiwoomRequestError(
                f"return_code={code} return_msg={payload.get('return_msg')}"
            )

        rows = self._rows(payload, array_field)
        cont = response_headers.get("cont-yn")
        returned_key = response_headers.get("next-key") or None
        has_more = bool(rows) and cont == "Y" and returned_key is not None
        return Page(rows=rows, next_key=returned_key, has_more=has_more)
```

Note that the retry re-enters `_attempt`, which applies the pacing sleep again. That is intended: a rate-limited account should wait both its backoff and its normal interval before trying again.

- [ ] **Step 4: Add the same to `ThemeClient`**

`themes.py` ended up structurally identical to `rest.py`: its private method is also named
`_page`, and it also returns the shared `Page` NamedTuple rather than a bare tuple. So this
step is the same rename plus the same wrapper.

Give `__init__` the same two parameters and store them, rename the existing `_page` body to
`_attempt`, and add:

```python
    def _page(
        self,
        api_id: str,
        body: dict[str, object],
        array_field: str,
        next_key: str | None,
    ) -> Page:
        for attempt in range(self._max_retries + 1):
            try:
                return self._attempt(api_id, body, array_field, next_key)
            except KiwoomRateLimited:
                if attempt == self._max_retries:
                    raise
                self._sleep(self._backoff_base ** (attempt + 1))
        raise AssertionError("unreachable")
```

Note the parameter order differs from `rest.py`'s `_page` — `themes.py` takes
`(api_id, body, array_field, next_key)` while `rest.py` takes
`(api_id, array_field, body, next_key)`. Keep each file's existing order; do not
"harmonise" them, because the callers in each file pass positionally.

- [ ] **Step 5: Run every client test**

Run: `uv run pytest services/market-collector/tests/test_backoff.py services/market-collector/tests/test_rest.py services/market-collector/tests/test_kiwoom_themes.py -v`
Expected: PASS. The Task 5 pacing test still holds, because the first attempt's behaviour is unchanged.

- [ ] **Step 6: Run the whole suite and the linters**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run ty check`
Expected: all clean.

- [ ] **Step 7: Commit**

```bash
git add services/market-collector
git commit -m "feat: back off and retry when Kiwoom rate-limits a request"
```

---

## Operational pre-flight

None of this is code, and all of it blocks a working deployment. It comes from §11 of the spec and should be confirmed before the first real backfill, not on the morning of 2026-09-28.

- [ ] **Every one of the five Kiwoom accounts has the developer machine's IP allowlisted.** An unregistered IP fails token issue with `return_code=3` and message `8050`. Registration is per account on the Kiwoom REST API site under API 사용신청.
- [ ] **The deployment target has a stable egress IP, and it is allowlisted on all five accounts.** A container with a dynamic egress address fails every call. This needs a NAT gateway with an Elastic IP or an instance with a fixed address, and it is an infrastructure change someone else may own.
- [ ] **Confirm whether Kiwoom caps registered IPs per account.** Five accounts multiplied by several developers plus the deployment target may exceed a limit nobody has checked.
- [ ] **`universe/kospi200.csv` holds 200 six-digit codes from KRX.** Task 3 Step 6 fails loudly if not.
- [ ] **QuestDB has a snapshot policy in the deployed environment.** Kiwoom discards minute candles after about a year, so this database becomes the only copy of anything older. Losing the volume is unrecoverable. `compose.dev.yaml` uses a local named volume with no backup, which is fine for development and not for production.
- [ ] **Run the backfill at least a day before the deadline.** It is about 2.2 hours across five accounts, and the minute window slides every day it is postponed.

## Deliberately not in Phase 1

Named so their absence was a decision rather than an oversight. **Three of these have since
landed** — marked below — so read this as the Phase 1 boundary, not as current scope.

- ~~The WebSocket live path, group allocation, and tick-to-candle aggregation.~~
  **Landed** as `live.py`. The one-minute requirement turned out to be the collector's own,
  so no version of this service met its requirement without it.
- ~~The in-memory `IndicatorWindow`.~~ **Landed** as `live.Window`, seeded from QuestDB.
- ~~Reconciliation that overwrites WebSocket-derived candles with Kiwoom's own.~~
  **Landed**, but before the open rather than after the close: extended-session trading
  happens after 15:30, so only a pre-open pass sees both sessions final.
- Intraday theme refresh. Snapshots are still daily. `intraday` refreshes candles, not
  themes.
- Any change to `packages/core`.
- Resolving what `dt_prft_rt` measures. The column still ships under its upstream name with
  its semantics marked unconfirmed.

## Self-review record

**Spec coverage.** At the time of writing, every section of the design document mapped to a task except the live path and reconciliation, deferred by decision, and the open questions, which are not work. Both deferrals have since been built; the spec's section numbers have also changed, since the three design documents were consolidated into one. Two gaps were found while reviewing and are now closed: rate-limit backoff from §9 had no task and became Task 15, and §11's operational prerequisites had no home and became the pre-flight checklist above.

**Scope correction.** The spec's `IndicatorWindow` and its indicator-window setting were removed from Tasks 1 and 8. Phase 1 computes indicators over a whole collected series and never holds a live window, so building one here would have shipped code with no caller. The live path later brought both back as `live.Window` and `MARKET_COLLECTOR_LIVE_WINDOW`, with a caller.

**Type consistency.** `Page(rows, next_key, has_more)`, `CandleRow`, `Cursor`, `ThemeGroup` and `ThemeMember` field names are used identically everywhere they appear. `backfill_one`'s interface block was missing the `max_pages` parameter its implementation takes, and now matches.

---

## Beyond this plan

Tasks 1–15 delivered Phase 1. The work below landed afterwards, driven by scope
changes rather than by a plan task, and the SDD ledger
(`.superpowers/sdd/2026-09-22-market-collector-phase1/progress.md`) carries the
rulings behind each one.

| Change | Commit | Why |
|---|---|---|
| Time and count bounds on `read_regular_candles` (`since`, `limit`) | `4f0fac3`, `ef38763` | The graph layer reads a recent window or the newest N, never the whole history. `limit` means the *newest* N — the naive `ORDER BY ts ASC LIMIT n` is silently wrong |
| The analyzer's catalogue may grow past the stored field set | `534ad9b` | Indicators added for tool use are computed on demand, not persisted. A test that pinned the two as equal would have read as "add a column" |
| Theme memberships stored for the KOSPI 200 only; `in_universe` dropped | `0386fa4` | Nothing outside the universe can be joined against. `theme_snapshot`'s counts stay market-wide and must not be combined with the stored rows |
| `universe/` collapsed into `universe.py` | `55f5f63` | Five files for one responsibility, 45 of 329 lines pure ceremony |
| Shared `Pager` extracted into `kiwoom/rest.py` | `8140d04` | Three clients each restated the same pacing, backoff, paging and stall guard |
| The live path: WebSocket ticks into 1-minute candles | `f842ca8` (reverted, restored in `9afaef8`) | The one-minute freshness requirement is the collector's, and polling cannot meet it |
| `intraday` refresh for the timeframes the live path does not produce | `baeaedf` | Nothing kept 15-minute and 1-hour candles current during a session |
| Theme reads: `read_themes`, `read_symbol_themes` | `f056f08`, `8a44cc7` | The collector could write theme snapshots but not read them, so nothing downstream could see a theme at all |

**Measured since** (spec §9): one WebSocket group accepted 200 symbols and one connection
accepted four groups, both looser than the 100-per-group figure this plan assumed, and the
IP allowlist covers the WebSocket endpoint.

**Still to measure**, all during market hours (spec §4 "Not yet measured"): the `0B`
trade-tick field ids, whether a `ka10080` page carries the minute currently forming, and
the actual rate-limit ceiling behind the 1.3-second pacing.
