import { expect, test } from "bun:test";
import { type Submission, validatePortfolio } from "../src/portfolio/validate_portfolio.ts";

const base: Submission = {
  holdings: [{ company_id: "A", weight: 1, reason: "news", cited_cluster_ids: [1] }],
  exits: [],
  cash_weight: 0,
  commentary: "overall",
};

test("accepts a first portfolio whose entries all have reasons", () => {
  expect(validatePortfolio(base, new Set())).toEqual([]);
});

test("a held company keeps its place without a new reason", () => {
  const submission = { ...base, holdings: [{ company_id: "A", weight: 1, cited_cluster_ids: [] }] };
  expect(validatePortfolio(submission, new Set(["A"]))).toEqual([]);
});

test("reports every problem at once", () => {
  const submission: Submission = {
    holdings: [
      { company_id: "A", weight: -1, cited_cluster_ids: [] },
      { company_id: "A", weight: 0, reason: "dup", cited_cluster_ids: [] },
      { company_id: "C", weight: 0, reason: "  ", cited_cluster_ids: [] },
    ],
    exits: [
      { company_id: "A", reason: "both", cited_cluster_ids: [] },
      { company_id: "Z", reason: "", cited_cluster_ids: [] },
    ],
    cash_weight: -0.5,
    commentary: " ",
  };

  expect(validatePortfolio(submission, new Set(["B"]))).toEqual([
    "holdings: A weight must be >= 0",
    "holdings: A is entering the portfolio and needs a reason",
    "holdings: A appears more than once",
    "holdings: C is entering the portfolio and needs a reason",
    "exits: A is both held and exited",
    "exits: A was not in the previous portfolio",
    "exits: Z was not in the previous portfolio",
    "exits: Z needs a reason",
    "exits: B was held and is dropped, so it needs an exit with a reason",
    "cash_weight must be >= 0",
    "weights and cash_weight sum to 0 or less; at least one must be positive",
    "commentary must not be empty",
  ]);
});

test("an exited previous holding needs only its exit", () => {
  const submission: Submission = {
    ...base,
    exits: [{ company_id: "B", reason: "guidance cut", cited_cluster_ids: [2] }],
  };
  expect(validatePortfolio(submission, new Set(["B"]))).toEqual([]);
});
