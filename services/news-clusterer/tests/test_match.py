from news_clusterer.match import match


def test_unchanged_clusters_keep_their_ids():
    matches, unmatched = match({0: {1, 2}, 1: {3, 4}}, {10: {3, 4}, 20: {1, 2}})

    assert matches == {0: 20, 1: 10}
    assert unmatched == set()


def test_a_grown_cluster_keeps_its_id():
    matches, unmatched = match({0: {1, 2, 3}}, {10: {1, 2}})

    assert matches == {0: 10}
    assert unmatched == set()


def test_a_split_keeps_the_id_on_the_larger_part():
    matches, unmatched = match({0: {1}, 1: {2, 3}}, {10: {1, 2, 3}})

    assert matches == {0: None, 1: 10}
    assert unmatched == set()


def test_a_merge_keeps_the_id_with_the_larger_overlap():
    matches, unmatched = match({0: {1, 2, 3}}, {10: {1}, 20: {2, 3}})

    assert matches == {0: 20}
    assert unmatched == {10}


def test_equal_overlaps_prefer_the_lower_old_id_then_the_lower_label():
    matches, unmatched = match({0: {1, 2}, 1: {3, 4}}, {10: {1, 3}, 20: {2, 4}})

    assert matches == {0: 10, 1: 20}
    assert unmatched == set()


def test_first_run_and_vanished_clusters():
    assert match({0: {1}}, {}) == ({0: None}, set())
    assert match({}, {10: {1}}) == ({}, {10})
