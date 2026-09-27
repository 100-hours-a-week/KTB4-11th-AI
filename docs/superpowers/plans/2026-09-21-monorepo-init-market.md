# market-analyzer Core Indicators Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement six core TA-Lib indicators (RSI, MACD/Signal/Histogram, Stochastic %K/%D, ROC, Williams %R) as pure functions in `ktb_market_analyzer`, plus a static one-line English description for every output field, so a future ingestion service and a future LLM-tool layer both have real numbers — and real explanations of those numbers — to work with.

**Architecture:** Split the existing single-file `ktb_market_analyzer` package into `indicators.py` (five pure functions plus two `NamedTuple` result types for the multi-output indicators) and `descriptions.py` (a static `dict[str, str]` registry), both re-exported from `__init__.py`. Every function takes plain `float64` numpy arrays and returns arrays — or a `NamedTuple` of arrays — with industry-standard default periods that remain overridable by keyword argument. No I/O, no configuration, no first-party dependencies: this plan does not change that contract, it fills it in.

**Tech Stack:** Python 3.13, TA-Lib 0.8.0, numpy, pytest, ruff, ty — all already present in `packages/market-analyzer`. No new dependencies; `uv.lock` does not need to change.

**Spec:** `docs/superpowers/specs/2026-09-20-monorepo-init-design.md` (§3, §5, §11). This plan implements the "TA-Lib feature extraction" half of that package's stated purpose. Two decisions in this plan intentionally go beyond that spec's text; both are recorded below under "Decisions beyond the existing spec" rather than by editing the spec file — the product owner has decided the spec text can lag the implementation here.

## Global Constraints

Every task's requirements implicitly include this section.

- **`packages/market-analyzer` depends on nothing first-party — not even `ktb-core`.** (spec §3) This plan does not add any import from `ktb_core` or any other workspace member.
- **No I/O, no configuration, no LLM calls anywhere in this package.** Every function is a pure transformation of numpy arrays. `descriptions.py` is static data, not configuration.
- **No signal/label classification in this package.** RSI, MACD, Stochastic, ROC and Williams %R are returned as numbers only — no derived `"BUY"`/`"SELL"`/`"HOLD"` field anywhere. See decision 1 below.
- **`requires-python = ">=3.13,<3.15"`**, provisioned by `uv` — never the host interpreter.
- **`ruff` at line-length 100** and **`ty==0.0.82`** (pinned exactly, pre-1.0 — never widen to a range) are the workspace's lint/type-check tools. Run both at the end of every task.
- **`--locked`, never `--frozen`.** No dependency changes happen in this plan, so `uv lock` is not required — `ta-lib` and `numpy` are already in `uv.lock` from the package's initialization.
- **Commit at the end of every task.** Commit messages end with:
  ```
  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
  ```

### Decisions beyond the existing spec

1. **Signal classification is explicitly out of scope, by direct product decision.** The design doc's §3 describes the package's purpose as "TA-Lib feature extraction driving deterministic template selection." This plan implements only the feature-extraction half. Turning a Stochastic %K/%D crossover into a BUY/SELL/HOLD label is deliberately left to a future LLM-driven layer — market-analyzer emits numbers only, and the LLM is trusted to interpret them (with the help of `descriptions.py`).
2. **QuestDB persistence and "other TA-Lib indicators as LLM tools" are out of scope for this package**, and belong to a separate, not-yet-built `market-collector` service (Kiwoom OHLCV ingestion + writing these six indicators to QuestDB) and a future LLM-tool spec, respectively. Note for whoever writes the `market-collector` spec: the design doc's §2 currently states QuestDB's "ingestion and schema lifecycle... live outside this repository" — `market-collector` writing to QuestDB will contradict that sentence. This is a deliberate v1 deviation the product owner has already accepted; the spec text has intentionally been left unchanged and should be reconciled when `market-collector` gets its own spec.

### A validated TA-Lib gotcha, recorded so it isn't rediscovered

TA-Lib's own default for `STOCH`'s `fastk_period` is **5**, not 14. This plan's `stochastic()` wrapper defaults to `fastk_period=14, slowk_period=3, slowd_period=3` — the "Slow Stochastic 14,3,3" convention most charting platforms treat as standard — which is a deliberate override of TA-Lib's built-in default, not a copy of it.

