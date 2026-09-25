from market_collector import universe


def test_the_package_exposes_its_public_surface():
    # EmptyUniverseError is physically defined in repository.py (it is
    # raised from both repository.latest_members and service.sync_universe),
    # but the package's __init__ is where every consumer imports it from,
    # per the design's module table.
    assert universe.EmptyUniverseError is not None
    assert universe.IndexMember is not None
    assert universe.IndexSource is not None
    assert callable(universe.fetch_members)
    assert callable(universe.latest_members)
    assert callable(universe.sync_universe)
    assert callable(universe.upsert_members)


def test_kospi200_csv_and_its_loader_are_gone():
    assert not hasattr(universe, "load_kospi200")
    assert not hasattr(universe, "load_from")
