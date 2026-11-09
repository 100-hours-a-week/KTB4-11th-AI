# Portfolio Rebalancer Design

Replaces `2026-09-29-portfolio-rebalancer-design.md` and its implementation, which are deleted.
That design laddered limit orders over three trading days and mirrored accounts into its own
tables. This service publishes order requests, keeps no state of its own, and attaches to every order the
explanation the Backend shows the user.

Two members change:

1. **portfolio-builder** keeps the agent's reasoning trace and, with one more LLM call per
   portfolio, turns it into a buy and a sell explanation per stock.
2. **portfolio-rebalancer** fetches the Backend account snapshot, selects the latest explained
   common or participant portfolio per account, and publishes cancellation and order requests to Order FIFO.

portfolio-builder is built first: the rebalancer has nothing to send until explanations exist.

## Data Contract

An explanation is what the Backend calls `reason` and `thoughts`:

| ours | Backend order field | shape |
|---|---|---|
| `reason` | `reason` | one line summarising the reasonings |
| `reasonings[]` | `thoughts[]` | ordered `{label, body}` |

All text is Korean in 토스 말투: sentences end in "~해요" / "~했어요". Example of the register:

```json
{
  "reason": "가장 강한 뉴스부터 찾고, 직접 수혜를 받는 종목에 집중해요.",
  "reasonings": [
    {"label": "반도체가 가장 강해요", "body": "수출, 실적, HBM 수요, 용인 산단까지 여러 호재가 겹쳐서 반도체를 핵심 테마로 봐요."},
    {"label": "현금도 남겨둬요", "body": "모든 뉴스가 긍정적인 건 아니어서 새로운 기회나 위험에 대응할 수 있도록 현금을 일부 남겨둬요."}
  ]
}
```

Every **holding** gets two explanations, `buy` and `sell`, because the same stock is bought by an
account that holds too little and sold by one that holds too much. Every **exit** gets `sell`
only. The rebalancer picks the side that matches the order.

## Migration `0007`

Revision `0007`, `down_revision = "0006"`.

- `portfolios.trace JSONB NULL`: the agent run's trace, written after the run.
- `portfolio_reasons`:

| column | type |
|---|---|
| `portfolio_id` | `BIGINT` FK `portfolios.id` `ON DELETE CASCADE` |
| `company_id` | `TEXT` FK `corporations.corp_code` |
| `side` | `TEXT`, `CHECK (side IN ('buy', 'sell'))` |
| `reason` | `TEXT NOT NULL` |
| `reasonings` | `JSONB NOT NULL`, a list of `{label, body}` |

Primary key `(portfolio_id, company_id, side)`.

A dev database that applied the deleted `0007`/`0008` has their tables and `alembic_version =
'0008'`. Drop `users`, `accounts`, `account_holdings`, `account_pending_orders`,
`rebalance_orders`, then `uv run alembic stamp 0006` before upgrading.

## portfolio-builder

### Trace

`RunLog` already sees every model turn (reasoning, text, tool calls) and every tool result. It
also appends each to `trace: list[TraceEntry]`:

```python
class TraceEntry(BaseModel):
    turn: int
    kind: Literal["model", "tool"]
    reasoning: str | None = None
    text: str | None = None
    name: str | None = None
    args: dict[str, Any] | None = None
    result: str | None = None
```

`RunResult` carries the trace out of `run_agent`. When the outcome is `saved`, `main()` writes it
with one `UPDATE portfolios SET trace = …` (`model_dump(mode="json")`). The submit call is part of
the trace, which is why it is not written inside `submit_portfolio`.

### Explain call

New `explain.py`, called from `main()` after the trace is saved. One
`model.with_structured_output(Explanations)` call on the same OpenRouter model:

```python
class Reasoning(BaseModel):
    label: str = Field(min_length=1, max_length=255)
    body: str = Field(min_length=1, max_length=16000)

class SideExplanation(BaseModel):
    reason: str = Field(min_length=1, max_length=200)
    reasonings: list[Reasoning] = Field(min_length=1)

class StockExplanation(BaseModel):
    company_id: str
    buy: SideExplanation | None
    sell: SideExplanation

class Explanations(BaseModel):
    stocks: list[StockExplanation]
```

The Backend's limits set the `label`/`body` maximums.

