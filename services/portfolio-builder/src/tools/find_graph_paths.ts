import type { AgentTool } from "@earendil-works/pi-agent-core";
import type { SQL } from "bun";
import { Type } from "typebox";
import { pgArray } from "../pg_array.ts";
import { findSeedEntities, withGraphTimeout } from "./graph_seeds.ts";

const Params = Type.Object({
  from_name: Type.String(),
  to_name: Type.String(),
  max_depth: Type.Optional(Type.Integer({ minimum: 1, maximum: 6, default: 4 })),
});

export function findGraphPathsTool(sql: SQL): AgentTool<typeof Params> {
  return {
    name: "find_graph_paths",
    label: "Find graph paths",
    description:
      "Every simple path of at most max_depth relations between two entities in the knowledge graph, following relations in either direction, shortest first. Use it to see how an event or company reaches another company.",
    parameters: Params,
    execute: async (_toolCallId, { from_name, to_name, max_depth = 4 }) => {
      const from = await findSeedEntities(sql, from_name);
      const to = await findSeedEntities(sql, to_name);
      const result = await withGraphTimeout(sql, async (tx) => {
        const paths = await tx`WITH RECURSIVE edges AS (
            SELECT id AS relation_id, source_entity_id AS a, target_entity_id AS b, 'forward'::text AS direction FROM relations
            UNION ALL
            SELECT id, target_entity_id, source_entity_id, 'backward'::text FROM relations
          ), paths(node, nodes, relation_ids, directions) AS (
            SELECT id, ARRAY[id], ARRAY[]::bigint[], ARRAY[]::text[]
            FROM unnest(${pgArray(from)}::bigint[]) AS s(id)
            UNION ALL
            SELECT e.b, p.nodes || e.b, p.relation_ids || e.relation_id, p.directions || e.direction
            FROM paths p JOIN edges e ON e.a = p.node
            WHERE cardinality(p.relation_ids) < ${max_depth}
              AND e.b <> ALL(p.nodes)
              AND p.node <> ALL(${pgArray(to)}::bigint[])
          )
          SELECT nodes, relation_ids, directions FROM paths
          WHERE node = ANY(${pgArray(to)}::bigint[]) AND cardinality(relation_ids) > 0
          ORDER BY cardinality(relation_ids), nodes`;
        const nodeIds = [
          ...new Set(paths.flatMap((p: { nodes: unknown[] }) => p.nodes.map((n) => Number(n)))),
        ];
        const relationIds = [
          ...new Set(
            paths.flatMap((p: { relation_ids: unknown[] }) => p.relation_ids.map((r) => Number(r))),
          ),
        ];
        const names = new Map<number, string>(
          (
            await tx`SELECT id, raw_name FROM entities WHERE id = ANY(${pgArray(nodeIds as number[])}::bigint[])`
          ).map((e: { id: unknown; raw_name: string }) => [Number(e.id), e.raw_name]),
        );
        const relations = new Map<
          number,
          { type: string; description: string; cluster_id: unknown }
        >(
          (
            await tx`SELECT id, type, description, cluster_id FROM relations WHERE id = ANY(${pgArray(relationIds as number[])}::bigint[])`
          ).map((r: { id: unknown; type: string; description: string; cluster_id: unknown }) => [
            Number(r.id),
            r,
          ]),
        );
        return {
          paths: paths.map(
            (p: { nodes: unknown[]; relation_ids: unknown[]; directions: string[] }) => ({
              length: p.relation_ids.length,
              steps: p.relation_ids.map((rid, i) => {
                const relation = relations.get(Number(rid));
                return {
                  from: names.get(Number(p.nodes[i])),
                  to: names.get(Number(p.nodes[i + 1])),
                  type: relation?.type,
                  direction: p.directions[i],
                  description: relation?.description,
                  cluster_id: Number(relation?.cluster_id),
                };
              }),
            }),
          ),
        };
      });
      return { content: [{ type: "text", text: JSON.stringify(result) }], details: result };
    },
  };
}
