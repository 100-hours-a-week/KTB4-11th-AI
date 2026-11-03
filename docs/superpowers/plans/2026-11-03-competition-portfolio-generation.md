# Competition Portfolio Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate a portfolio per competition participant from its frozen settings, consumed off SQS, stored alongside the common model portfolio without either one reaching the other.

**Architecture:** A second console script on `portfolio-builder` runs a long-lived SQS worker beside the existing cron entry point, reusing `run_agent`, every tool, and `explain.py`. Ownership lives in three new tables so one portfolio row can be shared by participants whose settings and prior state match; a `generation_key` unique constraint is both the sharing mechanism and the duplicate-run guard. The rebalancer is untouched, so competition portfolios are generated and stored but not traded until stage 2.

**Tech Stack:** Python 3.13, LangChain `create_agent` on OpenRouter, SQLAlchemy Core + PostgreSQL, Alembic, boto3 SQS, Pydantic / pydantic-settings, pytest.

**Spec:** `docs/superpowers/specs/2026-11-02-ai-vs-ai-competition-portfolio-design.md`

## Global Constraints

- This is **stage 1 of two**. `portfolio-rebalancer` is not modified by any task here. Competition portfolios are stored and never traded in this stage.
- Risk profile is exactly these five strings, no spaces: `성장형`, `성장추구형`, `위험중립형`, `안정추구형`, `안정형`. No normalisation anywhere.
- `additional_request` is stored and hashed **verbatim**. No trimming, case folding, or whitespace collapsing.
- `theme_companies` is keyed by `stock_code`, not `corp_code` (migration `0006`), and the flag column is `is_major`. Portfolio `company_id` is `corporations.corp_code`, so every theme lookup is two hops: `theme_companies.stock_code -> corporations.stock_code -> corporations.corp_code`. Joining on `corp_code` matches nothing and silently disables every exclusion check.
- No ownership column on `portfolios`. One new nullable column only: `acknowledged_constraints jsonb NULL`.
- Env prefix `PORTFOLIO_BUILDER_` for everything; one `settings.py` for the member (AGENTS.md).
- Python `>=3.13,<3.15`. ruff `E,F,I,UP,B`, line length 100. `uv run ruff format` must be clean.
- **Never write comments or docstrings** (AGENTS.md). No thin wrappers or helpers with a single caller.
- Database tests run only when `KTB_TEST_POSTGRES_DSN` is set and TRUNCATE tables; they point at `ktb_test`, never `ktb`.
- After any dependency change: `uv lock`, then `uv export --package portfolio-builder --no-dev --no-emit-workspace --format requirements-txt -o docker/requirements/portfolio-builder.txt` and `uv export --all-packages --no-dev --group migrations --no-emit-workspace --format requirements-txt -o docker/requirements/app.txt`, using exactly those `-o` paths.

**Base branch:** `feat/479-competition-portfolio-generation`, branched from `dev` at `641a1ab`. GitHub issue #479. PRs target `dev`, never `main`.

