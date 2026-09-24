import numpy as np

BLOCK_ROWS = 1024


def dbscan(vectors: np.ndarray, eps: float, min_samples: int) -> np.ndarray:
    n = len(vectors)
    neighbors: list[np.ndarray] = []
    # ponytail: O(n²) distances per run, computed in row blocks so memory stays BLOCK_ROWS × n;
    # switch to incremental DBSCAN when the "clustering cost" log shows a run no longer fits.
    for start in range(0, n, BLOCK_ROWS):
        distances = 1 - vectors[start : start + BLOCK_ROWS] @ vectors.T
        # Rounding can put a point's distance to itself above a tiny eps; it is always 0.
        np.fill_diagonal(distances[:, start:], 0)
        neighbors.extend(np.flatnonzero(row <= eps) for row in distances)

    is_core = [len(points) >= min_samples for points in neighbors]
    labels = np.full(n, -1, dtype=np.int64)
    cluster = 0
    for seed in range(n):
        if labels[seed] != -1 or not is_core[seed]:
            continue
        labels[seed] = cluster
        stack = [seed]
        while stack:
            for neighbor in neighbors[stack.pop()]:
                if labels[neighbor] == -1:
                    labels[neighbor] = cluster
                    if is_core[neighbor]:
                        stack.append(neighbor)
        cluster += 1
    return labels
