import sqlalchemy as sa
from ktb_core.normalize import normalize

NODE_LIMIT = 100
EDGE_LIMIT = 200
PATH_LIMIT = 20
TIMEOUT_MESSAGE = "graph query timed out; use a smaller depth or a more specific name"


class MissingEntity(Exception):
    def __init__(self, name: str, candidates: list[str]) -> None:
        self.message = f'no entity matches "{name}"'
        self.candidates = candidates
        super().__init__(self.message)


class InvalidEntityName(Exception):
    pass


def _set_timeout(conn: sa.Connection) -> None:
    conn.execute(sa.text("SET LOCAL statement_timeout = '10s'"))


def _like_contains(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _seeds(conn: sa.Connection, name: str) -> list[int]:
    normalized = normalize(name)
    if not normalized:
        raise InvalidEntityName("entity name must not be empty after normalization")
    seeds = list(
        conn.execute(
            sa.text(
                "SELECT e.id FROM entities e WHERE e.name LIKE :pattern"
                " UNION SELECT e.id FROM corporation_aliases a"
                " JOIN entities e ON e.stock_code = a.stock_code WHERE a.alias = :alias"
                " ORDER BY id"
            ),
            {"pattern": _like_contains(normalized), "alias": normalized},
        ).scalars()
    )
    if seeds:
        return seeds
    candidates = list(
        conn.execute(
            sa.text(
                "SELECT DISTINCT raw_name FROM entities WHERE name LIKE :pattern"
                " ORDER BY raw_name LIMIT 5"
            ),
            {"pattern": _like_contains(normalized[:2])},
        ).scalars()
    )
    raise MissingEntity(name, candidates)


def get_neighborhood(conn: sa.Connection, name: str, depth: int) -> dict[str, object]:
    _set_timeout(conn)
    seeds = _seeds(conn, name)
    nodes = [
        dict(row)
        for row in conn.execute(
            sa.text(
                "WITH RECURSIVE edges AS ("
                " SELECT source_entity_id AS a, target_entity_id AS b FROM relations"
                " UNION ALL SELECT target_entity_id, source_entity_id FROM relations"
                "), walk(entity_id, hop) AS ("
                " SELECT id, 0 FROM unnest(CAST(:seeds AS bigint[])) AS s(id)"
                " UNION SELECT e.b, w.hop + 1 FROM walk w JOIN edges e ON e.a = w.entity_id"
                " WHERE w.hop < :depth"
                ") SELECT w.entity_id AS id, min(w.hop) AS hop, e.raw_name AS name, e.type,"
                " c.corp_code AS company_id FROM walk w JOIN entities e ON e.id = w.entity_id"
                " LEFT JOIN corporations c ON c.stock_code = e.stock_code"
                " GROUP BY w.entity_id, e.raw_name, e.type, c.corp_code"
                " ORDER BY hop, id LIMIT :limit"
            ),
            {"seeds": seeds, "depth": depth, "limit": NODE_LIMIT + 1},
        ).mappings()
    ]
    truncated = len(nodes) > NODE_LIMIT
    nodes = nodes[:NODE_LIMIT]
    hops = {node["id"]: node["hop"] for node in nodes}
    rows = conn.execute(
        sa.text(
            "SELECT r.id, r.source_entity_id, r.target_entity_id, s.raw_name AS source,"
            " r.type, t.raw_name AS target, r.description, r.cluster_id"
            " FROM relations r JOIN entities s ON s.id = r.source_entity_id"
            " JOIN entities t ON t.id = r.target_entity_id"
            " WHERE r.source_entity_id = ANY(CAST(:ids AS bigint[]))"
            " AND r.target_entity_id = ANY(CAST(:ids AS bigint[])) ORDER BY r.id"
        ),
        {"ids": list(hops)},
    ).mappings()
    edges = [
        {
            "id": row["id"],
            "source": row["source"],
            "type": row["type"],
            "target": row["target"],
            "description": row["description"],
            "cluster_id": row["cluster_id"],
            "hop": min(hops[row["source_entity_id"]], hops[row["target_entity_id"]]),
        }
        for row in rows
    ]
    edges.sort(key=lambda edge: (edge["hop"], edge["id"]))
    truncated = truncated or len(edges) > EDGE_LIMIT
    return {"nodes": nodes, "edges": edges[:EDGE_LIMIT], "truncated": truncated}


def get_paths(
    conn: sa.Connection, from_name: str, to_name: str, max_depth: int
) -> dict[str, object]:
    _set_timeout(conn)
    sources = _seeds(conn, from_name)
    targets = _seeds(conn, to_name)
    paths = conn.execute(
        sa.text(
            "WITH RECURSIVE edges AS ("
            " SELECT id AS relation_id, source_entity_id AS a, target_entity_id AS b,"
            " 'forward'::text AS direction FROM relations UNION ALL"
            " SELECT id, target_entity_id, source_entity_id, 'backward'::text FROM relations"
            "), paths(node, nodes, relation_ids, directions) AS ("
            " SELECT id, ARRAY[id], ARRAY[]::bigint[], ARRAY[]::text[]"
            " FROM unnest(CAST(:sources AS bigint[])) AS s(id) UNION ALL"
            " SELECT e.b, p.nodes || e.b, p.relation_ids || e.relation_id,"
            " p.directions || e.direction FROM paths p JOIN edges e ON e.a = p.node"
            " WHERE cardinality(p.relation_ids) < :max_depth"
            " AND e.b <> ALL(p.nodes) AND p.node <> ALL(CAST(:targets AS bigint[]))"
            ") SELECT nodes, relation_ids, directions FROM paths"
            " WHERE node = ANY(CAST(:targets AS bigint[])) AND cardinality(relation_ids) > 0"
            " ORDER BY cardinality(relation_ids), nodes LIMIT :limit"
        ),
        {"sources": sources, "targets": targets, "max_depth": max_depth, "limit": PATH_LIMIT + 1},
    ).all()
    truncated = len(paths) > PATH_LIMIT
    paths = paths[:PATH_LIMIT]
    node_ids = sorted({node_id for path in paths for node_id in path.nodes})
    relation_ids = sorted({relation_id for path in paths for relation_id in path.relation_ids})
    names = dict(
        conn.execute(
            sa.text("SELECT id, raw_name FROM entities WHERE id = ANY(CAST(:ids AS bigint[]))"),
            {"ids": node_ids},
        ).all()
    )
    relation_rows = conn.execute(
        sa.text(
            "SELECT id, type, description, cluster_id FROM relations"
            " WHERE id = ANY(CAST(:ids AS bigint[]))"
        ),
        {"ids": relation_ids},
    ).mappings()
    relation_by_id = {row["id"]: row for row in relation_rows}
    return {
        "paths": [
            {
                "length": len(path.relation_ids),
                "steps": [
                    {
                        "from": names[path.nodes[index]],
                        "to": names[path.nodes[index + 1]],
                        "type": relation_by_id[relation_id]["type"],
                        "direction": path.directions[index],
                        "description": relation_by_id[relation_id]["description"],
                        "cluster_id": relation_by_id[relation_id]["cluster_id"],
                    }
                    for index, relation_id in enumerate(path.relation_ids)
                ],
            }
            for path in paths
        ],
        "truncated": truncated,
    }