Also: do **not** pass `slowk_matype=0` / `slowd_matype=0` to `talib.STOCH`. The installed `ty` stub types these parameters as the `MA_Type` enum, and passing a bare `int` literal fails `ty check` with `invalid-argument-type` (verified 2026-09-21 against `ty==0.0.82`). Omit both parameters — TA-Lib's own default (`MA_Type.SMA`) is what we want anyway, so there is nothing to override.

---

## File Structure

| Path | Responsibility |
|---|---|
| `packages/market-analyzer/src/ktb_market_analyzer/indicators.py` | The five pure indicator functions plus `MacdResult` and `StochasticResult`. |
| `packages/market-analyzer/src/ktb_market_analyzer/descriptions.py` | `DESCRIPTIONS: dict[str, str]` — one English line per output field. |
| `packages/market-analyzer/src/ktb_market_analyzer/__init__.py` | Re-exports the public surface of both modules above. |
| `packages/market-analyzer/tests/test_indicators.py` | Existing RSI tests (untouched) plus new tests for the four new indicators. |
| `packages/market-analyzer/tests/test_descriptions.py` | Registry completeness and formatting checks (new file). |

---

### Task 1: Move `rsi` into `indicators.py`

Pure refactor — no behavior change. Isolates the growing indicator surface into its own module before Task 2 adds a second function, so `__init__.py` stays a thin re-export point.

**Files:**
- Create: `packages/market-analyzer/src/ktb_market_analyzer/indicators.py`
- Modify: `packages/market-analyzer/src/ktb_market_analyzer/__init__.py`
- Test: `packages/market-analyzer/tests/test_indicators.py` (no changes — must keep passing unmodified)

**Interfaces:**
- Consumes: nothing.
- Produces: `ktb_market_analyzer.indicators.rsi(close: npt.NDArray[np.float64], timeperiod: int = 14) -> npt.NDArray[np.float64]`, re-exported as `ktb_market_analyzer.rsi`. Tasks 2–5 add sibling functions to this same module.

- [ ] **Step 1: Record the baseline**

Run: `uv run pytest packages/market-analyzer -v`
Expected: 5 passed (the existing RSI tests).

- [ ] **Step 2: Create `indicators.py` with the moved function**

`packages/market-analyzer/src/ktb_market_analyzer/indicators.py`:
```python
"""Pure TA-Lib feature extraction over OHLC price arrays.

No I/O, no configuration, no first-party dependencies — every function here
takes numpy arrays and returns numpy arrays (or a NamedTuple of them).
"""

import numpy as np
import numpy.typing as npt
import talib

__all__ = ["rsi"]


def rsi(
    close: npt.NDArray[np.float64],
    timeperiod: int = 14,
) -> npt.NDArray[np.float64]:
    return talib.RSI(close, timeperiod=timeperiod)
```

- [ ] **Step 3: Turn `__init__.py` into a re-export point**

`packages/market-analyzer/src/ktb_market_analyzer/__init__.py`:
```python
"""Deterministic TA-Lib feature extraction for 척척개미단."""

from ktb_market_analyzer.indicators import rsi

__all__ = ["rsi"]
```

- [ ] **Step 4: Run the tests to confirm the refactor changed nothing**

Run: `uv run pytest packages/market-analyzer -v`
Expected: 5 passed — same count, same test names, because `test_indicators.py` still does `from ktb_market_analyzer import rsi` and gets the same function through the re-export.

- [ ] **Step 5: Lint and type-check**

```bash
uv run ruff format . && uv run ruff check . && uv run ty check
```
Expected: all clean.

- [ ] **Step 6: Commit**

