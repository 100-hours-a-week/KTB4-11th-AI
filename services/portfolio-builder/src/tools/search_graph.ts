import type { AgentTool } from "@earendil-works/pi-agent-core";
import type { SQL } from "bun";
import { Type } from "typebox";
import { pgArray } from "../pg_array.ts";
import { findSeedEntities, withGraphTimeout } from "./graph_seeds.ts";

const Params = Type.Object({
  name: Type.String(),
  depth: Type.Optional(Type.Integer({ minimum: 1, maximum: 3, default: 2 })),
});

export function searchGraphTool(sql: SQL): AgentTool<typeof Params> {
  return {
    name: "search_graph",
    label: "Search knowledge graph",
    description:
      "The knowledge-graph neighbourhood of an entity (company, product, person, ...) found by name: every entity within `depth` hops over relations in either direction, and every relation among them with the cluster_id it came from. Nearest first.",
    parameters: Params,
    execute: async (_toolCallId, { name, depth = 2 }) => {
      const seeds = await findSeedEntities(sql, name);
      const result = await withGraphTimeout(sql, async (tx) => {
        const nodes = await tx`WITH RECURSIVE edges AS (
            SELECT source_entity_id AS a, target_entity_id AS b FROM relations
            UNION ALL
            SELECT target_entity_id, source_entity_id FROM relations
          ), walk(entity_id, hop) AS (
            SELECT id, 0 FROM unnest(${pgArray(seeds)}::bigint[]) AS s(id)
            UNION
            SELECT e.b, w.hop + 1 FROM walk w JOIN edges e ON e.a = w.entity_id WHERE w.hop < ${depth}
          )
          SELECT w.entity_id AS id, min(w.hop) AS hop, e.raw_name AS name, e.type, e.corp_code AS company_id
          FROM walk w JOIN entities e ON e.id = w.entity_id
          GROUP BY w.entity_id, e.raw_name, e.type, e.corp_code
          ORDER BY hop, id`;
        const hops = new Map<number, number>(
          nodes.map((n: { id: unknown; hop: unknown }) => [Number(n.id), Number(n.hop)]),
        );
        const ids = pgArray([...hops.keys()]);
        const edges =
          await tx`SELECT r.id, r.source_entity_id, r.target_entity_id, s.raw_name AS source,
            r.type, t.raw_name AS target, r.description, r.cluster_id
          FROM relations r
          JOIN entities s ON s.id = r.source_entity_id
          JOIN entities t ON t.id = r.target_entity_id
          WHERE r.source_entity_id = ANY(${ids}::bigint[]) AND r.target_entity_id = ANY(${ids}::bigint[])
          ORDER BY r.id`;
        return {
          nodes: nodes.map((n: { id: unknown; hop: unknown }) => ({
            ...n,
            id: Number(n.id),
            hop: Number(n.hop),
          })),
          edges: edges
            .map(
              (e: {
                id: unknown;
                source_entity_id: unknown;
                target_entity_id: unknown;
                cluster_id: unknown;
              }) => {
                const { source_entity_id, target_entity_id, ...edge } = e;
                return {
                  ...edge,
                  id: Number(e.id),
                  cluster_id: Number(e.cluster_id),
                  hop: Math.min(
                    hops.get(Number(source_entity_id)) ?? 0,
                    hops.get(Number(target_entity_id)) ?? 0,
                  ),
                };
              },
            )
            .sort(
              (x: { hop: number; id: number }, y: { hop: number; id: number }) =>
                x.hop - y.hop || x.id - y.id,
            ),
        };
      });
      return { content: [{ type: "text", text: JSON.stringify(result) }], details: result };
    },
  };
}