**Input.** The trace and the saved portfolio: every holding with its name, weight and the weight it
had in the previous portfolio, and every exit. Reasoning, text and tool arguments go in whole.
Each tool result is cut to `PORTFOLIO_BUILDER_EXPLAIN_RESULT_CHARS` characters (default `2000`) so
a 150-turn run fits the context window.

**Prompt.** Write in 토스 말투; keep a label short, like a heading; ground every sentence in what the
trace shows; the `buy` side says why to own more of the stock, the `sell` side why to own less of
it at this weight; an exit's `sell` says why it leaves.

**Coverage.** A `model_validator` checks the result against the portfolio, passed in through
`Explanations.model_validate(raw, context={"holdings": …, "exits": …})`: every holding has `buy`
and `sell`, every exit has `sell` and no `buy`, and no other company appears. All violations are
reported together. On failure the errors go back to the model once; a second failure ends the run
with exit code 1.

### Order readiness

`portfolios.status` (migration `0008`) says whether a portfolio can be traded:

| status | set when |
|---|---|
| `explanation_pending` | the agent saves the portfolio (column default) |
| `ready` | the explanations are saved, in the same transaction as the `portfolio_reasons` rows |
| `explanation_failed` | the explain step raises (trace save, provider error, or a second rejection) |

The migration marks existing portfolios `ready` when they have reasons and `explanation_failed`
otherwise.

A run first looks at the latest portfolio. When it is not `ready` and has a trace, the run does not
call the agent: it explains that portfolio again from the stored trace and exits 0 on success, or
marks it `explanation_failed`, logs `explain_failed` at ERROR and exits 1. A retrying scheduler
therefore never writes a second portfolio. Without a trace (a crash before it was saved) the run
builds a new portfolio. An explanation that can never succeed keeps every run failing and trading
halted; deleting that portfolio row (it cascades) unblocks it.

On success the rows are inserted into `portfolio_reasons` and the status set to `ready` in one
transaction.

### Unchanged

`portfolio_holdings.reason`, `portfolio_exits.reason` and `cited_cluster_ids` stay the agent's own
record and keep feeding the next run's briefing. The system prompt does not change.

### Settings

| variable | default |
|---|---|
| `PORTFOLIO_BUILDER_EXPLAIN_RESULT_CHARS` | `2000` |

## portfolio-rebalancer

A one-shot `main()` with no inbound surface. Compose or cron runs it hourly during market hours.
It keeps no tables: a pending order stops a duplicate, and a filled order shows up as a
holding on the next run.

### One run

One invocation automatically handles common and participant accounts; there is no competition
mode flag. Respect the KRX session unless test mode is configured. Fetch one authenticated
Backend snapshot, validate identities and select every account's portfolio before publishing
any cancellation. Missing/unready common portfolios skip only common accounts; healthy
participants continue. Collect quotes for all selected targets and held stocks, run the existing
allocation calculation, then publish cancellation and replacement requests per account.

Only a latest `ready` portfolio may trade. Missing, pending or foreign portfolios skip the
account without cancelling orders or falling back to common/older history. An
`explanation_failed` portfolio skips the account, increments the explanation-failure count,
and makes the run exit 2 after healthy accounts finish. Cancellation or placement publication
failures take precedence and exit 1. Invalid stored portfolio data increments `portfolio_failed`;
a common read failure skips common accounts while participant selection continues, and a
participant read failure skips only that account. Log the exception type without stored content.
Missing or malformed participant identity on active accounts increments `identity_failed`.
Inactive accounts are skipped as `inactive` before participant ID validation, so their missing
or malformed participant IDs do not increase this counter. Duplicate account ID checks still
precede inactivity filtering; duplicate participant checks apply to active mappings.
These data failures exit 2 after healthy accounts finish. Database connection failures propagate
before any cancellation; HTTP snapshot failures exit before any publication.

### Snapshot

Parsed into Pydantic models matching the Backend's `AiUserSnapshotResponse`:

```python
class Stock(BaseModel):
    stock_code: str
    quantity: int
    total_cost: float

class PendingOrder(BaseModel):
    order_id: int
    stock_code: str
    order_side: Literal["buy", "sell"]
    order_type: Literal["limit", "market"]
    order_status: str
    limit_price: int | None
    quantity: int
    current_stock_price: float

class Account(BaseModel):
    account_id: int
    participant_id: int | None
    is_active: bool
    cash_balance: int
    stocks: list[Stock]
    pending_orders: list[PendingOrder]

class User(BaseModel):
    user_id: int
    accounts: list[Account]
```

