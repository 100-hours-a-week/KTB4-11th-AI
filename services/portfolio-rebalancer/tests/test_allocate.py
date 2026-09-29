from portfolio_rebalancer.allocate import Target, allocate, spend_leftover

CAPITAL = 10_000_000

WEIGHTS = (0.20, 0.15, 0.12, 0.10, 0.10, 0.08, 0.08, 0.07, 0.05, 0.05)


def targets(weights=WEIGHTS) -> list[Target]:
    return [
        Target(company_id=f"C{i:02d}", stock_code=f"{i:06d}", weight=w)
        for i, w in enumerate(weights)
    ]


def flat(price: float, count: int = 10) -> dict[str, float]:
    return {f"{i:06d}": price for i in range(count)}


def invested(positions) -> float:
    return sum(p.shares * p.price for p in positions)


def test_every_affordable_company_is_allocated_and_none_dropped():
    positions, cash = allocate(targets(), flat(10_000), CAPITAL, cash_weight=0.0)

    assert len(positions) == 10
    assert all(p.shares > 0 for p in positions)
    assert invested(positions) + cash == CAPITAL


def test_an_unaffordable_company_is_dropped():
    prices = flat(10_000) | {"000002": 9_000_000}

    positions, _ = allocate(targets(), prices, CAPITAL, cash_weight=0.0)

    assert {p.stock_code for p in positions} == {t.stock_code for t in targets()} - {"000002"}


def test_a_dropped_weight_is_shared_equally_not_proportionally():
    """The 0.12 of the dropped company must land as 0.12/9 on each survivor. Shared in
    proportion it would have gone mostly to the 0.20, which is the whole distinction."""
    prices = flat(10_000) | {"000002": 9_000_000}

    positions, _ = allocate(targets(), prices, CAPITAL, cash_weight=0.0)

    by_code = {p.stock_code: p.weight for p in positions}
    equal_share = 0.12 / 9
    assert by_code["000000"] == 0.20 + equal_share
    assert by_code["000009"] == 0.05 + equal_share
    assert by_code["000000"] - 0.20 == by_code["000009"] - 0.05


def test_dropping_a_company_never_makes_another_unaffordable():
    """A survivor's budget only rises, so one pass cannot cascade into another drop."""
    prices = flat(1_200_000) | {"000000": 100_000}

    positions, _ = allocate(targets(), prices, CAPITAL, cash_weight=0.0)

    assert all(p.shares >= 1 for p in positions)


def test_every_company_unaffordable_leaves_the_whole_amount_in_cash():
    positions, cash = allocate(targets(), flat(50_000_000), CAPITAL, cash_weight=0.0)

    assert positions == []
    assert cash == CAPITAL


def test_cash_weight_is_held_back_before_any_budget():
    positions, cash = allocate(targets(), flat(10_000), CAPITAL, cash_weight=0.25)

    assert invested(positions) <= CAPITAL * 0.75
    assert cash >= CAPITAL * 0.25


def test_cash_is_never_negative():
    for price in (1_000, 137_000, 999_999):
        _, cash = allocate(targets(), flat(price), CAPITAL, cash_weight=0.1)
        assert cash >= 0


def test_a_margin_drops_a_company_whose_budget_only_just_covers_a_share():
    """Affordability is a threshold, so a price that moved against us must not be treated
    as affordable when it sits inside the margin."""
    prices = flat(10_000) | {"000009": 495_000}

    without, _ = allocate(targets(), prices, CAPITAL, cash_weight=0.0, margin=0.0)
    with_margin, _ = allocate(targets(), prices, CAPITAL, cash_weight=0.0, margin=0.05)

    assert "000009" in {p.stock_code for p in without}
    assert "000009" not in {p.stock_code for p in with_margin}


def test_spend_leftover_leaves_less_than_the_cheapest_share():
    prices = flat(137_000)
    ideal = {f"{i:06d}": CAPITAL * WEIGHTS[i] for i in range(10)}
    shares = {code: int(ideal[code] // prices[code]) for code in prices}
    leftover = CAPITAL - sum(shares[c] * prices[c] for c in shares)

    _, remainder = spend_leftover(shares, prices, ideal, leftover)

    assert remainder < min(prices.values())


def test_spend_leftover_fills_the_largest_shortfall_first():
    prices = {"A": 100.0, "B": 100.0}
    ideal = {"A": 1_000.0, "B": 500.0}
    shares = {"A": 5, "B": 4}  # A is 500 short, B is 100 short

    filled, _ = spend_leftover(shares, prices, ideal, 100.0)

    assert filled == {"A": 6, "B": 4}


def test_spend_leftover_reaches_the_weight_cycle():
    """Five companies whose gaps all close while cash remains: the second phase has to
    spend it or 540,500 sits idle."""
    prices = {"A": 73_000.0, "B": 412_000.0, "C": 155_000.0, "D": 28_500.0, "E": 9_000.0}
    weights = {"A": 0.40, "B": 0.25, "C": 0.20, "D": 0.10, "E": 0.05}
    capital = 42_590_000
    ideal = {c: capital * w for c, w in weights.items()}
    shares = {c: int(ideal[c] // prices[c]) for c in prices}
    leftover = capital - sum(shares[c] * prices[c] for c in shares)
    assert leftover == 540_500

    filled, remainder = spend_leftover(shares, prices, ideal, leftover)

    assert remainder == 0
    assert sum(filled.values()) > sum(shares.values())


def test_spend_leftover_beats_dividing_it_equally():
    """Ten companies at 300,000 with 1,300,000 left over: an equal split gives each
    130,000 and buys nothing, which is why the leftover is not divided."""
    prices = flat(300_000)
    ideal = {f"{i:06d}": CAPITAL * WEIGHTS[i] for i in range(10)}
    shares = {c: int(ideal[c] // prices[c]) for c in prices}
    leftover = CAPITAL - sum(shares[c] * prices[c] for c in shares)
    assert leftover == 1_300_000
    assert leftover / len(prices) < min(prices.values())

    filled, remainder = spend_leftover(shares, prices, ideal, leftover)

    assert sum(filled.values()) - sum(shares.values()) == 4
    assert remainder == 100_000