**The disclosure integration is not in this base.** The spec was written against a working tree that had it, but on `dev` the portfolio-builder agent has no `search_disclosures` tool, no `RequireDisclosures` middleware, and no disclosure rules in `SYSTEM_PROMPT`. That work is a three-deep stack of open PRs (#430 -> #432 -> #441 -> `dev`) carrying `retrieval_client.py`, `disclosures.py`, `tools/disclosures.py` and `agent/hooks/require_disclosures.py`.

Consequences for this plan, and nothing more: Task 8's competition prompt mirrors `dev`'s mandatory-analysis rule, which is `analyze_technicals` alone, and Task 11 wires `dev`'s tool list. Every other task is untouched by it. When the stack merges, `COMPETITION_SYSTEM_PROMPT` needs the disclosure rule added beside the technicals rule and the competition worker's tool list needs `disclosure_tools`, in the same commit that resolves the conflict in `system_prompt.py` and `__main__.py`. Do not add either ahead of the merge: a tool that does not exist on this base cannot be built or tested here.

**Migration head on this base:** `0011` (`0011_add_corporation_sector.py`), verified as the single head. This plan's migration is therefore `0012_create_competition_portfolios.py` with `down_revision = "0011"`.

The `0011` collision warned about earlier does not exist here: the competing `0011_create_disclosures.py` is an untracked file in the main checkout's working tree and travels with the disclosure stack, not with `dev`. Re-run `uv run alembic heads` before Task 1 anyway -- if the disclosure stack merges into `dev` mid-implementation and this branch rebases, the head moves and `0012` may collide in exactly the same way.

**Verified baseline on this worktree:** 1014 passed, 0 failed, 18 skipped (Valkey-only, needing `KTB_TEST_VALKEY_URL`). Every database test in this plan runs against `ktb_test`, migrated to head:

```bash
docker compose -f compose.local.yaml up -d postgres
KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test uv run pytest
```

Local note: after any `uv sync`, run `chflags nohidden .venv/lib/python3.13/site-packages/*.pth` or every workspace import fails in subprocess-spawned tests.

## Review Focus

Inputs the spec implies that no task's main tests exercise, most likely to bite first. Each has a test assigned in the task that owns the code.

1. **An `additional_request` of unbounded length.** Nothing caps it, and it goes into the briefing on every turn, so a megabyte of free text inflates every model call in the run. A reasonable person expects a long prompt to be truncated or rejected, not to silently multiply cost. → Task 2.
2. **The same theme code in both `interested_theme_codes` and `excluded_theme_codes`.** The user contradicted themselves and the spec's conflict rule does not cover two selected settings colliding. Exclusion must win, as the stricter of the two. → Task 2.
3. **A theme code valid at participant creation that no longer exists in `themes` on a later round.** Settings are frozen but `market-syncer` rewrites `themes`, so a stale excluded code would resolve to an empty company set and exclusion would quietly stop working mid-competition. → Task 6.
4. **Excluded themes covering every candidate company.** The agent has nothing it may hold, burns `max_turns`, and the job fails with no explanation a user could act on. → Task 6.
5. **An empty `additional_request`.** The common case for a user who only picked from menus. The briefing must not render a dangling empty section, and the key must still be stable. → Task 8.

---

### Task 1: Schema

**Files:**
- Create: `infrastructure/postgres/migrations/versions/0012_create_competition_portfolios.py`
- Modify: `services/portfolio-builder/src/portfolio_builder/database.py`
- Modify: `services/portfolio-builder/tests/conftest.py` (the `TABLES` truncate string)
- Test: `services/portfolio-builder/tests/test_competition_schema.py`

**Interfaces:**
- Consumes: nothing.
- Produces: SQLAlchemy `Table` objects `competition_participants`, `competition_portfolios`, `competition_jobs` exported from `portfolio_builder.database`, and `portfolios.acknowledged_constraints`.

Table shapes are in the spec's *Schema* section; copy them exactly. `competition_jobs.status` gets a check constraint over `('running', 'succeeded', 'failed')`, matching how `portfolios_status_check` is written in `0008_add_portfolio_status.py`.

- [ ] **Step 1: Run `uv run alembic heads` and confirm exactly one head**

Expected: one revision id. If two are printed, stop and resolve the `0011` collision described in the prerequisite before continuing.

- [ ] **Step 2: Write the failing test**

```python
def test_competition_tables_accept_a_participant_a_job_and_a_shared_link(engine):
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO competition_participants"
            " (participant_id, competition_id, user_id, risk_profile,"
            " interested_theme_codes, excluded_theme_codes, additional_request)"
            " VALUES (1, 10, 100, '성장형', '{T1}', '{}', ''), (2, 10, 100, '성장형', '{T1}', '{}', '')"
        ))
        portfolio_id = conn.execute(sa.text(
            "INSERT INTO portfolios (cash_weight, commentary, model)"
            " VALUES (0.1, 'c', 'm') RETURNING id"
        )).scalar_one()
        conn.execute(sa.text(
            "INSERT INTO competition_portfolios (participant_id, portfolio_id)"
            " VALUES (1, :p), (2, :p)"
        ), {"p": portfolio_id})
        linked = conn.execute(sa.text(
            "SELECT count(*) FROM competition_portfolios WHERE portfolio_id = :p"
        ), {"p": portfolio_id}).scalar_one()
    assert linked == 2


def test_generation_key_is_unique(engine):
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO competition_participants"
            " (participant_id, competition_id, user_id, risk_profile, additional_request)"
            " VALUES (1, 10, 100, '성장형', '')"
        ))
        conn.execute(sa.text(
            "INSERT INTO competition_jobs (generation_key, participant_id, status)"
            " VALUES ('k', 1, 'running')"
        ))
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as conn:
            conn.execute(sa.text(
                "INSERT INTO competition_jobs (generation_key, participant_id, status)"
                " VALUES ('k', 1, 'running')"
            ))


def test_acknowledged_constraints_defaults_to_null(engine):
    with engine.begin() as conn:
        stored = conn.execute(sa.text(
            "INSERT INTO portfolios (cash_weight, commentary, model)"
            " VALUES (0.1, 'c', 'm') RETURNING acknowledged_constraints"
        )).scalar_one()
    assert stored is None


def test_competition_jobs_rejects_an_unknown_status(engine):
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO competition_participants"
            " (participant_id, competition_id, user_id, risk_profile, additional_request)"
            " VALUES (1, 10, 100, '성장형', '')"
        ))
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as conn:
            conn.execute(sa.text(
                "INSERT INTO competition_jobs (generation_key, participant_id, status)"
                " VALUES ('k', 1, 'pending')"
            ))
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test uv run pytest services/portfolio-builder/tests/test_competition_schema.py -v`
Expected: FAIL, relation `competition_participants` does not exist.

- [ ] **Step 4: Write the migration**

Chain `down_revision` to the single head from Step 1. `upgrade()` creates the three tables and adds `portfolios.acknowledged_constraints`; `downgrade()` reverses in the opposite order. Follow `0007_create_portfolio_reasons.py` for formatting, including the module docstring that every migration in this directory carries.

- [ ] **Step 5: Add the three tables to `database.py` and `acknowledged_constraints` to the `portfolios` table**

Mirror the column types from the migration. `interested_theme_codes` and `excluded_theme_codes` are `postgresql.ARRAY(sa.Text)`; `acknowledged_constraints` is `postgresql.JSONB`.

- [ ] **Step 6: Add the new tables to the front of `TABLES` in `conftest.py`**

They must precede `portfolios` in the TRUNCATE list, as the existing entries are ordered dependents-first.

- [ ] **Step 7: Apply and run the tests**

Run: `KTB_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test uv run alembic upgrade head` then the pytest command from Step 3.
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add infrastructure/postgres/migrations/versions services/portfolio-builder/src/portfolio_builder/database.py services/portfolio-builder/tests/conftest.py services/portfolio-builder/tests/test_competition_schema.py
git commit -m "feat: add competition portfolio tables"
```

---

### Task 2: Message Contract

**Files:**
- Create: `services/portfolio-builder/src/portfolio_builder/competition/__init__.py` (empty)
- Create: `services/portfolio-builder/src/portfolio_builder/competition/message.py`
- Test: `services/portfolio-builder/tests/test_competition_message.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `RiskProfile = Literal["성장형", "성장추구형", "위험중립형", "안정추구형", "안정형"]`
  - `class Request(BaseModel)` with `competition_id: Annotated[StrictInt, Field(gt=0)]`, `participant_id`, `user_id` the same, `risk_profile: RiskProfile`, `interested_theme_codes: list[str] = []`, `excluded_theme_codes: list[str] = []`, `additional_request: str = ""`
  - `Request.effective_excluded` and `Request.effective_interested` properties returning `list[str]`, deduplicated and sorted, with any code present in both removed from interested.

Follow `report_builder/worker.py`'s `Request` for the identifier fields. `MAX_ADDITIONAL_REQUEST_CHARS = 4000` is module-level and enforced by a `Field(max_length=...)` on `additional_request`, so an over-long prompt fails validation and the message is discarded rather than inflating every turn.

- [ ] **Step 1: Write the failing test**

```python
PROFILES = ["성장형", "성장추구형", "위험중립형", "안정추구형", "안정형"]
BASE = {"competition_id": 10, "participant_id": 1, "user_id": 100, "risk_profile": "성장형"}


@pytest.mark.parametrize("profile", PROFILES)
def test_every_risk_profile_validates(profile):
    assert Request(**{**BASE, "risk_profile": profile}).risk_profile == profile


@pytest.mark.parametrize("profile", ["성장 추구형", "위험 중립형", "안정 추구형", "", "growth"])
def test_spaced_or_unknown_risk_profile_is_rejected(profile):
    with pytest.raises(ValidationError):
        Request(**{**BASE, "risk_profile": profile})


@pytest.mark.parametrize(
    "changes",
    [
        {"competition_id": "bad"},
        {"participant_id": None},
        {"participant_id": 0},
        {"user_id": -1},
    ],
)
def test_invalid_identifiers_are_rejected(changes):
    with pytest.raises(ValidationError):
        Request(**{**BASE, **changes})


def test_additional_request_defaults_to_empty_and_is_kept_verbatim():
    assert Request(**BASE).additional_request == ""
    text = "  반도체만 담아줘\n"
    assert Request(**{**BASE, "additional_request": text}).additional_request == text


def test_additional_request_over_the_limit_is_rejected():
    with pytest.raises(ValidationError):
        Request(**{**BASE, "additional_request": "가" * (MAX_ADDITIONAL_REQUEST_CHARS + 1)})


def test_a_theme_in_both_lists_is_excluded_and_not_interested():
    request = Request(
        **BASE, interested_theme_codes=["T1", "T2"], excluded_theme_codes=["T2"]
    )
    assert request.effective_interested == ["T1"]
    assert request.effective_excluded == ["T2"]


def test_duplicate_theme_codes_collapse_and_sort():
    request = Request(**BASE, interested_theme_codes=["T2", "T1", "T2"])
    assert request.effective_interested == ["T1", "T2"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest services/portfolio-builder/tests/test_competition_message.py -v`
Expected: FAIL, cannot import `Request`.

- [ ] **Step 3: Implement `RiskProfile`, `MAX_ADDITIONAL_REQUEST_CHARS`, and `Request` in `competition/message.py`**

- [ ] **Step 4: Run the test to verify it passes**

Run: the command from Step 2.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/portfolio-builder/src/portfolio_builder/competition services/portfolio-builder/tests/test_competition_message.py
git commit -m "feat: validate competition generation requests"
```

---

### Task 3: Frozen Settings Snapshot

**Files:**
- Create: `services/portfolio-builder/src/portfolio_builder/competition/repository.py`
- Test: `services/portfolio-builder/tests/test_competition_repository.py`

**Interfaces:**
- Consumes: `Request` (Task 2), `competition_participants` (Task 1).
- Produces:
  - `@dataclass(frozen=True) class ParticipantSettings` with `participant_id: int`, `competition_id: int`, `user_id: int`, `risk_profile: str`, `interested_theme_codes: tuple[str, ...]`, `excluded_theme_codes: tuple[str, ...]`, `additional_request: str`
  - `store_settings(engine: sa.Engine, request: Request) -> tuple[ParticipantSettings, bool]` -- the stored snapshot and whether the incoming request differed from it

Named `ParticipantSettings`, not `Settings`: `portfolio_builder.settings.Settings` is the pydantic-settings class, and Task 11 holds both at once.

Insert with `ON CONFLICT (participant_id) DO NOTHING`, then read the row back and return it. The second element is `True` when any stored field differs from the request, so the caller can log the warning; it is never `True` on the first message. Name it in the destructuring at the call site rather than wrapping it in another type.

- [ ] **Step 1: Write the failing test**

```python
def _request(**changes):
    return Request(
        competition_id=10, participant_id=1, user_id=100, risk_profile="성장형",
        interested_theme_codes=["T1"], excluded_theme_codes=[], additional_request="원문",
        **changes,
    )


def test_first_message_stores_the_snapshot(engine):
    settings, changed = store_settings(engine, _request())

    assert not changed
    assert settings.risk_profile == "성장형"
    assert settings.interested_theme_codes == ("T1",)
    assert settings.additional_request == "원문"


def test_later_message_keeps_the_stored_snapshot_and_reports_the_difference(engine):
    store_settings(engine, _request())

    settings, changed = store_settings(
        engine, _request(risk_profile="안정형", additional_request="다른 원문")
    )

    assert changed
    assert settings.risk_profile == "성장형"
    assert settings.additional_request == "원문"


def test_an_identical_later_message_reports_no_difference(engine):
    store_settings(engine, _request())

    _, changed = store_settings(engine, _request())

    assert not changed
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test uv run pytest services/portfolio-builder/tests/test_competition_repository.py -v`
Expected: FAIL, cannot import `store_settings`.

- [ ] **Step 3: Implement `ParticipantSettings` and `store_settings(engine, request) -> tuple[ParticipantSettings, bool]` in `competition/repository.py`**

- [ ] **Step 4: Run the test to verify it passes**

- [ ] **Step 5: Commit**

```bash
git add services/portfolio-builder/src/portfolio_builder/competition/repository.py services/portfolio-builder/tests/test_competition_repository.py
git commit -m "feat: freeze competition settings on the first request"
```

---

### Task 4: generation_key and Job Claiming

**Files:**
- Modify: `services/portfolio-builder/src/portfolio_builder/competition/repository.py`
- Test: `services/portfolio-builder/tests/test_competition_repository.py`

**Interfaces:**
- Consumes: `ParticipantSettings` (Task 3), `competition_jobs` (Task 1).
- Produces:
  - `generation_key(participant: ParticipantSettings, previous_portfolio_id: int | None, llm_model: str) -> str`
  - `@dataclass(frozen=True) class Claim` with `key: str`, `mine: bool`, `portfolio_id: int | None`
  - `claim_job(engine: sa.Engine, key: str, participant: ParticipantSettings, previous_portfolio_id: int | None, stale_after_seconds: int, max_attempts: int) -> Claim | None`
  - `finish_job(engine: sa.Engine, key: str, portfolio_id: int) -> None`
  - `fail_job(engine: sa.Engine, key: str, error: str) -> None`

`generation_key` hashes, in this order, with a separator that cannot appear in a theme code or risk profile: the literal `"v1"`, `risk_profile`, the `interested_theme_codes` tuple, the `excluded_theme_codes` tuple, `additional_request` verbatim, `previous_portfolio_id or 0`, `llm_model`. Return the hex digest.

`claim_job` returns `Claim(mine=True)` when this caller must generate, `Claim(mine=False, portfolio_id=...)` when a finished job already holds the result, and `None` when another worker is generating right now or the attempt limit is spent. A `running` row whose `started_at` is older than `stale_after_seconds` is reclaimable and bumps `attempts`; so is a `failed` row under `max_attempts`.

- [ ] **Step 1: Write the failing test**

```python
MODEL = "openrouter/m"


def test_same_settings_and_previous_portfolio_give_the_same_key():
    a = _settings()
    b = _settings()
    assert generation_key(a, None, MODEL) == generation_key(b, None, MODEL)


@pytest.mark.parametrize(
    "changes",
    [
        {"risk_profile": "안정형"},
        {"additional_request": "원문 "},
        {"interested_theme_codes": ("T1", "T2")},
        {"excluded_theme_codes": ("T9",)},
    ],
)
def test_any_setting_difference_changes_the_key(changes):
    assert generation_key(_settings(**changes), None, MODEL) != generation_key(
        _settings(), None, MODEL
    )


def test_a_different_previous_portfolio_changes_the_key():
    settings = _settings()
    assert generation_key(settings, 1, MODEL) != generation_key(settings, 2, MODEL)
    assert generation_key(settings, None, MODEL) == generation_key(settings, 0, MODEL)


def test_a_different_model_changes_the_key():
    assert generation_key(_settings(), None, "other") != generation_key(_settings(), None, MODEL)


def test_theme_code_order_does_not_change_the_key():
    assert generation_key(_settings(interested_theme_codes=("T1", "T2")), None, MODEL) == (
        generation_key(_settings(interested_theme_codes=("T1", "T2")), None, MODEL)
    )


def test_the_first_claim_is_mine_and_a_concurrent_claim_is_not(engine):
    settings, _ = store_settings(engine, _request())
    key = generation_key(settings, None, MODEL)

    first = claim_job(engine, key, settings, None, 3600, 3)
    second = claim_job(engine, key, settings, None, 3600, 3)

    assert first.mine
    assert second is None


def test_a_finished_job_hands_back_its_portfolio_without_generating(engine):
    settings, _ = store_settings(engine, _request())
    key = generation_key(settings, None, MODEL)
    claim_job(engine, key, settings, None, 3600, 3)
    portfolio_id = _insert_portfolio(engine)
    finish_job(engine, key, portfolio_id)

    claim = claim_job(engine, key, settings, None, 3600, 3)

    assert not claim.mine
    assert claim.portfolio_id == portfolio_id


def test_a_stale_running_job_is_reclaimable(engine):
    settings, _ = store_settings(engine, _request())
    key = generation_key(settings, None, MODEL)
    claim_job(engine, key, settings, None, 3600, 3)

    assert claim_job(engine, key, settings, None, 0, 3).mine


def test_a_failed_job_is_retried_until_the_attempt_limit(engine):
    settings, _ = store_settings(engine, _request())
    key = generation_key(settings, None, MODEL)
    claim_job(engine, key, settings, None, 3600, 2)
    fail_job(engine, key, "boom")

    assert claim_job(engine, key, settings, None, 3600, 2).mine
    fail_job(engine, key, "boom")
    assert claim_job(engine, key, settings, None, 3600, 2) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test uv run pytest services/portfolio-builder/tests/test_competition_repository.py -v`
Expected: FAIL, cannot import `generation_key`.

- [ ] **Step 3: Implement `generation_key`, `Claim`, `claim_job`, `finish_job`, `fail_job`**

`claim_job` does the insert with `ON CONFLICT (generation_key) DO NOTHING ... RETURNING generation_key` so a returned row means this caller won the race. On conflict, re-read the row in the same transaction with `FOR UPDATE` and decide from its `status`, `started_at` and `attempts`.

- [ ] **Step 4: Run the tests to verify they pass**

- [ ] **Step 5: Commit**

```bash
git add services/portfolio-builder/src/portfolio_builder/competition/repository.py services/portfolio-builder/tests/test_competition_repository.py
git commit -m "feat: deduplicate and share competition generations by key"
```

---

### Task 5: Participant Portfolio History

**Files:**
- Modify: `services/portfolio-builder/src/portfolio_builder/competition/repository.py`
- Modify: `services/portfolio-builder/src/portfolio_builder/briefing/repository.py`
- Modify: `services/portfolio-builder/src/portfolio_builder/explain.py` (`load_unexplained`)
- Test: `services/portfolio-builder/tests/test_competition_repository.py`
- Test: `services/portfolio-builder/tests/test_explain.py`

**Interfaces:**
- Consumes: `competition_portfolios` (Task 1).
- Produces:
  - `link_portfolio(engine: sa.Engine, participant_id: int, portfolio_id: int) -> None` in `competition/repository.py`, idempotent via `ON CONFLICT DO NOTHING`
  - `latest_participant_portfolio(engine: sa.Engine, participant_id: int) -> tuple[int, str, bool] | None` in `competition/repository.py` -- `(portfolio_id, status, has_trace)` for the newest linked row **regardless of status**, or `None`
  - `find_participant_portfolio(conn: sa.Connection, participant_id: int) -> PreviousPortfolio | None` in `briefing/repository.py`, the participant-scoped twin of `find_previous_portfolio`
  - `load_unexplained` keeps its signature and gains the same not-linked condition

`find_participant_portfolio` reuses `find_previous_portfolio`'s holdings and exits queries verbatim and differs only in how the portfolio row is chosen: joined to `competition_portfolios` for this participant, ordered by `portfolio_id DESC LIMIT 1`. Restructure `find_previous_portfolio` so the two share the holdings and exits loading rather than duplicating the SQL, and give the common path the added condition that the portfolio has **no** row in `competition_portfolios`.

`load_unexplained` in `explain.py` needs the identical condition, and this is the one place in the plan where omitting it corrupts existing behaviour rather than merely failing: the cron entry point calls it first and re-explains whatever it returns instead of building. One competition portfolio stuck at `explanation_failed` would otherwise consume the next morning's common-portfolio run, and no common portfolio would be produced that day.

- [ ] **Step 1: Write the failing test**

```python
def test_the_latest_participant_portfolio_ignores_status(engine):
    settings, _ = store_settings(engine, _request())
    first = _insert_portfolio(engine, status="ready")
    second = _insert_portfolio(engine, status="explanation_failed", trace=[{"k": "v"}])
    link_portfolio(engine, settings.participant_id, first)
    link_portfolio(engine, settings.participant_id, second)

    assert latest_participant_portfolio(engine, settings.participant_id) == (
        second, "explanation_failed", True
    )


def test_a_participant_with_no_portfolio_has_no_history(engine):
    settings, _ = store_settings(engine, _request())

    assert latest_participant_portfolio(engine, settings.participant_id) is None


def test_linking_is_idempotent_and_two_participants_can_share_one_portfolio(engine):
    store_settings(engine, _request())
    store_settings(engine, _request(participant_id=2))
    portfolio_id = _insert_portfolio(engine)

    link_portfolio(engine, 1, portfolio_id)
    link_portfolio(engine, 1, portfolio_id)
    link_portfolio(engine, 2, portfolio_id)

    assert latest_participant_portfolio(engine, 1)[0] == portfolio_id
    assert latest_participant_portfolio(engine, 2)[0] == portfolio_id


def test_the_common_path_does_not_see_a_competition_portfolio(engine):
    store_settings(engine, _request())
    common = _insert_portfolio(engine)
    competition = _insert_portfolio(engine)
    link_portfolio(engine, 1, competition)

    with engine.connect() as conn:
        previous = find_previous_portfolio(conn)

    assert previous.portfolio["id"] == common


def test_the_participant_path_sees_only_its_own_portfolio(engine):
    store_settings(engine, _request())
    _insert_portfolio(engine)
    mine = _insert_portfolio(engine)
    link_portfolio(engine, 1, mine)

    with engine.connect() as conn:
        previous = find_participant_portfolio(conn, 1)

    assert previous.portfolio["id"] == mine
```

And in `test_explain.py`:

```python
def test_load_unexplained_ignores_a_competition_portfolio(engine):
    store_settings(engine, _request())
    competition = _insert_portfolio(engine, status="explanation_failed", trace=[{"k": "v"}])
    link_portfolio(engine, 1, competition)

    assert load_unexplained(engine) is None


def test_load_unexplained_still_finds_a_common_portfolio(engine):
    store_settings(engine, _request())
    common = _insert_portfolio(engine, status="explanation_failed", trace=[{"k": "v"}])
    linked = _insert_portfolio(engine, status="explanation_failed", trace=[{"k": "v"}])
    link_portfolio(engine, 1, linked)

    portfolio_id, _ = load_unexplained(engine)

    assert portfolio_id == common
```

The second test matters more than the first: the competition row is the newer one, so a
`load_unexplained` that merely ordered differently would still pass the first test.

- [ ] **Step 2: Run the tests to verify they fail**

Expected: FAIL, cannot import `link_portfolio`.

- [ ] **Step 3: Implement the three functions, rework `find_previous_portfolio`, and add the condition to `load_unexplained`**

- [ ] **Step 4: Run the full portfolio-builder suite**

Run: `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test uv run pytest services/portfolio-builder -v`
Expected: PASS, including the existing `test_briefing.py`, which pins the common path's behaviour.

- [ ] **Step 5: Commit**

```bash
git add services/portfolio-builder/src/portfolio_builder services/portfolio-builder/tests
git commit -m "feat: keep competition portfolios out of the common portfolio path"
```

---

### Task 6: Excluded Themes

**Files:**
- Modify: `services/portfolio-builder/src/portfolio_builder/portfolio.py`
- Modify: `services/portfolio-builder/src/portfolio_builder/tools/submit.py`
- Test: `services/portfolio-builder/tests/test_submit.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `excluded_company_ids(engine: sa.Engine, excluded_theme_codes: Sequence[str]) -> frozenset[str]` in `portfolio.py`
  - `validate_portfolio(submission: Submission, previous: frozenset[str], excluded: frozenset[str] = frozenset()) -> list[str]`
  - `submit_tool(..., excluded: frozenset[str] = frozenset())`, bound through to `submit_portfolio` the way `previous` already is

The query is the two-hop join from Global Constraints and returns an empty set for an empty code list without touching the database. The error message for a rejected holding reads `f"holdings: {company} is in an excluded theme"`, appended inside the existing holdings loop so it joins the other errors in one rejection. Exits are never rejected for exclusion -- dropping an excluded holding is the behaviour we want.

- [ ] **Step 1: Write the failing test**

The `engine` fixture already seeds theme `T1` containing `005930` (Samsung, `corp_code` `00126380`) and `000660` (SK Hynix, `00164779`), and `373220` (LG Energy Solution, `01515323`) belongs to no theme.

```python
HYNIX = "00164779"
LGES = "01515323"


def test_excluded_company_ids_resolves_corp_codes_through_stock_code(engine):
    assert excluded_company_ids(engine, ["T1"]) == frozenset({SAMSUNG, HYNIX})


def test_no_excluded_themes_resolves_to_an_empty_set(engine):
    assert excluded_company_ids(engine, []) == frozenset()


def test_an_unknown_theme_code_resolves_to_an_empty_set(engine):
    assert excluded_company_ids(engine, ["T404"]) == frozenset()


def test_a_holding_in_an_excluded_theme_is_rejected(engine, news_client):
    tool = submit_tool(
        engine, frozenset(), "m", news_client, Recorder(), excluded=frozenset({SAMSUNG})
    )

    with pytest.raises(PortfolioRejected) as rejected:
        tool.invoke(_call(VALID))

    assert f"holdings: {SAMSUNG} is in an excluded theme" in rejected.value.errors
    assert _rows(engine) == []


def test_a_company_outside_every_excluded_theme_is_accepted(engine, news_client):
    tool = submit_tool(
        engine, frozenset(), "m", news_client, Recorder(), excluded=frozenset({SAMSUNG})
    )
    holding = {"company_id": LGES, "weight": 3, "reason": "r", "cited_cluster_ids": [1]}

    tool.invoke(_call({**VALID, "holdings": [holding]}))

    assert len(_rows(engine)) == 1


def test_an_excluded_previous_holding_can_be_exited(engine, news_client):
    tool = submit_tool(
        engine, frozenset({SAMSUNG}), "m", news_client, Recorder(),
        excluded=frozenset({SAMSUNG}),
    )
    submission = {
        **VALID,
        "holdings": [{"company_id": LGES, "weight": 3, "reason": "r", "cited_cluster_ids": [1]}],
        "exits": [{"company_id": SAMSUNG, "reason": "제외 테마", "cited_cluster_ids": [1]}],
    }

    tool.invoke(_call(submission))

    assert len(_rows(engine)) == 1


def test_exclusion_errors_join_the_other_errors_in_one_rejection(engine, news_client):
    tool = submit_tool(
        engine, frozenset(), "m", news_client, Recorder(), excluded=frozenset({SAMSUNG})
    )

    with pytest.raises(PortfolioRejected) as rejected:
        tool.invoke(_call({**VALID, "commentary": ""}))

    assert len(rejected.value.errors) == 2
```

Review Focus 3 and 4 (a theme code that has vanished from `themes`; every candidate excluded):

```python
def test_a_theme_code_no_longer_in_themes_excludes_nothing(engine):
    with engine.begin() as conn:
        conn.execute(sa.text("DELETE FROM theme_companies WHERE theme_code = 'T1'"))
        conn.execute(sa.text("DELETE FROM themes WHERE theme_code = 'T1'"))

    assert excluded_company_ids(engine, ["T1"]) == frozenset()


def test_excluding_every_company_leaves_a_cash_only_portfolio_submittable(engine, news_client):
    every = excluded_company_ids(engine, ["T1"]) | {LGES}
    tool = submit_tool(engine, frozenset(), "m", news_client, Recorder(), excluded=every)

    tool.invoke(_call({**VALID, "holdings": [], "cash_weight": 1}))

    [(_, cash_weight)] = _rows(engine)
    assert cash_weight == pytest.approx(1.0)
```

A vanished theme code silently excludes nothing, which is the behaviour the test pins so the log line in Task 11 is the only warning; and total exclusion must still leave an all-cash portfolio submittable rather than making every submission impossible.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test uv run pytest services/portfolio-builder/tests/test_submit.py -v`
Expected: FAIL, cannot import `excluded_company_ids`.

- [ ] **Step 3: Implement `excluded_company_ids`, extend `validate_portfolio`, thread `excluded` through `submit_tool`**

- [ ] **Step 4: Run the tests to verify they pass, including the pre-existing cases in this file**

- [ ] **Step 5: Commit**

```bash
git add services/portfolio-builder/src/portfolio_builder/portfolio.py services/portfolio-builder/src/portfolio_builder/tools/submit.py services/portfolio-builder/tests/test_submit.py
git commit -m "feat: reject holdings in an excluded theme"
```

---

### Task 7: Declared Constraints

**Files:**
- Modify: `services/portfolio-builder/src/portfolio_builder/portfolio.py`
- Modify: `services/portfolio-builder/src/portfolio_builder/tools/submit.py`
- Test: `services/portfolio-builder/tests/test_portfolio.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `class Constraints(BaseModel)` with `max_holdings: int | None = None`, `max_weight_per_holding: float | None = None`, `min_cash_weight: float | None = None`
  - `Submission.constraints: Constraints = Constraints()`
  - `validate_constraints(submission: Submission) -> list[str]`, called from `validate_portfolio` on the **normalised** submission
  - `SubmitPortfolioArgs.constraints: Constraints` with a `Field` description telling the model to report only numeric limits it actually read in the user's request
  - `save_portfolio` writes `acknowledged_constraints` from `submission.constraints`, as `None` when every field is `None`

Ordering matters: the three limits are measured after `normalize_weights`, where holdings plus `cash_weight` sum to 1, so `validate_portfolio` runs the normalised submission through `validate_constraints` while keeping its existing checks on the raw one. Error texts: `f"constraints: {len(holdings)} holdings exceeds max_holdings {n}"`, `f"constraints: {company} weight {w} exceeds max_weight_per_holding {limit}"`, `f"constraints: cash_weight {c} is below min_cash_weight {limit}"`.

- [ ] **Step 1: Write the failing test**

```python
def _submission(holdings, cash_weight=1.0, **constraints):
    return Submission(
        holdings=[
            Holding(company_id=c, weight=w, reason="r", cited_cluster_ids=[]) for c, w in holdings
        ],
        exits=[],
        cash_weight=cash_weight,
        commentary="c",
        constraints=Constraints(**constraints),
    )


def test_no_declared_constraints_reject_nothing():
    assert validate_constraints(_submission([("a", 0.9)], cash_weight=0.1)) == []


def test_too_many_holdings_is_rejected():
    submission = _submission([("a", 0.3), ("b", 0.3)], cash_weight=0.4, max_holdings=1)
    assert validate_constraints(submission) == [
        "constraints: 2 holdings exceeds max_holdings 1"
    ]


def test_an_overweight_holding_is_rejected():
    submission = _submission([("a", 0.5)], cash_weight=0.5, max_weight_per_holding=0.25)
    assert len(validate_constraints(submission)) == 1


def test_too_little_cash_is_rejected():
    submission = _submission([("a", 0.9)], cash_weight=0.1, min_cash_weight=0.2)
    assert len(validate_constraints(submission)) == 1


def test_limits_are_measured_against_normalised_weights_including_cash():
    raw = _submission([("a", 3.0)], cash_weight=1.0, max_weight_per_holding=0.8)
    assert validate_constraints(normalize_weights(raw)) == []
    stricter = _submission([("a", 3.0)], cash_weight=1.0, max_weight_per_holding=0.7)
    assert len(validate_constraints(normalize_weights(stricter))) == 1


def test_a_submission_satisfying_every_declared_constraint_passes():
    submission = _submission(
        [("a", 0.2), ("b", 0.2)], cash_weight=0.6,
        max_holdings=2, max_weight_per_holding=0.25, min_cash_weight=0.5,
    )
    assert validate_constraints(submission) == []
```

And in `test_submit.py`, that the declaration is stored:

```python
def test_declared_constraints_are_stored_on_the_portfolio(engine, news_client):
    tool = submit_tool(engine, frozenset(), "m", news_client, Recorder())

    tool.invoke(_call({**VALID, "constraints": {"max_holdings": 5}}))

    with engine.connect() as conn:
        stored = conn.execute(
            sa.text("SELECT acknowledged_constraints FROM portfolios")
        ).scalar_one()
    assert stored == {"max_holdings": 5, "max_weight_per_holding": None, "min_cash_weight": None}


def test_no_declared_constraints_store_null(engine, news_client):
    tool = submit_tool(engine, frozenset(), "m", news_client, Recorder())

    tool.invoke(_call(VALID))

    with engine.connect() as conn:
        assert conn.execute(
            sa.text("SELECT acknowledged_constraints FROM portfolios")
        ).scalar_one() is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest services/portfolio-builder/tests/test_portfolio.py -v`
Expected: FAIL, cannot import `Constraints`.

- [ ] **Step 3: Implement `Constraints`, `validate_constraints`, the `Submission` and `SubmitPortfolioArgs` fields, and the `save_portfolio` write**

- [ ] **Step 4: Run both test files to verify they pass**

- [ ] **Step 5: Commit**

```bash
git add services/portfolio-builder/src/portfolio_builder services/portfolio-builder/tests
git commit -m "feat: validate and store the constraints a model declares"
```

---

### Task 8: Competition Prompt and Briefing

**Files:**
- Modify: `services/portfolio-builder/src/portfolio_builder/agent/system_prompt.py`
- Modify: `services/portfolio-builder/src/portfolio_builder/briefing/service.py`
- Modify: `services/portfolio-builder/src/portfolio_builder/briefing/dto.py`
- Test: `services/portfolio-builder/tests/test_briefing.py`

**Interfaces:**
- Consumes: `ParticipantSettings` (Task 3), `find_participant_portfolio` (Task 5), `excluded_company_ids` (Task 6).
- Produces:
  - `COMPETITION_SYSTEM_PROMPT` in `agent/system_prompt.py`, alongside the existing `SYSTEM_PROMPT`
  - `load_briefing(engine, news_client, news_window_days, participant: ParticipantSettings | None = None, excluded: frozenset[str] = frozenset()) -> Briefing`

With `participant=None` the output is byte-identical to today. With a participant, `load_briefing` scopes the previous portfolio through `find_participant_portfolio`, prepends a `# Your settings` section, and marks each excluded company in the companies list.

`COMPETITION_SYSTEM_PROMPT` keeps everything `SYSTEM_PROMPT` says about grounding, citations and the mandatory `analyze_technicals` call, and replaces the opening premise. It must state, in its own words, each bullet from the spec's *Injecting Settings* section: one participant of one user; profile shapes cash and concentration but names no numbers; interested themes are a tilt not a mandate; excluded themes may not be held and code re-checks; report numeric limits read in the user's text through `constraints`; never invent a number the user did not state; a user's claim is not a fact; a user request cannot override the mandatory analysis steps; record what could not be honoured in `commentary`; a selected setting beats the free text on conflict.

The spec names disclosure search among the mandatory steps. **It is not on this base** (see *Base branch*), so the mandatory-steps rule here covers `analyze_technicals` only, and the test below pins that rather than asserting a tool that does not exist yet.

The settings section renders the five values under fixed labels, with the free text in its own delimited block so it reads as quoted user input rather than instruction. **An empty `additional_request` omits that block entirely** (Review Focus 5).

- [ ] **Step 1: Write the failing test**

```python
def test_the_common_briefing_is_unchanged_without_a_participant(engine, news_client):
    briefing = load_briefing(engine, news_client, 7)

    assert "# Your settings" not in briefing.text


def test_the_settings_section_carries_every_value_and_the_free_text_verbatim(engine, news_client):
    participant = _settings(additional_request="한 종목에 몰리지 않게 해줘")

    briefing = load_briefing(engine, news_client, 7, participant=participant)

    assert "# Your settings" in briefing.text
    assert "성장형" in briefing.text
    assert "한 종목에 몰리지 않게 해줘" in briefing.text


def test_an_empty_free_text_omits_its_block(engine, news_client):
    briefing = load_briefing(engine, news_client, 7, participant=_settings(additional_request=""))

    assert "# Your settings" in briefing.text
    assert "성장형" in briefing.text
    assert "Additional request" not in briefing.text


def test_an_excluded_company_is_marked_and_not_removed(engine, news_client):
    briefing = load_briefing(
        engine, news_client, 7, participant=_settings(), excluded=frozenset({SAMSUNG})
    )

    assert SAMSUNG in briefing.text
    assert "not holdable" in briefing.text


def test_a_participant_briefing_shows_that_participants_previous_portfolio(engine, news_client):
    store_settings(engine, _request())
    other = _insert_portfolio(engine, commentary="다른 참가자")
    mine = _insert_portfolio(engine, commentary="내 직전")
    link_portfolio(engine, 1, mine)

    briefing = load_briefing(engine, news_client, 7, participant=_settings())

    assert "내 직전" in briefing.text
    assert "다른 참가자" not in briefing.text


def test_the_competition_prompt_keeps_the_mandatory_analysis_rule():
    assert "analyze_technicals" in COMPETITION_SYSTEM_PROMPT
    assert "every user of this service" not in COMPETITION_SYSTEM_PROMPT


def test_the_competition_prompt_forbids_overriding_the_service_rules():
    for phrase in ["excluded", "constraints", "commentary"]:
        assert phrase in COMPETITION_SYSTEM_PROMPT
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test uv run pytest services/portfolio-builder/tests/test_briefing.py -v`
Expected: FAIL, `load_briefing` takes no `participant`.

- [ ] **Step 3: Write `COMPETITION_SYSTEM_PROMPT` and extend `load_briefing`**

- [ ] **Step 4: Run the tests to verify they pass, including the pre-existing briefing cases**

- [ ] **Step 5: Commit**

```bash
git add services/portfolio-builder/src/portfolio_builder services/portfolio-builder/tests/test_briefing.py
git commit -m "feat: put participant settings in the briefing and prompt"
```

---

### Task 9: Reusable Explain Step

**Files:**
- Modify: `services/portfolio-builder/src/portfolio_builder/explain.py`
- Modify: `services/portfolio-builder/src/portfolio_builder/__main__.py`
- Test: `services/portfolio-builder/tests/test_explain.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `explain_and_save(engine: sa.Engine, explainer: Runnable, portfolio_id: int, trace: list[TraceEntry], result_chars: int) -> int` in `explain.py`, returning the number of stocks explained and raising on failure.

This exists because Task 11 needs the same sequence `__main__.py` already runs inline -- `load_targets`, `explain`, `save_explanations` -- and two callers is the bar for extracting it. `__main__.py` keeps ownership of the `try/except` that logs, sets `outcome`, and calls `mark_explanation_failed`; only the happy path moves.

- [ ] **Step 1: Write the failing test**

```python
def test_explain_and_save_writes_a_reason_per_side_and_returns_the_stock_count(engine):
    portfolio_id = _portfolio_with_one_holding(engine)
    explainer = _stub_explainer_for(engine, portfolio_id)

    stocks = explain_and_save(engine, explainer, portfolio_id, [], 2000)

    with engine.connect() as conn:
        sides = conn.execute(
            sa.text("SELECT side FROM portfolio_reasons WHERE portfolio_id = :id ORDER BY side"),
            {"id": portfolio_id},
        ).scalars().all()
    assert stocks == 1
    assert sides == ["buy", "sell"]


def test_explain_and_save_propagates_a_failure(engine):
    portfolio_id = _portfolio_with_one_holding(engine)
    failing = Mock(invoke=Mock(side_effect=RuntimeError("boom")))

    with pytest.raises(RuntimeError):
        explain_and_save(engine, failing, portfolio_id, [], 2000)
```

- [ ] **Step 2: Run the tests to verify they fail**

Expected: FAIL, cannot import `explain_and_save`.

- [ ] **Step 3: Add `explain_and_save` to `explain.py` and call it from `__main__.py` in place of the inline three-call sequence**

- [ ] **Step 4: Run the suite to confirm `test_main.py` still passes unchanged**

Run: `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test uv run pytest services/portfolio-builder -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/portfolio-builder/src/portfolio_builder services/portfolio-builder/tests/test_explain.py
git commit -m "refactor: extract the explain-and-save step for reuse"
```

---

### Task 10: Backend Notification

**Files:**
- Create: `services/portfolio-builder/src/portfolio_builder/competition/backend.py`
- Test: `services/portfolio-builder/tests/test_competition_backend.py`

**Interfaces:**
- Consumes: `ParticipantSettings` (Task 3), `ktb_core.backend_auth.BackendAuth`.
- Produces: `class Backend` wrapping `BackendAuth` with `completed(participant: ParticipantSettings, portfolio_id: int) -> None` and `failed(participant: ParticipantSettings, error: str) -> None`.

Both POST to `f"/api/v1/competitions/{participant.competition_id}/portfolio"` with `str(participant.user_id)` as the JWT subject, mirroring how `report_builder/worker.py` calls the Backend. Body carries `participant_id`, `status` (`"completed"` or `"failed"`), and `portfolio_id` or `error`. **Every name here is provisional** (spec, *Notifying the Backend*): this module is the only place the path and body appear, so agreement with the Backend changes one file.

- [ ] **Step 1: Write the failing test**

```python
def test_completion_posts_the_portfolio_under_the_users_subject():
    auth = Mock()
    backend = Backend(auth)

    backend.completed(_settings(), 42)

    method, path, subject = auth.request.call_args.args
    assert (method, path, subject) == ("POST", "/api/v1/competitions/10/portfolio", "100")
    assert auth.request.call_args.kwargs["json"] == {
        "participant_id": 1, "status": "completed", "portfolio_id": 42
    }


def test_failure_posts_the_error():
    auth = Mock()

    Backend(auth).failed(_settings(), "boom")

    assert auth.request.call_args.kwargs["json"] == {
        "participant_id": 1, "status": "failed", "error": "boom"
    }


def test_a_transport_error_propagates():
    auth = Mock(request=Mock(side_effect=httpx.ConnectError("down")))

    with pytest.raises(httpx.ConnectError):
        Backend(auth).completed(_settings(), 42)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest services/portfolio-builder/tests/test_competition_backend.py -v`
Expected: FAIL, cannot import `Backend`.

- [ ] **Step 3: Implement `Backend` in `competition/backend.py`**

- [ ] **Step 4: Run the tests to verify they pass**

- [ ] **Step 5: Commit**

```bash
git add services/portfolio-builder/src/portfolio_builder/competition/backend.py services/portfolio-builder/tests/test_competition_backend.py
git commit -m "feat: notify the backend when a competition portfolio is ready"
```

---

### Task 11: The Worker

**Files:**
- Create: `services/portfolio-builder/src/portfolio_builder/competition/worker.py`
- Create: `services/portfolio-builder/src/portfolio_builder/competition/__main__.py`
- Test: `services/portfolio-builder/tests/test_competition_worker.py`

**Interfaces:**
- Consumes: everything from Tasks 2-10.
- Produces: `process_message(message: dict[str, object], sqs, engine, backend, model_factory, settings, log) -> None` in `worker.py`; `main()` in `__main__.py`.

`__main__.py` is the `report_builder/__main__.py` loop with this plan's settings: build the SQS client, resolve a queue name to a URL, create the engine, and poll with `WaitTimeSeconds=20` and the configured visibility timeout, handing each message to `process_message`. Keep no logic in `__main__.py` beyond wiring (AGENTS.md).

`process_message` order, from the spec's *Registering Work* and *Re-explain Path*:

1. Validate the body as `Request`. On `ValidationError`, log and **delete** the message -- a bad message never becomes good.
2. `store_settings`; log a warning when it reports a difference.
3. `latest_participant_portfolio`. If a row exists, is not `ready`, and has a trace: re-explain it via `explain_and_save`, notify, delete the message, return. Do not generate.
4. `generation_key` from the settings, that row's id (or `None`) and the configured model; then `claim_job`.
5. `None` claim: return **without deleting** -- another worker holds it, or attempts are spent.
6. `mine=False`: `link_portfolio` to the existing `portfolio_id`, notify, delete, return.
7. `mine=True`: `excluded_company_ids`, `load_briefing` with the participant and excluded set, `run_agent` with `COMPETITION_SYSTEM_PROMPT` and the same tool list `__main__.py` builds on this base -- `news_tools`, `graph_tools`, `technicals_tool`, `submit_tool` -- with `excluded` passed to `submit_tool`. No disclosure tool exists on this base (see *Base branch*). On no portfolio: `fail_job`, notify failure, delete.
8. On a portfolio: `save_trace`, `link_portfolio`, `finish_job`, then `explain_and_save`. An explain failure calls `mark_explanation_failed` and leaves the job `succeeded`.
9. Notify completion, delete the message. A notification failure leaves the message for redelivery, which step 3 or 6 then handles without regenerating.

Also log `excluded_theme_codes` alongside the resolved company count, so an excluded code that no longer resolves to any company (Review Focus 3) is visible.

- [ ] **Step 1: Write the failing test**

Stub the model the way `test_agent.py` already stubs one, and monkeypatch `run_agent` for the paths that do not exercise the agent.

```python
def test_a_successful_generation_links_notifies_and_deletes(engine, monkeypatch, ...):
    ...
    assert sqs.delete_message.called
    assert backend.completed.call_args.args[1] == portfolio_id
    assert latest_participant_portfolio(engine, 1)[0] == portfolio_id


def test_an_invalid_message_is_deleted_without_generating(engine, ...):
    process_message({"Body": json.dumps({**MESSAGE, "risk_profile": "성장 추구형"}), ...})

    assert sqs.delete_message.called
    assert not run_agent_stub.called


def test_a_second_participant_with_the_same_settings_shares_without_generating(engine, ...):
    # first message generates
    # second, participant_id=2, same settings
    assert run_agent_stub.call_count == 1
    assert latest_participant_portfolio(engine, 2)[0] == latest_participant_portfolio(engine, 1)[0]


def test_a_running_job_leaves_the_message_in_place(engine, ...):
    assert not sqs.delete_message.called
    assert not run_agent_stub.called


def test_an_unexplained_portfolio_is_re_explained_instead_of_regenerated(engine, ...):
    # link an explanation_failed portfolio with a trace to participant 1
    assert not run_agent_stub.called
    assert explain_stub.called
    assert sqs.delete_message.called


def test_round_two_settings_drift_is_logged_and_the_snapshot_wins(engine, ...):
    assert ("settings_mismatch", logging.WARNING) in [(e, lvl) for e, lvl, _ in log.events]


def test_a_failed_generation_fails_the_job_notifies_and_deletes(engine, ...):
    assert backend.failed.called
    assert sqs.delete_message.called


def test_an_explain_failure_keeps_the_job_succeeded_and_marks_the_portfolio(engine, ...):
    with engine.connect() as conn:
        status = conn.execute(sa.text("SELECT status FROM portfolios")).scalar_one()
        job = conn.execute(sa.text("SELECT status FROM competition_jobs")).scalar_one()
    assert (status, job) == ("explanation_failed", "succeeded")


def test_a_notification_failure_leaves_the_message_for_redelivery(engine, ...):
    assert not sqs.delete_message.called
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test uv run pytest services/portfolio-builder/tests/test_competition_worker.py -v`
Expected: FAIL, cannot import `process_message`.

- [ ] **Step 3: Implement `process_message` in `worker.py`**

- [ ] **Step 4: Implement `main()` in `competition/__main__.py`**

- [ ] **Step 5: Run the tests to verify they pass**

- [ ] **Step 6: Commit**

```bash
git add services/portfolio-builder/src/portfolio_builder/competition services/portfolio-builder/tests/test_competition_worker.py
git commit -m "feat: add the competition portfolio worker"
```

---

### Task 12: Configuration and Deployment

**Files:**
- Modify: `services/portfolio-builder/src/portfolio_builder/settings.py`
- Modify: `services/portfolio-builder/pyproject.toml`
- Modify: `docker/portfolio-builder.Dockerfile`, `docker/app.Dockerfile`
- Modify: `docker/requirements/portfolio-builder.txt`, `docker/requirements/app.txt`
- Modify: `compose.local.yaml`, `compose.prod.yaml`, `.env.example`, `AGENTS.md`
- Test: `services/portfolio-builder/tests/test_settings.py`

**Interfaces:**
- Consumes: nothing.
- Produces: the settings fields listed in the spec's *Settings* section, and the `competition-portfolio-builder` console script.

The competition-only values must not become required on the existing cron run (spec, *Settings*). Give them empty-string / `None` defaults on the one `Settings` class and have `competition/__main__.py` fail at startup if any is unset -- the alternative, a second settings class, would break AGENTS.md's one-`settings.py` rule less cleanly than a startup check.

`portfolio-builder` gains `boto3` and `httpx`, which `report-builder` already depends on at `boto3>=1.35` and `httpx>=0.28`; use the same floors.

- [ ] **Step 1: Write the failing test**

```python
def test_competition_settings_default_to_unset(monkeypatch):
    _set_required(monkeypatch)

    settings = Settings()

    assert settings.competition_queue_url == ""
    assert settings.competition_visibility_timeout == 900
    assert settings.competition_stale_running_seconds == 3600
    assert settings.competition_max_attempts == 3
    assert settings.backend_timeout == 10


def test_the_existing_cron_settings_still_load_without_any_competition_variable(monkeypatch):
    _set_required(monkeypatch)

    assert Settings().llm_model


def test_competition_variables_are_read_from_the_environment(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("PORTFOLIO_BUILDER_COMPETITION_QUEUE_URL", "q")
    monkeypatch.setenv("PORTFOLIO_BUILDER_COMPETITION_MAX_ATTEMPTS", "5")

    settings = Settings()

    assert (settings.competition_queue_url, settings.competition_max_attempts) == ("q", 5)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest services/portfolio-builder/tests/test_settings.py -v`
Expected: FAIL on the unknown attribute.

- [ ] **Step 3: Add the fields to `Settings` and the startup check to `competition/__main__.py`**

- [ ] **Step 4: Add the console script, the two dependencies, and regenerate the requirements**

```bash
uv lock
uv export --package portfolio-builder --no-dev --no-emit-workspace --format requirements-txt -o docker/requirements/portfolio-builder.txt
uv export --all-packages --no-dev --group migrations --no-emit-workspace --format requirements-txt -o docker/requirements/app.txt
```

- [ ] **Step 5: Add the compose services and document the variables**

A `competition-portfolio-builder` service in both compose files: the same image as `portfolio-builder` with `command: competition-portfolio-builder`, long-running rather than under the `jobs` profile. Add every new variable to `.env.example` with a comment and to the AGENTS.md environment table, and add the member's new entry to the AGENTS.md architecture table noting it is a long-running SQS worker.

- [ ] **Step 6: Run lint, format, and the full suite**

```bash
uv run ruff check .
uv run ruff format --check .
KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/ktb_test uv run pytest
```
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add .
git commit -m "feat: configure and deploy the competition portfolio worker"
```

---

## Not In This Plan

Stage 2, trading, gets its own plan: `Account.participant_id` in the Backend snapshot, per-participant loading in `load_portfolio`, and participant-scoped `LEFTOVERS`. The spec's *Two Pull Requests* section holds the condition for shipping stage 1 alone -- participant accounts must not arrive `is_active=true` while only stage 1 is deployed -- and that is an agreement with the Backend, not a task here.
