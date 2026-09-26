from collections import Counter
from typing import NamedTuple

type Clusters = dict[int, set[int]]


class Matching(NamedTuple):
    old_id_by_label: dict[int, int | None]
    unmatched_old_ids: set[int]


def match(new: Clusters, old: Clusters) -> Matching:
    old_id_by_label: dict[int, int | None] = dict.fromkeys(new)
    claimed_old_ids: set[int] = set()
    for _, old_id, label in _overlaps_by_priority(new, old):
        if old_id_by_label[label] is None and old_id not in claimed_old_ids:
            old_id_by_label[label] = old_id
            claimed_old_ids.add(old_id)
    return Matching(old_id_by_label, set(old) - claimed_old_ids)


def _overlaps_by_priority(new: Clusters, old: Clusters) -> list[tuple[int, int, int]]:
    old_id_by_article = {
        article_id: old_id for old_id, members in old.items() for article_id in members
    }
    return sorted(
        (-shared, old_id, label)
        for label, members in new.items()
        for old_id, shared in Counter(
            old_id_by_article[article_id]
            for article_id in members
            if article_id in old_id_by_article
        ).items()
    )
