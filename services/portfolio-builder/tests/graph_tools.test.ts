import { beforeAll, beforeEach, describe, expect, test } from "bun:test";
import { findGraphPathsTool } from "../src/tools/find_graph_paths.ts";
import { findSeedEntities, withGraphTimeout } from "../src/tools/graph_seeds.ts";
import { searchGraphTool } from "../src/tools/search_graph.ts";
import { hasDb, seedFixture, testSql } from "./db.ts";

describe.skipIf(!hasDb)("graph tools", () => {
  const sql = hasDb ? testSql() : (undefined as never);
  beforeAll(() => seedFixture(sql));

  test("seeds match normalized names and company aliases", async () => {
    expect(await findSeedEntities(sql, "㈜ 삼성전자")).toEqual([1]);
    expect(await findSeedEntities(sql, "hbm")).toEqual([4]);
  });

  test("an unknown name lists candidates", async () => {
    await expect(findSeedEntities(sql, "삼성바이오")).rejects.toThrow("삼성전자");
  });

  test.each([
    [1, [1, 2]],
    [2, [1, 2, 4]],
    [3, [1, 2, 4, 3]],
  ])("search_graph at depth %i reaches %p, nearest first", async (depth, expected) => {
    const result = await searchGraphTool(sql).execute("c", { name: "삼성전자", depth });

    expect(result.details.nodes.map((n: { id: number }) => n.id)).toEqual(expected);
  });

  test("search_graph returns edges between reached nodes with their cluster", async () => {
    const result = await searchGraphTool(sql).execute("c", { name: "삼성전자", depth: 2 });

    expect(result.details.edges).toEqual([
      expect.objectContaining({
        source: "삼성전자",
        type: "supplies",
        target: "엔비디아",
        cluster_id: 1,
        hop: 0,
      }),
      expect.objectContaining({
        source: "HBM",
        type: "used_by",
        target: "엔비디아",
        cluster_id: 1,
        hop: 1,
      }),
    ]);
  });

  test("find_graph_paths walks edges in both directions without revisiting nodes", async () => {
    const result = await findGraphPathsTool(sql).execute("c", {
      from_name: "삼성전자",
      to_name: "SK하이닉스",
      max_depth: 4,
    });

    expect(result.details.paths).toHaveLength(1);
    expect(result.details.paths[0].steps).toEqual([
      expect.objectContaining({
        from: "삼성전자",
        to: "엔비디아",
        type: "supplies",
        direction: "forward",
        cluster_id: 1,
      }),
      expect.objectContaining({
        from: "엔비디아",
        to: "HBM",
        type: "used_by",
        direction: "backward",
        cluster_id: 1,
      }),
      expect.objectContaining({
        from: "HBM",
        to: "SK하이닉스",
        type: "produces",
        direction: "backward",
        cluster_id: 2,
      }),
    ]);
  });

  test("find_graph_paths respects max_depth", async () => {
    const result = await findGraphPathsTool(sql).execute("c", {
      from_name: "삼성전자",
      to_name: "SK하이닉스",
      max_depth: 2,
    });

    expect(result.details.paths).toEqual([]);
  });

  test("withGraphTimeout maps a Postgres statement timeout to a plain error", async () => {
    await expect(
      withGraphTimeout(sql, async (tx) => {
        await tx`SET LOCAL statement_timeout = '10ms'`;
        await tx`SELECT pg_sleep(1)`;
      }),
    ).rejects.toThrow("graph query timed out");
  });
});

describe.skipIf(!hasDb)("find_graph_paths with a cycle in the graph", () => {
  const sql = hasDb ? testSql() : (undefined as never);
  beforeEach(async () => {
    await seedFixture(sql);
    await sql`INSERT INTO relations (id, cluster_id, source_entity_id, target_entity_id, type, description)
      OVERRIDING SYSTEM VALUE VALUES (4, 1, 1, 4, 'related_to', '삼성전자와 HBM')`;
  });

  test("does not revisit a node already on the path", async () => {
    const result = await findGraphPathsTool(sql).execute("c", {
      from_name: "삼성전자",
      to_name: "SK하이닉스",
      max_depth: 4,
    });

    expect(result.details.paths.map((p: { length: number }) => p.length)).toEqual([2, 3]);
  });
});