Fields not listed (`account_name`) are ignored. A 503 (`MARKET_DATA_UNAVAILABLE`) or any other
error ends the run with exit code 1; the next run retries.

### Participant selection and ownership

Every account must carry an explicit `participant_id`: null selects common history and a strict
positive integer selects that participant. Missing identity is skipped before cancellation or
placement. For active accounts, invalid participant IDs mark only that account for exclusion
and `identity_failed`;
they are never treated as common accounts. Other invalid account financial fields still reject the
snapshot before publication. Never infer a participant from an account ID,
user ID, agent ID or investment profile. Match the settings user_id to the snapshot user_id
when selecting participant portfolios and exit history; a mismatched owner is excluded. Order and cancellation requests retain the current snapshot's `user_id`.

Exclude every duplicate account ID across the full snapshot, including identical duplicates and
conflicts between common and participant accounts. If a participant maps to multiple active
accounts, exclude every affected mapping. Unrelated valid accounts continue. Load each valid
participant once, selecting only its latest link in `competition_portfolios`. Historical exit
reasons use the same participant history, including shared portfolios. Common latest/history
queries exclude all competition-linked portfolios. A database without competition tables uses
independent legacy common SQL. Participant accounts require both competition_participants and
competition_portfolios; absent tables raise MissingParticipantSchema, increment portfolio_failed,
and make the run exit 2 after healthy accounts finish. This change adds no migration.

Backend explicit nullable participant identity is a deployment prerequisite; participant trading
also requires the competition schema and generation results. Migration `0017` requires the
settings user_id; agent_id stays optional and unused. Selection,
cancellation and placement logs include actual user, account, participant and chosen portfolio
IDs. The run summary keeps the common portfolio ID nullable and never inherits the last
participant's ID. No new identity fields enter the existing order payload.

### Decision

A pure function, no I/O:

```python
def rebalance(
    portfolio: Portfolio,
    account: Account,
    closes: dict[str, float],
    band: float,
    buy_buffer: float,
) -> list[Order]: ...
```

- An inactive account returns nothing.
- A stock with any pending order in the account is skipped, both sides.
- **Capital** = `cash_balance` − Σ pending buys (`quantity × (limit_price or current_stock_price)`)
  \+ Σ held `quantity × close`, minus the value of locked holdings (below).
- **Exits:** sell every held share.
- A held stock that is neither a holding nor an exit (bought by hand) is left alone.
- A holding that is held but has no close freezes every holding in the account; exits still sell.
- Sells come first. Buys spend only cash that exists now, never a sell's proceeds, and go in
  descending budget; a buy that does not fit is cut to the shares that do, and dropped at zero.

#### Allocation

Restored from `2026-09-29-portfolio-rebalancer-design.md`. Every price below is the ask,
`close × (1 + buy_buffer)`.

1. **Cash first.** `investable = capital × (1 − cash_weight − unpriced)`. `cash_weight` comes from
   `portfolios`. `unpriced` is the weight of holdings that have no close and are not held: it stays
   cash rather than being shared out, because a missing close is usually a gap in the data, and
   sharing its weight out would overbuy the rest until the close returns.
2. **Drop what cannot be bought.** `budget_i = round(investable × weight_i ÷ Σ weights)`, in whole
   won. A holding whose budget is below one share is dropped, and its weight is **shared equally**
   over those that remain, not in proportion, so one expensive name is not absorbed by the largest
   holding. Repeat until every remaining holding can afford a share. An equal share raises every
   remaining budget, so the set only shrinks.
3. **Lock dropped holdings that are held.** Their value cannot be spent while they are not sold,
   so it leaves the capital and steps 1–2 run again. A locked holding gets no order, neither buy
   nor sell. The locked set only grows, so this ends.
4. **Whole shares.** `target_i = ⌊budget_i ÷ ask_i⌋`. The cash flooring leaves is spent one share
   at a time: first for whichever holding is furthest below its budget, and once none that is
   short can afford a share, round-robin over the holdings in descending budget. Whatever cannot
   buy a share stays cash.
5. **Band.** A held holding trades only when `|quantity × close − budget| ÷ capital > band`. It
   then buys up to `target`, or sells down to `max(target, ⌊budget ÷ close⌋)`, so a trim is not
   deepened by the buy buffer.

Each `Order` carries the side's `reason` and `reasonings`.

### Sending

