import { expect, test } from "bun:test";
import { normalizeWeights } from "../src/portfolio/normalize_weights.ts";

test("scales weights and cash so they sum to one", () => {
  const result = normalizeWeights({
    holdings: [
      { company_id: "a", weight: 0.1, cited_cluster_ids: [] },
      { company_id: "b", weight: 0.1, cited_cluster_ids: [] },
    ],
    exits: [],
    cash_weight: 0.05,
    commentary: "c",
  });

  expect(result.holdings.map((h) => h.weight)).toEqual([0.4, 0.4]);
  expect(result.cash_weight).toBeCloseTo(0.2);
});