```bash
git add packages/market-analyzer
git commit -m "refactor: move rsi into ktb_market_analyzer.indicators

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: `macd`

**Files:**
- Modify: `packages/market-analyzer/src/ktb_market_analyzer/indicators.py` (add `MacdResult`, `macd`)
- Modify: `packages/market-analyzer/src/ktb_market_analyzer/__init__.py` (re-export both)
- Test: `packages/market-analyzer/tests/test_indicators.py` (add a `_close` helper and three new tests)

**Interfaces:**
- Consumes: nothing new.
- Produces: `MacdResult` (`typing.NamedTuple` with fields `macd`, `signal`, `histogram`, each `npt.NDArray[np.float64]`); `macd(close: npt.NDArray[np.float64], fastperiod: int = 12, slowperiod: int = 26, signalperiod: int = 9) -> MacdResult`. Task 6's description registry uses the field names `"macd"`, `"macd_signal"`, `"macd_histogram"` to describe this function's three outputs.

- [ ] **Step 1: Write the failing tests**

Add to the top of `packages/market-analyzer/tests/test_indicators.py`, right after the existing imports:
```python
from ktb_market_analyzer import MacdResult, macd, rsi


def _close(n: int = 50) -> np.ndarray:
    return np.linspace(100.0, 120.0, n, dtype=np.float64)


