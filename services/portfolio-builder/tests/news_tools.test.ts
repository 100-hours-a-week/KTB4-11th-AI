import { beforeAll, describe, expect, test } from "bun:test";
import { getNewsClusterTool } from "../src/tools/get_news_cluster.ts";
import { searchNewsClusterTool, toPrefixQuery } from "../src/tools/search_news_cluster.ts";
import { hasDb, seedFixture, testSql } from "./db.ts";

test("builds a prefix tsquery and drops tsquery operators", () => {
  expect(toPrefixQuery(" 삼성 HBM ")).toBe("삼성:* & HBM:*");
  expect(toPrefixQuery("a&b | !c:*")).toBe("ab:* & c:*");
  expect(toPrefixQuery("&&")).toBe("");
});

describe.skipIf(!hasDb)("news tools", () => {
  const sql = hasDb ? testSql() : (undefined as never);
  beforeAll(() => seedFixture(sql));

  test("get_news_cluster returns summary, articles, entities and relations", async () => {
    const result = await getNewsClusterTool(sql).execute("call-1", { id: 1 });

    expect(result.details).toMatchObject({
      cluster_id: 1,
      title: "삼성전자 HBM 공급 확대",
      articles: [{ title: expect.any(String) }, { title: expect.any(String) }],
      relations: [
        { source: "삼성전자", type: "supplies", target: "엔비디아" },
        { source: "HBM", type: "used_by", target: "엔비디아" },
      ],
    });
    expect(result.details.entities).toContainEqual({
      id: 1,
      name: "삼성전자",
      type: "company",
      company_id: "00126380",
    });
  });

  test("get_news_cluster rejects an unknown id", async () => {
    await expect(getNewsClusterTool(sql).execute("call-1", { id: 999 })).rejects.toThrow("999");
  });

  test("search_news_cluster matches a word with a particle attached", async () => {
    // Cluster 2 contains only "SK하이닉스가"; a non-prefix query would miss it.
    const result = await searchNewsClusterTool(sql).execute("call-1", { query: "SK하이닉스" });

    expect(result.details.map((r: { cluster_id: number }) => r.cluster_id)).toEqual([2]);
  });

  test("search_news_cluster rejects a query with no searchable terms", async () => {
    await expect(searchNewsClusterTool(sql).execute("call-1", { query: "&|" })).rejects.toThrow();
  });
});
