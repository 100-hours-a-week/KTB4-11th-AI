import type { AgentTool } from "@earendil-works/pi-agent-core";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import type { SQL } from "bun";
import { Type } from "typebox";
import { normalizeCompanyName } from "./normalize_company_name.ts";

export type TechnicalsClient = Pick<Client, "callTool">;

const Params = Type.Object({ name: Type.String({ description: "company name or company_id" }) });

// Connect on first use so a run whose agent never asks for technicals does not need the server.
export function connectMarketMcp(url: string): {
  get: () => Promise<TechnicalsClient>;
  close: () => Promise<void>;
} {
  let pending: Promise<TechnicalsClient> | undefined;
  let connected: Client | undefined;
  const get = () => {
    pending ??= (async () => {
      const client = new Client({ name: "portfolio-builder", version: "0.1.0" });
      await client.connect(new StreamableHTTPClientTransport(new URL(url)));
      connected = client;
      return client;
    })().catch((error) => {
      pending = undefined;
      throw error;
    });
    return pending;
  };
  // ponytail: nothing to close if get() was never called or never succeeded.
  const close = async () => {
    if (connected) await connected.close();
  };
  return { get, close };
}

export function analyzeTechnicalsTool(
  sql: SQL,
  getClient: () => Promise<TechnicalsClient>,
): AgentTool<typeof Params> {
  return {
    name: "analyze_technicals",
    label: "Analyze technicals",
    description:
      "Technical indicators and their verdicts for one listed company, looked up by company name (or company_id).",
    parameters: Params,
    execute: async (_toolCallId, { name }) => {
      const [company] = await sql`SELECT corp_code, corp_name, stock_code FROM (
          SELECT c.*, 0 AS priority FROM companies c WHERE c.corp_code = ${name.trim()}
          UNION ALL
          SELECT c.*, 1 FROM company_aliases a JOIN companies c ON c.corp_code = a.corp_code
          WHERE a.alias = ${normalizeCompanyName(name)}
        ) AS matches ORDER BY priority LIMIT 1`;
      if (!company) {
        const candidates = await sql`SELECT corp_name FROM companies
          WHERE corp_name ILIKE ${`%${name.trim()}%`} ORDER BY corp_name LIMIT 5`;
        const names = candidates.map((c: { corp_name: string }) => c.corp_name);
        throw new Error(
          `no company matches "${name}". Candidates: ${names.length ? names.join(", ") : "none"}`,
        );
      }

      let client: TechnicalsClient;
      try {
        client = await getClient();
      } catch (error) {
        throw new Error(
          `market-analyzer-mcp is unavailable: ${error instanceof Error ? error.message : error}`,
        );
      }
      const response = await client.callTool({
        name: "analyze_technicals",
        arguments: { stock_code: company.stock_code },
      });
      const text = (response.content as { type: string; text?: string }[])
        .filter((c) => c.type === "text")
        .map((c) => c.text)
        .join("\n");
      if (response.isError) throw new Error(text || "analyze_technicals failed");

      return {
        content: [
          {
            type: "text",
            text: `${company.corp_name} (company_id ${company.corp_code}, stock_code ${company.stock_code})\n${text}`,
          },
        ],
        details: { company_id: company.corp_code, stock_code: company.stock_code },
      };
    },
  };
}
