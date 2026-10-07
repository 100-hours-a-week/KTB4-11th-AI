import sqlalchemy as sa


def _relation(engine, source: int, target: int, relation_type: str = "connects") -> int:
    with engine.begin() as conn:
        cluster_id = conn.execute(sa.text("SELECT min(id) FROM clusters")).scalar_one_or_none()
        if cluster_id is None:
            cluster_id = conn.execute(
                sa.text("INSERT INTO clusters DEFAULT VALUES RETURNING id")
            ).scalar_one()
        return conn.execute(
            sa.text(
                "INSERT INTO relations"
                " (cluster_id, source_entity_id, target_entity_id, type, description)"
                " VALUES (:cluster_id, :source, :target, :type, 'description') RETURNING id"
            ),
            {
                "cluster_id": cluster_id,
                "source": source,
                "target": target,
                "type": relation_type,
            },
        ).scalar_one()


def test_neighborhood_matches_normalized_name_and_alias(client, engine, seed):
    company = seed.stock("005930")
    neighbor = seed.entity("HBM")
    _relation(engine, company, neighbor)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO corporation_aliases (stock_code, alias)"
                " VALUES ('005930', 'samsungelectronics')"
            )
        )

    response = client.get("/graph/neighborhood", params={"name": " samsung electronics "})

    assert response.status_code == 200
    assert response.json() == {
        "nodes": [
            {
                "id": company,
                "hop": 0,
                "name": "name 005930",
                "type": "company",
                "company_id": "005930",
            },
            {"id": neighbor, "hop": 1, "name": "HBM", "type": "concept", "company_id": None},
        ],
        "edges": [
            {
                "id": 1,
                "source": "name 005930",
                "type": "connects",
                "target": "HBM",
                "description": "description",
                "cluster_id": 1,
                "hop": 0,
            }
        ],
        "truncated": False,
    }


def test_neighborhood_respects_depth_and_truncates(client, engine, seed):
    root = seed.entity("root")
    adjacent = [seed.entity(f"node-{index}") for index in range(201)]
    for node in adjacent:
        _relation(engine, root, node)
    for _ in range(201):
        _relation(engine, root, adjacent[0])

    response = client.get("/graph/neighborhood", params={"name": "root", "depth": 1})

    assert response.status_code == 200
    body = response.json()
    assert len(body["nodes"]) == 100
    assert len(body["edges"]) == 200
    assert body["truncated"] is True
    assert all(node["hop"] in (0, 1) for node in body["nodes"])


def test_paths_are_bidirectional_simple_and_disconnected_is_empty(client, engine, seed):
    start, middle, finish, isolated = [
        seed.entity(name) for name in ("start", "middle", "finish", "isolated")
    ]
    _relation(engine, start, middle, "first")
    _relation(engine, finish, middle, "second")
    _relation(engine, middle, start, "cycle")

    response = client.get(
        "/graph/paths", params={"from_name": "start", "to_name": "finish", "max_depth": 4}
    )

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert len(paths) == 2
    assert all(path["length"] == 2 for path in paths)
    assert all(path["steps"][1]["direction"] == "backward" for path in paths)
    assert all(path["steps"][0]["from"] != path["steps"][1]["to"] for path in paths)
    assert response.json()["truncated"] is False
    assert client.get(
        "/graph/paths", params={"from_name": "start", "to_name": "isolated"}
    ).json() == {"paths": [], "truncated": False}


def test_missing_seed_returns_candidates(client, seed):
    seed.entity("samsung electronics")

    response = client.get("/graph/neighborhood", params={"name": "samsung electronix"})

    assert response.status_code == 404
    assert response.json() == {
        "detail": {
            "message": 'no entity matches "samsung electronix"',
            "candidates": ["samsung electronics"],
        }
    }


def test_graph_parameters_are_validated(client):
    for params in ({"name": ""}, {"name": "x", "depth": 0}, {"name": "x", "depth": 4}):
        assert client.get("/graph/neighborhood", params=params).status_code == 422
    for params in (
        {"from_name": "", "to_name": "x"},
        {"from_name": "x", "to_name": "x", "max_depth": 0},
        {"from_name": "x", "to_name": "x", "max_depth": 7},
    ):
        assert client.get("/graph/paths", params=params).status_code == 422


def test_graph_rejects_names_that_normalize_to_empty(client):
    assert client.get("/graph/neighborhood", params={"name": "(주)"}).status_code == 422
    assert (
        client.get("/graph/paths", params={"from_name": "company", "to_name": "㈜"}).status_code
        == 422
    )


def test_statement_timeout_returns_gateway_timeout(client, engine, seed):
    from sqlalchemy import event

    seed.entity("timeout")

    class QueryCanceled(Exception):
        sqlstate = "57014"

    def timeout(conn, cursor, statement, parameters, context, executemany):
        if "WITH RECURSIVE" in statement:
            raise sa.exc.OperationalError(
                statement,
                parameters,
                QueryCanceled("cancelled"),
                connection_invalidated=False,
            )
        return None

    event.listen(engine, "before_cursor_execute", timeout)
    try:
        response = client.get("/graph/neighborhood", params={"name": "timeout"})
    finally:
        event.remove(engine, "before_cursor_execute", timeout)

    assert response.status_code == 504
    assert response.json() == {
        "detail": "graph query timed out; use a smaller depth or a more specific name"
    }
