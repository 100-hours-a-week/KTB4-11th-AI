export type Holding = {
  company_id: string;
  weight: number;
  reason?: string;
  cited_cluster_ids: number[];
};
export type Exit = { company_id: string; reason: string; cited_cluster_ids: number[] };
export type Submission = {
  holdings: Holding[];
  exits: Exit[];
  cash_weight: number;
  commentary: string;
};

export function validatePortfolio(submission: Submission, previous: ReadonlySet<string>): string[] {
  const errors: string[] = [];
  const held = new Set<string>();
  for (const holding of submission.holdings) {
    const id = holding.company_id;
    if (!(holding.weight >= 0)) errors.push(`holdings: ${id} weight must be >= 0`);
    if (!previous.has(id) && !holding.reason?.trim()) {
      errors.push(`holdings: ${id} is entering the portfolio and needs a reason`);
    }
    if (held.has(id)) errors.push(`holdings: ${id} appears more than once`);
    held.add(id);
  }

  const exited = new Set<string>();
  for (const exit of submission.exits) {
    const id = exit.company_id;
    if (exited.has(id)) errors.push(`exits: ${id} appears more than once`);
    exited.add(id);
    if (held.has(id)) errors.push(`exits: ${id} is both held and exited`);
    if (!previous.has(id)) errors.push(`exits: ${id} was not in the previous portfolio`);
    if (!exit.reason.trim()) errors.push(`exits: ${id} needs a reason`);
  }
  for (const id of previous) {
    if (!held.has(id) && !exited.has(id)) {
      errors.push(`exits: ${id} was held and is dropped, so it needs an exit with a reason`);
    }
  }

  if (!(submission.cash_weight >= 0)) errors.push("cash_weight must be >= 0");
  const total = submission.holdings.reduce((sum, h) => sum + h.weight, 0) + submission.cash_weight;
  if (!(total > 0)) {
    errors.push("weights and cash_weight sum to 0 or less; at least one must be positive");
  }
  if (!submission.commentary.trim()) errors.push("commentary must not be empty");
  return errors;
}
