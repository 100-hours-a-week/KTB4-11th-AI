import pytest
from portfolio_rebalancer.__main__ import tick


def test_a_tick_refuses_while_the_store_is_missing():
    """#42 carries the PostgreSQL store. Until it lands, a tick must fail loudly rather
    than poll the Backend and decide nothing."""
    with pytest.raises(NotImplementedError, match="#42"):
        tick(db=object(), client=object(), token="a-token")
