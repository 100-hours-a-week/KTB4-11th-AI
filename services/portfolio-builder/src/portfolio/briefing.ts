import type { SQL } from "bun";
import { pgArray } from "../pg_array.ts";

export const SYSTEM_PROMPT = `You are the portfolio manager of one model portfolio of KOSPI stocks that every user of this service follows.

Goal: each run, review the previous portfolio against what has happened in the news since, and decide what to hold, at what relative weight, what to drop and how much to keep in cash. Then call submit_portfolio with the new portfolio and its reasons.

Grounding: the portfolio must carry reasons, and every stored reason must originate in the briefing or in a tool result from this run: a news cluster (cite its cluster_id), a graph relation, or a technical analysis. You may use your pre-trained knowledge while thinking (to interpret events, relate industries, decide what to look up), but a fact you know only from memory cannot be the basis of a stored reason; find it in the data with a tool first.

Rules:
- Identify companies by company_id as shown in the briefing and tool results.
- Weights are relative and non-negative; the system scales holdings and cash_weight so they sum to 1.
- Every company not in the previous portfolio needs a reason.
- Every previous holding you drop needs an entry in exits with a reason.
- Cite the cluster_ids each decision relies on in cited_cluster_ids.
- Write a commentary covering the portfolio as a whole and this run's decisions.
- If submit_portfolio returns errors, fix every one and call it again.

Tools: get_news_cluster and search_news_cluster read news clusters; search_graph and find_graph_paths explore the knowledge graph of entities and relations; analyze_technicals reads a company's technical indicators by name.`;

export type Briefing = {
  previousPortfolioId: number | null;
  previousCompanyIds: Set<string>;
  previousHoldings: number;
  previousExits: number;
  clusterIds: number[];
  companyCount: number;
  themeCount: number;
  text: string;
};

type Company = { corp_code: string; corp_name: string; stock_code: string };

const label = (c: Company) =>
  `${c.corp_name} (company_id ${c.corp_code}, stock_code ${c.stock_code})`;

export async function loadBriefing(sql: SQL, newsWindowDays: number): Promise<Briefing> {
  const [previous] = await sql`SELECT id, created_at, cash_weight, commentary FROM portfolios
    ORDER BY created_at DESC, id DESC LIMIT 1`;
  const holdings = previous
    ? await sql`SELECT c.corp_code, c.corp_name, c.stock_code, h.weight, h.reason
        FROM portfolio_holdings h JOIN companies c ON c.corp_code = h.company_id
        WHERE h.portfolio_id = ${previous.id} ORDER BY h.weight DESC`
    : [];
  const exits = previous
    ? await sql`SELECT c.corp_code, c.corp_name, c.stock_code, e.reason
        FROM portfolio_exits e JOIN companies c ON c.corp_code = e.company_id
        WHERE e.portfolio_id = ${previous.id}`
    : [];

  const clusters = await sql`SELECT s.cluster_id, s.title, s.summary, c.updated_at
    FROM cluster_summaries s JOIN clusters c ON c.id = s.cluster_id
    WHERE c.updated_at >= now() - make_interval(days => ${newsWindowDays})
    ORDER BY c.updated_at DESC`;
  const clusterIds = clusters.map((c: { cluster_id: unknown }) => Number(c.cluster_id));
  const mentions =
    await sql`SELECT DISTINCT ce.cluster_id, co.corp_code, co.corp_name, co.stock_code
    FROM cluster_entities ce
    JOIN entities e ON e.id = ce.entity_id
    JOIN companies co ON co.corp_code = e.corp_code
    WHERE ce.cluster_id = ANY(${pgArray(clusterIds)}::bigint[])
    ORDER BY co.corp_code`;
  const companies = new Map<string, Company & { clusters: number[] }>();
  for (const m of mentions) {
    const entry = companies.get(m.corp_code) ?? { ...m, clusters: [] };
    entry.clusters.push(Number(m.cluster_id));
    companies.set(m.corp_code, entry);
  }
  const themes = await sql`SELECT tc.corp_code, t.name, tc.is_main
    FROM theme_companies tc JOIN themes t ON t.theme_code = tc.theme_code
    WHERE tc.corp_code = ANY(${pgArray([...companies.keys()])}::text[])
    ORDER BY tc.is_main DESC, t.name`;
  const themesByCompany = new Map<string, string[]>();
  for (const t of themes) {
    const names = themesByCompany.get(t.corp_code) ?? [];
    names.push(t.is_main ? `${t.name} (main)` : t.name);
    themesByCompany.set(t.corp_code, names);
  }

  const lines: string[] = ["# Previous portfolio"];
  if (!previous) {
    lines.push("None: this is the first portfolio, so every holding is an entry.");
  } else {
    lines.push(
      `Portfolio ${previous.id}, created ${new Date(previous.created_at).toISOString()}, cash_weight ${previous.cash_weight}`,
    );
    for (const h of holdings)
      lines.push(
        `- ${label(h)}: weight ${h.weight}. Reason: ${h.reason ?? "(kept, no new reason)"}`,
      );
    if (exits.length) lines.push("Exited last time:");
    for (const e of exits) lines.push(`- ${label(e)}. Reason: ${e.reason}`);
    lines.push(`Commentary: ${previous.commentary}`);
  }
  lines.push("", `# News clusters updated in the last ${newsWindowDays} days`);
  if (!clusters.length) lines.push("None.");
  for (const c of clusters) {
    lines.push(
      `## [cluster ${c.cluster_id}] ${c.title}`,
      `Updated ${new Date(c.updated_at).toISOString()}`,
      c.summary,
      "",
    );
  }
  lines.push("# Companies mentioned in these clusters");
  if (!companies.size) lines.push("None.");
  for (const c of companies.values()) {
    const themeText = themesByCompany.get(c.corp_code)?.join(", ") ?? "none";
    lines.push(`- ${label(c)}: clusters ${c.clusters.join(", ")}; themes: ${themeText}`);
  }

  return {
    previousPortfolioId: previous ? Number(previous.id) : null,
    previousCompanyIds: new Set(holdings.map((h: Company) => h.corp_code)),
    previousHoldings: holdings.length,
    previousExits: exits.length,
    clusterIds,
    companyCount: companies.size,
    themeCount: themes.length,
    text: lines.join("\n"),
  };
}
