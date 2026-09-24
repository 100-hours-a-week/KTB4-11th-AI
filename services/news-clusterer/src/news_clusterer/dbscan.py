import numpy as np
from numpy.typing import NDArray

NOISE = -1
BLOCK_ROWS = 1024


def dbscan(vectors: NDArray[np.floating], eps: float, min_samples: int) -> NDArray[np.int64]:
    if vectors.ndim != 2:
        raise ValueError(f"vectors must be a 2-D array, got shape {vectors.shape}")
    if eps <= 0:
        raise ValueError(f"eps must be positive, got {eps}")
    if min_samples < 1:
        raise ValueError(f"min_samples must be at least 1, got {min_samples}")

    neighborhoods = _cosine_neighborhoods(vectors, eps)
    is_core = [len(neighborhood) >= min_samples for neighborhood in neighborhoods]
    return _label_clusters(neighborhoods, is_core)


def _cosine_neighborhoods(vectors: NDArray[np.floating], eps: float) -> list[NDArray[np.intp]]:
    neighborhoods: list[NDArray[np.intp]] = []
    for start in range(0, len(vectors), BLOCK_ROWS):
        distances = 1.0 - vectors[start : start + BLOCK_ROWS] @ vectors.T
        distances_to_self = distances[:, start:]
        np.fill_diagonal(distances_to_self, 0.0)
        neighborhoods.extend(np.flatnonzero(row <= eps) for row in distances)
    return neighborhoods


def _label_clusters(
    neighborhoods: list[NDArray[np.intp]], is_core: list[bool]
) -> NDArray[np.int64]:
    labels = np.full(len(neighborhoods), NOISE, dtype=np.int64)
    cluster = 0
    for seed in range(len(neighborhoods)):
        if not is_core[seed] or labels[seed] != NOISE:
            continue
        labels[seed] = cluster
        frontier = [seed]
        while frontier:
            for neighbor in neighborhoods[frontier.pop()]:
                if labels[neighbor] == NOISE:
                    labels[neighbor] = cluster
                    if is_core[neighbor]:
                        frontier.append(neighbor)
        cluster += 1
    return labels
