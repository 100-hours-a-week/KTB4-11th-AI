import pytest
from portfolio_rebalancer_http.reservations import (
    PRICE_BANDS,
    read_reservation,
    reservation_prices,
)

REFERENCES = (1_234.0, 9_050.0, 78_000.0, 155_500.0, 412_000.0, 1_800_000.0)


def krx_tick(price: float) -> int:
    """The Backend rounds to these. Reproduced here only to prove recovery survives it."""
    for ceiling, tick in (
        (2_000, 1),
        (5_000, 5),
        (20_000, 10),
        (50_000, 50),
        (200_000, 100),
        (500_000, 500),
    ):
        if price < ceiling:
            return tick
    return 1_000


def rounded(price: float) -> float:
    tick = krx_tick(price)
    return round(price / tick) * tick


def test_each_band_sits_either_side_of_the_reference():
    for day, band in enumerate(PRICE_BANDS):
        low, high = reservation_prices(78_000.0, day)

        assert low == pytest.approx(78_000.0 * (1 - band))
        assert high == pytest.approx(78_000.0 * (1 + band))


def test_the_bands_narrow():
    widths = [
        high - low
        for low, high in (reservation_prices(78_000.0, d) for d in range(len(PRICE_BANDS)))
    ]

    assert widths == sorted(widths, reverse=True)


def test_past_the_last_band_there_is_no_pair():
    assert reservation_prices(78_000.0, len(PRICE_BANDS)) is None


@pytest.mark.parametrize("reference", REFERENCES)
def test_a_pair_recovers_its_reference_and_day(reference):
    for day in range(len(PRICE_BANDS)):
        assert read_reservation(*reservation_prices(reference, day)) == (reference, day)


@pytest.mark.parametrize("reference", REFERENCES)
def test_recovery_survives_the_backends_tick_rounding(reference):
    """The Backend rounds both prices before they come back on a pending order, and the
    recovered day still has to be right."""
    for day in range(len(PRICE_BANDS)):
        low, high = reservation_prices(reference, day)

        recovered_reference, recovered_day = read_reservation(rounded(low), rounded(high))

        assert recovered_day == day
        assert recovered_reference == pytest.approx(reference, rel=1e-4)


@pytest.mark.parametrize("reference", REFERENCES)
def test_no_band_rounds_into_a_neighbour(reference):
    """Rounding is the only thing that could make two bands collide, so the recovered
    ratio has to stay nearer its own band than either neighbour."""
    for day, band in enumerate(PRICE_BANDS):
        low, high = (rounded(p) for p in reservation_prices(reference, day))
        ratio = (high - low) / (high + low)

        own = abs(ratio - band)
        others = [abs(ratio - other) for i, other in enumerate(PRICE_BANDS) if i != day]
        assert own < min(others)
