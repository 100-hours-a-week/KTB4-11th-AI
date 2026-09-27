import type { AgentTool } from "@earendil-works/pi-agent-core";
import type { SQL } from "bun";
import { Type } from "typebox";
import type { Log } from "../log.ts";
import { normalizeWeights } from "../portfolio/normalize_weights.ts";
import { SaveError, savePortfolio } from "../portfolio/save_portfolio.ts";
import { type Submission, validatePortfolio } from "../portfolio/validate_portfolio.ts";

const CitedClusters = Type.Array(Type.Integer({ minimum: 1 }), {
  description: "cluster_ids this decision relies on",
});

const Params = Type.Object({
  holdings: Type.Array(
    Type.Object({
      company_id: Type.String(),
      weight: Type.Number({ minimum: 0 }),
      reason: Type.Optional(
        Type.String({ description: "required for a company entering the portfolio" }),
      ),
      cited_cluster_ids: CitedClusters,
    }),
  ),
  exits: Type.Array(
    Type.Object({
      company_id: Type.String(),
      reason: Type.String(),
      cited_cluster_ids: CitedClusters,
    }),
    { description: "every previous holding that is dropped" },
  ),
  cash_weight: Type.Number({ minimum: 0 }),
  commentary: Type.String({
    description: "overall assessment of the portfolio and this run's decisions",
  }),
});

export function submitPortfolioTool(deps: {
  sql: SQL;
  previous: ReadonlySet<string>;
  model: string;
  log: Log;
}): AgentTool<typeof Params> {
  return {
    name: "submit_portfolio",
    label: "Submit portfolio",
    description:
      "Submit the new model portfolio. Weights are relative; the system scales holdings and cash_weight to sum to 1. On errors, fix every one and submit again.",
    parameters: Params,
    execute: async (_toolCallId, submission: Submission) => {
      const errors = validatePortfolio(submission, deps.previous);
      if (!errors.length) {
        try {
          const id = await savePortfolio(deps.sql, normalizeWeights(submission), deps.model);
          return {
            content: [{ type: "text", text: `Saved portfolio ${id}.` }],
            details: { portfolio_id: id },
            terminate: true,
          };
        } catch (error) {
          if (!(error instanceof SaveError)) throw error;
          errors.push(...error.errors);
        }
      }
      deps.log("validation_failed", { errors }, "WARNING");
      throw new Error(
        `The portfolio was not saved. Fix every error and call submit_portfolio again:\n- ${errors.join("\n- ")}`,
      );
    },
  };
}
