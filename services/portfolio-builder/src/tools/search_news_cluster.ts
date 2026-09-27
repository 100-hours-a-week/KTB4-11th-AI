import type { AgentTool } from "@earendil-works/pi-agent-core";
import type { SQL } from "bun";
import { Type } from "typebox";

export function toPrefixQuery(query: string): string {
  return query
    .split(/\s+/)
    .map((term) => term.replace(/[&|!():*<>'\\]/g, ""))
    .filter(Boolean)
    .map((term) => `${term}:*`)
    .join(" & ");
}

const Params = Type.Object({ query: Type.String({ description: "space-separated words" }) });

export function searchNewsClusterTool(sql: SQL): AgentTool<typeof Params> {
  return {
    name: "search_news_cluster",
    label: "Search news clusters",
    description:
      "Full-text search over every news cluster's title and summary (not only the briefing window). Every word must match as a prefix. Returns up to 10 clusters, best first.",
    parameters: Params,
    execute: async (_toolCallId, { query }) => {
      const tsquery = toPrefixQuery(query);
      if (!tsquery) throw new Error("query has no searchable words");
      const rows = await sql`SELECT s.cluster_id, s.title, left(s.summary, 200) AS excerpt,
          ts_rank(to_tsvector('simple', s.title || ' ' || s.summary), q) AS rank
        FROM cluster_summaries s, to_tsquery('simple', ${tsquery}) AS q
        WHERE to_tsvector('simple', s.title || ' ' || s.summary) @@ q
        ORDER BY rank DESC, s.cluster_id DESC
        LIMIT 10`;
      const result = rows.map((r: { cluster_id: unknown; rank: unknown }) => ({
        ...r,
        cluster_id: Number(r.cluster_id),
        rank: Number(r.rank),
      }));
      return { content: [{ type: "text", text: JSON.stringify(result) }], details: result };
    },
  };
}