def _high_low(close: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return close + 1.0, close - 1.0
```
(This replaces the bare `from ktb_market_analyzer import rsi` import line. The existing RSI tests are untouched otherwise — they keep building their own `close` array inline, exactly as today.)

Append at the end of the file:
```python
def test_macd_returns_arrays_matching_input_length():
    close = _close()
    result = macd(close)

    assert isinstance(result, MacdResult)
    assert result.macd.shape == close.shape
    assert result.signal.shape == close.shape
    assert result.histogram.shape == close.shape


def test_macd_warmup_period_is_nan():
    close = _close()
    result = macd(close, fastperiod=12, slowperiod=26, signalperiod=9)

    assert np.isnan(result.macd[:33]).all()
    assert np.isnan(result.signal[:33]).all()
    assert np.isnan(result.histogram[:33]).all()
    assert math.isfinite(result.macd[33])
    assert math.isfinite(result.signal[33])
    assert math.isfinite(result.histogram[33])


def test_macd_is_deterministic():
    close = _close()

    first = macd(close)
    second = macd(close)

    assert np.array_equal(first.macd, second.macd, equal_nan=True)
    assert np.array_equal(first.signal, second.signal, equal_nan=True)
    assert np.array_equal(first.histogram, second.histogram, equal_nan=True)
```

The warmup count of 33 is `(slowperiod - 1) + (signalperiod - 1)` = `25 + 8` for the default `(12, 26, 9)` — verified empirically against `talib.MACD` on 2026-09-21, not derived from documentation.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest packages/market-analyzer/tests/test_indicators.py -v -k macd`
Expected: `ImportError: cannot import name 'MacdResult' from 'ktb_market_analyzer'` (or `macd`).

- [ ] **Step 3: Implement `MacdResult` and `macd`**

Add to `packages/market-analyzer/src/ktb_market_analyzer/indicators.py`, after the module docstring and imports:
```python
from typing import NamedTuple
```
(add this import alongside the existing `numpy`/`talib` imports)

Then add, after the `rsi` function:
```python
class MacdResult(NamedTuple):
    macd: npt.NDArray[np.float64]
    signal: npt.NDArray[np.float64]
    histogram: npt.NDArray[np.float64]


def macd(
    close: npt.NDArray[np.float64],
    fastperiod: int = 12,
    slowperiod: int = 26,
    signalperiod: int = 9,
) -> MacdResult:
    macd_line, signal_line, histogram = talib.MACD(
        close, fastperiod=fastperiod, slowperiod=slowperiod, signalperiod=signalperiod
    )
    return MacdResult(macd=macd_line, signal=signal_line, histogram=histogram)
```

Update `__all__` at the top of the file to `["MacdResult", "rsi", "macd"]` — actually, keep it alphabetized by type-then-name to match ruff's import sort convention: `__all__ = ["MacdResult", "macd", "rsi"]`.

- [ ] **Step 4: Update `__init__.py`**

`packages/market-analyzer/src/ktb_market_analyzer/__init__.py`:
```python
"""Deterministic TA-Lib feature extraction for 척척개미단."""

from ktb_market_analyzer.indicators import MacdResult, macd, rsi

__all__ = ["MacdResult", "macd", "rsi"]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest packages/market-analyzer -v`
Expected: 8 passed (5 existing + 3 new).

- [ ] **Step 6: Lint and type-check**

```bash
uv run ruff format . && uv run ruff check . && uv run ty check
```
Expected: all clean.

- [ ] **Step 7: Commit**

```bash
git add packages/market-analyzer
git commit -m "feat: add macd to ktb_market_analyzer

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: `stochastic`

**Files:**
- Modify: `packages/market-analyzer/src/ktb_market_analyzer/indicators.py` (add `StochasticResult`, `stochastic`)
- Modify: `packages/market-analyzer/src/ktb_market_analyzer/__init__.py` (re-export)
- Test: `packages/market-analyzer/tests/test_indicators.py` (append four tests)

**Interfaces:**
- Consumes: the `_close` / `_high_low` helpers from Task 2.
- Produces: `StochasticResult` (`NamedTuple` with fields `k`, `d`); `stochastic(high, low, close, fastk_period: int = 14, slowk_period: int = 3, slowd_period: int = 3) -> StochasticResult`. Task 6's description registry uses `"stochastic_k"` / `"stochastic_d"` for this function's two outputs.

- [ ] **Step 1: Write the failing tests**

Change the import line in `test_indicators.py` to:
```python
from ktb_market_analyzer import MacdResult, StochasticResult, macd, rsi, stochastic
```

Append:
```python
def test_stochastic_returns_arrays_matching_input_length():
    close = _close()
    high, low = _high_low(close)

    result = stochastic(high, low, close)

    assert isinstance(result, StochasticResult)
    assert result.k.shape == close.shape
    assert result.d.shape == close.shape


def test_stochastic_warmup_period_is_nan():
    close = _close()
    high, low = _high_low(close)

    result = stochastic(high, low, close, fastk_period=14, slowk_period=3, slowd_period=3)

    assert np.isnan(result.k[:17]).all()
    assert np.isnan(result.d[:17]).all()
    assert math.isfinite(result.k[17])
    assert math.isfinite(result.d[17])


def test_stochastic_is_bounded_after_warmup():
    close = _close()
    high, low = _high_low(close)

    result = stochastic(high, low, close)

    assert np.nanmin(result.k) >= 0.0
    assert np.nanmax(result.k) <= 100.0
    assert np.nanmin(result.d) >= 0.0
    assert np.nanmax(result.d) <= 100.0


def test_stochastic_is_deterministic():
    close = _close()
    high, low = _high_low(close)

    first = stochastic(high, low, close)
    second = stochastic(high, low, close)

    assert np.array_equal(first.k, second.k, equal_nan=True)
    assert np.array_equal(first.d, second.d, equal_nan=True)
```

The warmup count of 17 is `(fastk_period - 1) + (slowk_period - 1) + (slowd_period - 1)` = `13 + 2 + 2` for `(14, 3, 3)` — verified empirically against `talib.STOCH` on 2026-09-21. Both `k` and `d` share the same warmup length; TA-Lib aligns both outputs to the deeper of the two lookbacks.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest packages/market-analyzer/tests/test_indicators.py -v -k stochastic`
Expected: `ImportError: cannot import name 'StochasticResult' from 'ktb_market_analyzer'`.

- [ ] **Step 3: Implement `StochasticResult` and `stochastic`**

Add to `indicators.py`, after `MacdResult`/`macd`:
```python
class StochasticResult(NamedTuple):
    k: npt.NDArray[np.float64]
    d: npt.NDArray[np.float64]


def stochastic(
    high: npt.NDArray[np.float64],
    low: npt.NDArray[np.float64],
    close: npt.NDArray[np.float64],
    fastk_period: int = 14,
    slowk_period: int = 3,
    slowd_period: int = 3,
) -> StochasticResult:
    k, d = talib.STOCH(
        high,
        low,
        close,
        fastk_period=fastk_period,
        slowk_period=slowk_period,
        slowd_period=slowd_period,
    )
    return StochasticResult(k=k, d=d)
```

**Do not** add `slowk_matype=0, slowd_matype=0` to this call — see "A validated TA-Lib gotcha" in Global Constraints. Omitting them is correct, not incomplete.

Update `__all__` to `["MacdResult", "StochasticResult", "macd", "rsi", "stochastic"]`.

- [ ] **Step 4: Update `__init__.py`**

```python
"""Deterministic TA-Lib feature extraction for 척척개미단."""

from ktb_market_analyzer.indicators import MacdResult, StochasticResult, macd, rsi, stochastic

__all__ = ["MacdResult", "StochasticResult", "macd", "rsi", "stochastic"]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest packages/market-analyzer -v`
Expected: 12 passed (8 + 4 new).

- [ ] **Step 6: Lint and type-check**

```bash
uv run ruff format . && uv run ruff check . && uv run ty check
```
Expected: all clean. If `ty check` reports `invalid-argument-type` on the `talib.STOCH` call, you passed `slowk_matype`/`slowd_matype` as bare ints — remove them.

- [ ] **Step 7: Commit**

```bash
git add packages/market-analyzer
git commit -m "feat: add stochastic to ktb_market_analyzer

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: `roc`

**Files:**
- Modify: `packages/market-analyzer/src/ktb_market_analyzer/indicators.py` (add `roc`)
- Modify: `packages/market-analyzer/src/ktb_market_analyzer/__init__.py` (re-export)
- Test: `packages/market-analyzer/tests/test_indicators.py` (append three tests)

**Interfaces:**
- Consumes: the `_close` helper from Task 2.
- Produces: `roc(close: npt.NDArray[np.float64], timeperiod: int = 10) -> npt.NDArray[np.float64]`. Task 6's description registry uses `"roc"` for this output.

- [ ] **Step 1: Write the failing tests**

Change the import line to:
```python
from ktb_market_analyzer import MacdResult, StochasticResult, macd, roc, rsi, stochastic
```

Append:
```python
def test_roc_returns_array_matching_input_length():
    close = _close()

    assert roc(close).shape == close.shape


def test_roc_warmup_period_is_nan():
    close = _close()

    result = roc(close, timeperiod=10)

    assert np.isnan(result[:10]).all()
    assert math.isfinite(result[10])


def test_roc_is_deterministic():
    close = _close()

    first = roc(close)
    second = roc(close)

    assert np.array_equal(first, second, equal_nan=True)
```

ROC's warmup count equals `timeperiod` exactly (10 NaNs, value present from index 10) — verified empirically against `talib.ROC` on 2026-09-21. There is no bounds test: rate of change is unbounded in both directions, unlike RSI, %K/%D or Williams %R.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest packages/market-analyzer/tests/test_indicators.py -v -k roc`
Expected: `ImportError: cannot import name 'roc' from 'ktb_market_analyzer'`.

- [ ] **Step 3: Implement `roc`**

Add to `indicators.py`, after `stochastic`:
```python
def roc(
    close: npt.NDArray[np.float64],
    timeperiod: int = 10,
) -> npt.NDArray[np.float64]:
    return talib.ROC(close, timeperiod=timeperiod)
```

Update `__all__` to `["MacdResult", "StochasticResult", "macd", "roc", "rsi", "stochastic"]`.

- [ ] **Step 4: Update `__init__.py`**

```python
"""Deterministic TA-Lib feature extraction for 척척개미단."""

from ktb_market_analyzer.indicators import MacdResult, StochasticResult, macd, roc, rsi, stochastic

__all__ = ["MacdResult", "StochasticResult", "macd", "roc", "rsi", "stochastic"]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest packages/market-analyzer -v`
Expected: 15 passed (12 + 3 new).

- [ ] **Step 6: Lint and type-check**

```bash
uv run ruff format . && uv run ruff check . && uv run ty check
```
Expected: all clean.

- [ ] **Step 7: Commit**

```bash
git add packages/market-analyzer
git commit -m "feat: add roc to ktb_market_analyzer

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: `williams_r`

**Files:**
- Modify: `packages/market-analyzer/src/ktb_market_analyzer/indicators.py` (add `williams_r`)
- Modify: `packages/market-analyzer/src/ktb_market_analyzer/__init__.py` (re-export)
- Test: `packages/market-analyzer/tests/test_indicators.py` (append four tests)

**Interfaces:**
- Consumes: the `_close` / `_high_low` helpers from Task 2.
- Produces: `williams_r(high, low, close, timeperiod: int = 14) -> npt.NDArray[np.float64]`. Task 6's description registry uses `"williams_r"` for this output. This is the last function Task 6 depends on.

- [ ] **Step 1: Write the failing tests**

Change the import line to:
```python
from ktb_market_analyzer import (
    MacdResult,
    StochasticResult,
    macd,
    roc,
    rsi,
    stochastic,
    williams_r,
)
```

Append:
```python
def test_williams_r_returns_array_matching_input_length():
    close = _close()
    high, low = _high_low(close)

    assert williams_r(high, low, close).shape == close.shape


def test_williams_r_warmup_period_is_nan():
    close = _close()
    high, low = _high_low(close)

    result = williams_r(high, low, close, timeperiod=14)

    assert np.isnan(result[:13]).all()
    assert math.isfinite(result[13])


def test_williams_r_is_bounded_after_warmup():
    close = _close()
    high, low = _high_low(close)

    result = williams_r(high, low, close)

    assert np.nanmin(result) >= -100.0
    assert np.nanmax(result) <= 0.0


def test_williams_r_is_deterministic():
    close = _close()
    high, low = _high_low(close)

    first = williams_r(high, low, close)
    second = williams_r(high, low, close)

    assert np.array_equal(first, second, equal_nan=True)
```

Williams %R's warmup count is `timeperiod - 1` (13 NaNs, value present from index 13) — note this differs from RSI's own `timeperiod`-length warmup (14 NaNs); each was verified independently against TA-Lib on 2026-09-21 rather than assumed to match.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest packages/market-analyzer/tests/test_indicators.py -v -k williams_r`
Expected: `ImportError: cannot import name 'williams_r' from 'ktb_market_analyzer'`.

- [ ] **Step 3: Implement `williams_r`**

Add to `indicators.py`, after `roc`:
```python
def williams_r(
    high: npt.NDArray[np.float64],
    low: npt.NDArray[np.float64],
    close: npt.NDArray[np.float64],
    timeperiod: int = 14,
) -> npt.NDArray[np.float64]:
    return talib.WILLR(high, low, close, timeperiod=timeperiod)
```

Update `__all__` to `["MacdResult", "StochasticResult", "macd", "roc", "rsi", "stochastic", "williams_r"]`.

- [ ] **Step 4: Update `__init__.py`**

```python
"""Deterministic TA-Lib feature extraction for 척척개미단."""

from ktb_market_analyzer.indicators import (
    MacdResult,
    StochasticResult,
    macd,
    roc,
    rsi,
    stochastic,
    williams_r,
)

__all__ = ["MacdResult", "StochasticResult", "macd", "roc", "rsi", "stochastic", "williams_r"]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest packages/market-analyzer -v`
Expected: 19 passed (15 + 4 new).

- [ ] **Step 6: Lint and type-check**

```bash
uv run ruff format . && uv run ruff check . && uv run ty check
```
Expected: all clean.

- [ ] **Step 7: Commit**

```bash
git add packages/market-analyzer
git commit -m "feat: add williams_r to ktb_market_analyzer

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 6: Indicator descriptions

**Files:**
- Create: `packages/market-analyzer/src/ktb_market_analyzer/descriptions.py`
- Modify: `packages/market-analyzer/src/ktb_market_analyzer/__init__.py` (re-export `DESCRIPTIONS`)
- Test: `packages/market-analyzer/tests/test_descriptions.py` (new file)

**Interfaces:**
- Consumes: the eight output field names produced by Tasks 1–5 (`rsi`, `macd`, `macd_signal`, `macd_histogram`, `stochastic_k`, `stochastic_d`, `roc`, `williams_r`).
- Produces: `ktb_market_analyzer.DESCRIPTIONS: dict[str, str]`. A future LLM-tool layer looks up a field's one-line English gloss here instead of hardcoding indicator explanations itself.

- [ ] **Step 1: Write the failing tests**

`packages/market-analyzer/tests/test_descriptions.py`:
```python
from ktb_market_analyzer import DESCRIPTIONS

EXPECTED_FIELDS = {
    "rsi",
    "macd",
    "macd_signal",
    "macd_histogram",
    "stochastic_k",
    "stochastic_d",
    "roc",
    "williams_r",
}


def test_covers_every_indicator_output_field():
    assert set(DESCRIPTIONS) == EXPECTED_FIELDS


def test_every_description_is_a_single_line_under_160_chars():
    for text in DESCRIPTIONS.values():
        assert "\n" not in text
        assert 0 < len(text) <= 160
```

The 160-character ceiling exists so a future LLM prompt that lists all eight descriptions alongside their values stays compact; `"\n" not in text` is the "one line" requirement literally.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest packages/market-analyzer/tests/test_descriptions.py -v`
Expected: `ImportError: cannot import name 'DESCRIPTIONS' from 'ktb_market_analyzer'`.

- [ ] **Step 3: Implement `descriptions.py`**

`packages/market-analyzer/src/ktb_market_analyzer/descriptions.py`:
```python
"""One-line, LLM-readable descriptions for every indicator output field."""

DESCRIPTIONS: dict[str, str] = {
    "rsi": (
        "Relative Strength Index (0-100): momentum oscillator; above 70 is typically "
        "overbought, below 30 is typically oversold."
    ),
    "macd": (
        "MACD line: difference between the 12- and 26-period exponential moving averages of price."
    ),
    "macd_signal": (
        "MACD signal line: a 9-period EMA of the MACD line, used to spot "
        "bullish/bearish crossovers."
    ),
    "macd_histogram": (
        "MACD histogram: MACD line minus its signal line; shows momentum strength and direction."
    ),
    "stochastic_k": (
        "Stochastic %K (0-100): closing price relative to its recent high-low range; "
        ">80 overbought, <20 oversold."
    ),
    "stochastic_d": (
        "Stochastic %D (0-100): a smoothed moving average of %K, used to spot crossovers with %K."
    ),
    "roc": (
        "Rate of Change: percent price change over the lookback period; positive is "
        "upward momentum, negative is downward."
    ),
    "williams_r": (
        "Williams %R (-100 to 0): momentum oscillator; above -20 is typically "
        "overbought, below -80 is typically oversold."
    ),
}
```

Each value is wrapped in parenthesized implicit string concatenation across two source lines purely to satisfy `ruff format` at line-length 100 — every value is still a single string with no embedded `\n`, which is what `test_every_description_is_a_single_line_under_160_chars` actually checks. Do not "fix" the multi-line source by joining it onto one editor line; run `ruff format` after editing and let it decide where the ~100-char wrap falls.

- [ ] **Step 4: Update `__init__.py`**

```python
"""Deterministic TA-Lib feature extraction for 척척개미단."""

from ktb_market_analyzer.descriptions import DESCRIPTIONS
from ktb_market_analyzer.indicators import (
    MacdResult,
    StochasticResult,
    macd,
    roc,
    rsi,
    stochastic,
    williams_r,
)

__all__ = [
    "DESCRIPTIONS",
    "MacdResult",
    "StochasticResult",
    "macd",
    "roc",
    "rsi",
    "stochastic",
    "williams_r",
]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest packages/market-analyzer -v`
Expected: 21 passed (19 + 2 new).

- [ ] **Step 6: Lint and type-check**

```bash
uv run ruff format . && uv run ruff check . && uv run ty check
```
Expected: all clean.

- [ ] **Step 7: Commit**

```bash
git add packages/market-analyzer
git commit -m "feat: add LLM-readable descriptions for every indicator output field

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Done criteria

On a clean checkout, after all six tasks:

1. `uv run pytest packages/market-analyzer -v` passes with 21 tests.
2. `uv run ruff format --check .`, `uv run ruff check .` and `uv run ty check` all pass across the whole workspace (not just this package).
3. `ktb_market_analyzer.__all__` exposes exactly: `DESCRIPTIONS`, `MacdResult`, `StochasticResult`, `macd`, `roc`, `rsi`, `stochastic`, `williams_r`.
4. `ktb_market_analyzer` still declares zero first-party dependencies and no third-party dependency beyond `ta-lib` and `numpy` (`packages/market-analyzer/pyproject.toml` is unchanged by this plan).
5. No function in this package returns or computes a BUY/SELL/HOLD-style label, and no function performs any I/O.