Use the existing SQS Order FIFO contract from
[the messaging design](2026-10-08-sqs-messaging-design.md). Publish cancellation requests first;
a successful send means cancellation was requested, not completed. A failed/partial cancellation
send prevents every replacement for that account. After all cancellation sends succeed, calculate
and publish replacements immediately in the same account FIFO group using the original snapshot,
without waiting for a refreshed snapshot. The Backend consumer must finish each cancellation
before the next command in that group. Do not budget unconfirmed sell proceeds for buys.

Preserve the existing pricing rules and cancellation/order payloads; leftover sell reasons come
from the same selected participant history. Preserve snapshot
`user_id` for cancellation and placement. Account ID is the FIFO group and event ID is the
deduplication ID. Log MessageId, eventId, correlationId and publication result. Snapshot HTTP
continues to use the existing Backend JWT authentication. AWS uses its default credential chain.

Actual publication failures exit 1 and retain the existing automatic retry policy. Explanation,
portfolio-data and identity-only failures exit 2: the systemd unit sets `RestartPreventExitStatus=2`, leaving exit 2 failed and
eligible for `OnFailure` without restarting the whole run. Deploy the updated image together
with the rendered/installed `ktb-portfolio-rebalancer.service`, then run `systemctl daemon-reload`.
An image-only rollout leaves the old restart policy active.

### Settings

| variable | default |
|---|---|
| `PORTFOLIO_REBALANCER_POSTGRES_DSN` | required |
| `PORTFOLIO_REBALANCER_QUESTDB_CONF` | required |
| `PORTFOLIO_REBALANCER_BACKEND_URL` | required |
| `PORTFOLIO_REBALANCER_BACKEND_JWT_SECRET` | required |
| `PORTFOLIO_REBALANCER_BACKEND_JWT_ISSUER` | required |
| `PORTFOLIO_REBALANCER_ORDER_QUEUE_URL` | required |
| `PORTFOLIO_REBALANCER_TEST_MODE` | `false` |
| `PORTFOLIO_REBALANCER_BAND` | `0.05` |
| `PORTFOLIO_REBALANCER_BUY_BUFFER` | `0.02` |
| `PORTFOLIO_REBALANCER_LOG_LEVEL` | `INFO` |

`BACKEND_JWT_SUBJECT` is gone: the subject is `ai-server` or a user id, never configured.

### Security

The JWT secret signs a token for any user, so whoever holds it can trade in every AI-managed
account. The Backend's design requires it: it shares one HS256 secret rather than issuing
per-service credentials. Keep the secret in the production secret store and out of shared dev
`.env` files. The safer follow-up, a Backend change outside this spec, is for the Backend to accept
orders from the service token with the account in the path.

## Testing

- **portfolio-builder:** `RunLog` builds the trace from fake model and tool messages; the
  `Explanations` validator reports a missing `buy`, a stray `buy` on an exit, and an unknown
  company together; the explain call retries once on coverage errors, with a fake chat model;
  the reasons and trace are saved against `ktb_test`.
- **portfolio-rebalancer:** table-driven `rebalance()` tests: a share costing more than the
  budget (SK하이닉스 at 1,800,000 with a 500,000 budget) buys nothing; drift inside the band does
  not trade and outside it does; an exit sells everything; a pending order skips its stock; a cash
  shortfall cuts the lowest-weight buy; `cash_weight` is held back; a dropped weight is shared
  equally, and the band uses the shared-out budget; the leftover is spent share by share, past the
  budget once the short holding is too dear; a held holding that cannot be bought is kept and its
  value locked out of the capital. The snapshot model parses the Backend doc's example JSON.
  Verify one authenticated snapshot, cancellation-before-placement FIFO publication, failure
  isolation, and unchanged payloads. Against `ktb_test`, verify explicit nullable identity,
  duplicate exclusion, common/participant SQL isolation, shared exit history, latest-unready
  behavior, quote aggregation, snapshot user identity and exit 2 versus send-failure exit 1.
  Participant SQL tests also run against a separately migrated database with competition tables.

## Docs

`AGENTS.md` and `README.md` drop the old rebalancer's tables, the `portfolio-rebalancer-http`
node and `BACKEND_JWT_SUBJECT`, add the settings above, and point the architecture paragraph at
this file. The rebalancer's `docker/requirements` file is regenerated once PyJWT and httpx are
declared.

## Out of Scope

- Fees, taxes and slippage.
