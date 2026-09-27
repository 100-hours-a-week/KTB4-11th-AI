import { beforeEach, describe, expect, test } from "bun:test";
import { loadBriefing, SYSTEM_PROMPT } from "../src/portfolio/briefing.ts";
import { savePortfolio } from "../src/portfolio/save_portfolio.ts";
import { HYNIX, hasDb, SAMSUNG, seedFixture, testSql } from "./db.ts";

test("the system prompt states the goal and the grounding rule", () => {
  expect(SYSTEM_PROMPT).toContain("model portfolio");
  expect(SYSTEM_PROMPT).toContain("pre-trained knowledge");
  expect(SYSTEM_PROMPT).toContain("submit_portfolio");
});

describe.skipIf(!hasDb)("loadBriefing", () => {
  const sql = hasDb ? testSql() : (undefined as never);
  beforeEach(() => seedFixture(sql));

  test("first run: only clusters inside the window, with their companies and themes", async () => {
    const briefing = await loadBriefing(sql, 7);

    expect(briefing.previousPortfolioId).toBeNull();
    expect(briefing.previousCompanyIds.size).toBe(0);
    expect(briefing.clusterIds).toEqual([1]);
    expect(briefing.companyCount).toBe(1);
    expect(briefing.themeCount).toBe(1);
    expect(briefing.text).toContain("first portfolio");
    expect(briefing.text).toContain("[cluster 1] 삼성전자 HBM 공급 확대");
    expect(briefing.text).toContain(`삼성전자 (company_id ${SAMSUNG}, stock_code 005930)`);
    expect(briefing.text).toContain("HBM (main)");
    expect(briefing.text).not.toContain("반도체 수출 둔화");
  });

  test("uses the most recently created portfolio as the previous one", async () => {
    await savePortfolio(
      sql,
      {
        holdings: [{ company_id: HYNIX, weight: 1, reason: "old", cited_cluster_ids: [] }],
        exits: [],
        cash_weight: 0,
        commentary: "older",
      },
      "m",
    );
    const latest = await savePortfolio(
      sql,
      {
        holdings: [{ company_id: SAMSUNG, weight: 1, reason: "new", cited_cluster_ids: [1] }],
        exits: [],
        cash_weight: 0,
        commentary: "newer",
      },
      "m",
    );

    const briefing = await loadBriefing(sql, 7);

    expect(briefing.previousPortfolioId).toBe(latest);
    expect([...briefing.previousCompanyIds]).toEqual([SAMSUNG]);
    expect(briefing.text).toContain("newer");
    expect(briefing.text).not.toContain("older");
  });
});
