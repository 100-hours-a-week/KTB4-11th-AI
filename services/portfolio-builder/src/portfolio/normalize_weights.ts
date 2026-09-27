import type { Submission } from "./validate_portfolio.ts";

export function normalizeWeights(submission: Submission): Submission {
  const total = submission.holdings.reduce((sum, h) => sum + h.weight, 0) + submission.cash_weight;
  return {
    ...submission,
    holdings: submission.holdings.map((h) => ({ ...h, weight: h.weight / total })),
    cash_weight: submission.cash_weight / total,
  };
}
