# Portfolio Rebalancer Design

Replaces `2026-09-29-portfolio-rebalancer-design.md` and its implementation, which are deleted.
That design laddered limit orders over three trading days and mirrored accounts into its own
tables. This one sends market orders, keeps no state of its own, and attaches to every order the
explanation the Backend shows the user.

Two members change:

1. **portfolio-builder** keeps the agent's reasoning trace and, with one more LLM call per
   portfolio, turns it into a buy and a sell explanation per stock.
2. **portfolio-rebalancer** reads the latest explained portfolio, polls the Backend for accounts,
   and sends one market order per stock that needs to move.

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
with exit code 1. The portfolio row then exists without reasons, and the rebalancer skips it.

On success the rows are inserted into `portfolio_reasons` in one transaction.

### Unchanged

`portfolio_holdings.reason`, `portfolio_exits.reason` and `cited_cluster_ids` stay the agent's own
record and keep feeding the next run's briefing. The system prompt does not change.

### Settings

| variable | default |
|---|---|
| `PORTFOLIO_BUILDER_EXPLAIN_RESULT_CHARS` | `2000` |

## portfolio-rebalancer

A one-shot `main()` with no inbound surface. Compose or cron runs it hourly during market hours.
It keeps no tables: a pending order stops a duplicate, and a filled market order shows up as a
holding on the next run.

### One run

1. **Portfolio.** Load the latest portfolio that has `portfolio_reasons` rows, with its holdings,
   exits, reasons, and stock codes from `corporations`. None: log and exit 0.
2. **Snapshot.** `GET {BACKEND_URL}/api/v1/users/ai-server` with the service token.
3. **Prices.** The last regular-session daily close from QuestDB `bars_1d` for every stock in the
   portfolio or held by any account, in one query.
4. **Decide** each account with `rebalance()`.
5. **Send** each order.

### Snapshot

Parsed into Pydantic models matching the Backend's `AiUserSnapshotResponse`:

```python
class Stock(BaseModel):
    stock_code: str
    quantity: int
    total_cost: Decimal

class PendingOrder(BaseModel):
    order_id: int
    stock_code: str
    order_side: Literal["buy", "sell"]
    order_type: Literal["limit", "market"]
    order_status: str
    limit_price: int | None
    quantity: int
    current_stock_price: Decimal

class Account(BaseModel):
    account_id: int
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

### Decision

A pure function, no I/O:

```python
def rebalance(
    portfolio: Portfolio, account: Account, closes: dict[str, Decimal], band: float
) -> list[Order]: ...
```

- An inactive account returns nothing.
- A stock with any pending order in the account is skipped, both sides.
- **Value** = `cash_balance` − Σ pending buys (`quantity × (limit_price or current_stock_price)`)
  \+ Σ held `quantity × close`.
- **Exits:** sell every held share.
- **Holdings:** target = ⌊value × weight ÷ close⌋.
  - Nothing held and target > 0: buy target.
  - Otherwise trade to target only when |held weight − target weight| > `band`, where held weight
    is `quantity × close ÷ value`.
- A held stock that is neither a holding nor an exit (bought by hand) is left alone.
- A stock without a close is skipped and logged.
- Sells come first. Buys spend only cash that exists now, never a sell's proceeds, and go in
  descending portfolio weight; a buy that does not fit is cut to the shares that do, and dropped
  at zero.

Each `Order` carries the side's `reason` and `reasonings`.

### Sending

```python
class OrderRequest(BaseModel):
    stock_code: str
    order_side: Literal["buy", "sell"]
    order_type: Literal["market"] = "market"
    quantity: int
    reason: str
    thoughts: list[Reasoning]
```

`POST {BACKEND_URL}/api/v1/accounts/{account_id}/orders`, one order per request.

**Auth.** Every request carries an HS256 JWT, signed with PyJWT over
`PORTFOLIO_REBALANCER_BACKEND_JWT_SECRET`, in the `access_token` cookie. Claims: `iss` = the
Backend's issuer, `type=access`, `actor=AI`, a five-minute `exp`, and

- `sub=ai-server` for the snapshot GET;
- `sub=<user_id>` for an order, which `OrderController` reads as the account owner.

**CSRF.** Before the first order of a run, `GET /api/v1/auth/csrf` returns
`{token, header_name}` and sets the `XSRF-TOKEN` cookie. Every POST sends
`Cookie: access_token=…; XSRF-TOKEN=<cookie>` and `X-XSRF-TOKEN: <token>`. The cookie header is
built by hand: the Backend marks the cookie `Secure` in production, and an HTTP client's cookie jar
would not send it back over plain `http://` inside the cluster. A 403 `INVALID_CSRF_TOKEN` fetches
a new token and retries that order once.

A failed order is logged and the run continues; the run exits 1 if any order failed. The next run
retries it, because nothing was recorded.

### Settings

| variable | default |
|---|---|
| `PORTFOLIO_REBALANCER_POSTGRES_DSN` | required |
| `PORTFOLIO_REBALANCER_QUESTDB_CONF` | required |
| `PORTFOLIO_REBALANCER_BACKEND_URL` | required |
| `PORTFOLIO_REBALANCER_BACKEND_JWT_SECRET` | required |
| `PORTFOLIO_REBALANCER_BACKEND_JWT_ISSUER` | required |
| `PORTFOLIO_REBALANCER_BAND` | `0.05` |
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
  shortfall cuts the lowest-weight buy. The snapshot model parses the Backend doc's example JSON.
  The client sends the cookies, the CSRF header and the right `sub`, and retries once on
  `INVALID_CSRF_TOKEN`, against `httpx.MockTransport`. The repository loads the latest explained
  portfolio against `ktb_test`.

## Docs

`AGENTS.md` and `README.md` drop the old rebalancer's tables, the `portfolio-rebalancer-http`
node and `BACKEND_JWT_SUBJECT`, add the settings above, and point the architecture paragraph at
this file. The rebalancer's `docker/requirements` file is regenerated once PyJWT and httpx are
declared.

## Out of Scope

- Rerunning the explain call alone for a portfolio whose explanation failed.
- A trading-day calendar: the schedule only runs during market hours.
- Fees, taxes and slippage.
- Limit orders. Orders are market orders on both sides, assuming the Backend accepts market
  sells.
