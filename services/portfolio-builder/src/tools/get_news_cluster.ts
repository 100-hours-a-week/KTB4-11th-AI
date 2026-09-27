import type { AgentTool } from "@earendil-works/pi-agent-core";
import type { SQL } from "bun";
import { Type } from "typebox";

const Params = Type.Object({ id: Type.Integer({ description: "cluster_id" }) });

export function getNewsClusterTool(sql: SQL): AgentTool<typeof Params> {
  return {
    name: "get_news_cluster",
    label: "Get news cluster",
    description:
      "One news cluster by cluster_id: title, summary, member articles, entities and the relations extracted from it.",
    parameters: Params,
    execute: async (_toolCallId, { id }) => {
      const [summary] = await sql`SELECT s.cluster_id, s.title, s.summary, c.updated_at
        FROM cluster_summaries s JOIN clusters c ON c.id = s.cluster_id WHERE s.cluster_id = ${id}`;
      if (!summary) throw new Error(`news cluster ${id} does not exist or has no summary yet`);
      const articles = await sql`SELECT a.title, a.source, a.published_at
        FROM article_clusters ac JOIN articles a ON a.id = ac.article_id
        WHERE ac.cluster_id = ${id} ORDER BY a.published_at DESC`;
      const entities = await sql`SELECT e.id, e.raw_name AS name, e.type, e.corp_code AS company_id
        FROM cluster_entities ce JOIN entities e ON e.id = ce.entity_id
        WHERE ce.cluster_id = ${id} ORDER BY e.id`;
      const relations =
        await sql`SELECT s.raw_name AS source, r.type, t.raw_name AS target, r.description
        FROM relations r
        JOIN entities s ON s.id = r.source_entity_id
        JOIN entities t ON t.id = r.target_entity_id
        WHERE r.cluster_id = ${id} ORDER BY r.id`;
      const result = {
        cluster_id: Number(summary.cluster_id),
        title: summary.title,
        summary: summary.summary,
        updated_at: new Date(summary.updated_at).toISOString(),
        articles: articles.map((a: { title: string; source: string; published_at: Date }) => ({
          title: a.title,
          source: a.source,
          published_at: new Date(a.published_at).toISOString(),
        })),
        entities: entities.map((e: { id: unknown }) => ({ ...e, id: Number(e.id) })),
        relations: [...relations],
      };
      return { content: [{ type: "text", text: JSON.stringify(result) }], details: result };
    },
  };
}
