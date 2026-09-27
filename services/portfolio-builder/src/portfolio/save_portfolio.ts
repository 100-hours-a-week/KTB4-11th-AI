import { SQL } from "bun";
import { pgArray } from "../pg_array.ts";
import type { Submission } from "./validate_portfolio.ts";

export class SaveError extends Error {
  constructor(readonly errors: string[]) {
    super(errors.join("; "));
  }
}

export async function savePortfolio(
  sql: SQL,
  submission: Submission,
  model: string,
): Promise<number> {
  const cited = [
    ...new Set([...submission.holdings, ...submission.exits].flatMap((x) => x.cited_cluster_ids)),
  ];
  try {
    return await sql.begin(async (tx) => {
      const [portfolio] = await tx`INSERT INTO portfolios (cash_weight, commentary, model)
        VALUES (${submission.cash_weight}, ${submission.commentary}, ${model}) RETURNING id`;
      const id = Number(portfolio.id);
      for (const h of submission.holdings) {
        await tx`INSERT INTO portfolio_holdings (portfolio_id, company_id, weight, reason, cited_cluster_ids)
          VALUES (${id}, ${h.company_id}, ${h.weight}, ${h.reason ?? null},
                  ${pgArray(h.cited_cluster_ids)}::bigint[])`;
      }
      for (const e of submission.exits) {
        await tx`INSERT INTO portfolio_exits (portfolio_id, company_id, reason, cited_cluster_ids)
          VALUES (${id}, ${e.company_id}, ${e.reason}, ${pgArray(e.cited_cluster_ids)}::bigint[])`;
      }
      // An array column cannot carry a foreign key, so citations are checked here instead.
      const found = await tx`SELECT id FROM clusters WHERE id = ANY(${pgArray(cited)}::bigint[])`;
      const foundIds = new Set(found.map((row: { id: unknown }) => Number(row.id)));
      const missing = cited.filter((c) => !foundIds.has(c));
      if (missing.length)
        throw new SaveError([`cited_cluster_ids not found: ${missing.join(", ")}`]);
      return id;
    });
  } catch (error) {
    if (error instanceof SaveError) throw error;
    if (error instanceof SQL.PostgresError) throw new SaveError([error.detail ?? error.message]);
    throw error;
  }
}
