def match(
    new: dict[int, set[int]], old: dict[int, set[int]]
) -> tuple[dict[int, int | None], set[int]]:
    # ponytail: compares every (new, old) pair; index articles by old cluster if this gets slow.
    pairs = sorted(
        (-len(members & previous), old_id, label)
        for label, members in new.items()
        for old_id, previous in old.items()
        if members & previous
    )
    matches: dict[int, int | None] = dict.fromkeys(new)
    taken: set[int] = set()
    for _, old_id, label in pairs:
        if matches[label] is None and old_id not in taken:
            matches[label] = old_id
            taken.add(old_id)
    return matches, set(old) - taken
