import numpy as np
import pytest
from news_clusterer.dbscan import dbscan
from sklearn.cluster import DBSCAN


def unit(rows: np.ndarray) -> np.ndarray:
    return (rows / np.linalg.norm(rows, axis=1, keepdims=True)).astype(np.float32)


def blobs() -> np.ndarray:
    # 64-d blobs: within a blob cosine distance stays below ~0.02, across blobs and to the
    # scattered points it stays above ~0.4, so no eps tested here sits near a real distance.
    rng = np.random.default_rng(7)
    centers = rng.normal(size=(5, 64))
    sizes = [12, 8, 5, 2, 1]
    points = [
        center + rng.normal(scale=0.01, size=(size, 64))
        for center, size in zip(centers, sizes, strict=True)
    ]
    scattered = rng.normal(size=(10, 64))
    vectors = unit(np.vstack([*points, scattered]))
    return vectors[rng.permutation(len(vectors))]


def arc_chains() -> np.ndarray:
    # Points on the unit circle 0.1 rad apart: with eps = 1 - cos(0.15) only direct
    # neighbours connect, so chain ends are border points rather than core points.
    angles = np.concatenate([np.arange(6) * 0.1, np.pi + np.arange(4) * 0.1, [np.pi / 2]])
    return unit(np.column_stack([np.cos(angles), np.sin(angles)]))


def assert_same_partition(ours: np.ndarray, reference: np.ndarray) -> None:
    assert ((ours == -1) == (reference == -1)).all()
    pairs = set(zip(ours.tolist(), reference.tolist(), strict=True))
    assert len(pairs) == len(set(ours.tolist())) == len(set(reference.tolist()))


@pytest.mark.parametrize(
    ("make", "eps", "min_samples"),
    [
        (blobs, 0.1, 3),
        (blobs, 0.1, 6),
        (blobs, 0.1, 1),
        (blobs, 1e-6, 2),
        (arc_chains, 1 - np.cos(0.15), 3),
        (arc_chains, 1 - np.cos(0.15), 2),
    ],
)
def test_matches_sklearn(make, eps, min_samples):
    vectors = make()

    ours = dbscan(vectors, eps, min_samples)
    reference = DBSCAN(eps=eps, min_samples=min_samples, metric="cosine").fit(vectors).labels_

    assert ours.dtype == np.int64
    assert_same_partition(ours, reference)


def test_labels_are_consecutive_from_zero():
    labels = dbscan(blobs(), 0.1, 3)

    clusters = sorted(set(labels.tolist()) - {-1})
    assert clusters == list(range(len(clusters)))


def test_min_samples_one_leaves_no_noise_even_for_a_tiny_eps():
    labels = dbscan(blobs(), 1e-9, 1)

    assert (labels != -1).all()


def test_rows_beyond_one_block_are_clustered(monkeypatch):
    import news_clusterer.dbscan as module

    monkeypatch.setattr(module, "BLOCK_ROWS", 4)
    vectors = blobs()

    assert_same_partition(
        module.dbscan(vectors, 0.1, 3),
        DBSCAN(eps=0.1, min_samples=3, metric="cosine").fit(vectors).labels_,
    )


def test_empty_input():
    labels = dbscan(np.empty((0, 64), dtype=np.float32), 0.1, 3)

    assert labels.shape == (0,)


@pytest.mark.parametrize(
    ("vectors", "eps", "min_samples"),
    [
        (np.ones(4, dtype=np.float32), 0.1, 3),
        (blobs(), 0.0, 3),
        (blobs(), 0.1, 0),
    ],
)
def test_invalid_arguments_raise(vectors, eps, min_samples):
    with pytest.raises(ValueError):
        dbscan(vectors, eps, min_samples)
