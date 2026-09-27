import { beforeEach, describe, expect, test } from "bun:test";
import { SaveError, savePortfolio } from "../src/portfolio/save_portfolio.ts";
import type { Submission } from "../src/portfolio/validate_portfolio.ts";
import { HYNIX, hasDb, SAMSUNG, seedFixture, testSql } from "./db.ts";

describe.skipIf(!hasDb)("savePortfolio", () => {
  const sql = hasDb ? testSql() : (undefined as never);
  beforeEach(() => seedFixture(sql));

  const submission: Submission = {
    holdings: [
      { company_id: SAMSUNG, weight: 0.6, reason: "HBM 공급", cited_cluster_ids: [1] },
      { company_id: HYNIX, weight: 0.2, cited_cluster_ids: [] },
    ],
    exits: [],
    cash_weight: 0.2,
    commentary: "총평",
  };

  test("writes the portfolio, its holdings and citations", async () => {
    const id = await savePortfolio(sql, submission, "openai-codex/gpt-5.5");

    const [portfolio] =
      await sql`SELECT cash_weight, commentary, model FROM portfolios WHERE id = ${id}`;
    expect(portfolio).toEqual({
      cash_weight: 0.2,
      commentary: "총평",
      model: "openai-codex/gpt-5.5",
    });
    const holdings = await sql`SELECT company_id, weight, reason, cited_cluster_ids
      FROM portfolio_holdings WHERE portfolio_id = ${id} ORDER BY company_id`;
    expect(
      holdings.map((h: { cited_cluster_ids: unknown[] }) => ({
        ...h,
        cited_cluster_ids: h.cited_cluster_ids.map(Number),
      })),
    ).toEqual([
      { company_id: SAMSUNG, weight: 0.6, reason: "HBM 공급", cited_cluster_ids: [1] },
      { company_id: HYNIX, weight: 0.2, reason: null, cited_cluster_ids: [] },
    ]);
  });

  test("an unknown company rolls everything back with a readable error", async () => {
    const bad = {
      ...submission,
      holdings: [{ company_id: "99999999", weight: 1, reason: "x", cited_cluster_ids: [] }],
    };

    const error = await savePortfolio(sql, bad, "m").catch((e) => e);

    expect(error).toBeInstanceOf(SaveError);
    expect((error as SaveError).errors.join()).toContain("99999999");
    const [{ count }] = await sql`SELECT count(*)::int AS count FROM portfolios`;
    expect(count).toBe(0);
  });

  test("an unknown cited cluster rolls everything back", async () => {
    const bad = {
      ...submission,
      exits: [],
      holdings: [{ company_id: SAMSUNG, weight: 1, reason: "x", cited_cluster_ids: [1, 404] }],
    };

    const error = await savePortfolio(sql, bad, "m").catch((e) => e);

    expect((error as SaveError).errors).toEqual(["cited_cluster_ids not found: 404"]);
    const [{ count }] = await sql`SELECT count(*)::int AS count FROM portfolios`;
    expect(count).toBe(0);
  });
});
