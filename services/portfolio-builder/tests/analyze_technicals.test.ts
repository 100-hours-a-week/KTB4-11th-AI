import { beforeAll, describe, expect, test } from "bun:test";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { CallToolRequestSchema } from "@modelcontextprotocol/sdk/types.js";
import { analyzeTechnicalsTool, connectMarketMcp } from "../src/tools/analyze_technicals.ts";
import { hasDb, SAMSUNG, seedFixture, testSql } from "./db.ts";

test("connectMarketMcp's close is a no-op when nothing ever connected", async () => {
  await expect(connectMarketMcp("http://localhost:1/mcp").close()).resolves.toBeUndefined();
});

async function fakeMarketMcp() {
  const calls: unknown[] = [];
  const server = new Server({ name: "fake-market", version: "0" }, { capabilities: { tools: {} } });
  server.setRequestHandler(CallToolRequestSchema, async (request) => {
    calls.push({ name: request.params.name, arguments: request.params.arguments });
    if (request.params.arguments?.stock_code === "000660") {
      return { content: [{ type: "text", text: "no bars for 000660" }], isError: true };
    }
    return {
      content: [
        { type: "text", text: `RSI 71 overbought for ${request.params.arguments?.stock_code}` },
      ],
    };
  });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  await server.connect(serverTransport);
  const client = new Client({ name: "test", version: "0" });
  await client.connect(clientTransport);
  return { client, calls };
}

describe.skipIf(!hasDb)("analyze_technicals", () => {
  const sql = hasDb ? testSql() : (undefined as never);
  beforeAll(() => seedFixture(sql));

  test("resolves a name through company_aliases and calls MCP with the stock code", async () => {
    const { client, calls } = await fakeMarketMcp();

    const result = await analyzeTechnicalsTool(sql, async () => client).execute("c", {
      name: "㈜삼성 전자",
    });

    expect(calls).toEqual([{ name: "analyze_technicals", arguments: { stock_code: "005930" } }]);
    expect(result.details).toEqual({ company_id: SAMSUNG, stock_code: "005930" });
    const text = (result.content[0] as { text: string }).text;
    expect(text).toContain(`삼성전자 (company_id ${SAMSUNG}, stock_code 005930)`);
    expect(text).toContain("RSI 71 overbought for 005930");
  });

  test("accepts a company_id directly", async () => {
    const { client, calls } = await fakeMarketMcp();

    await analyzeTechnicalsTool(sql, async () => client).execute("c", { name: SAMSUNG });

    expect(calls).toEqual([{ name: "analyze_technicals", arguments: { stock_code: "005930" } }]);
  });

  test("an unknown name lists candidates", async () => {
    const { client } = await fakeMarketMcp();

    await expect(
      analyzeTechnicalsTool(sql, async () => client).execute("c", { name: "삼성" }),
    ).rejects.toThrow("삼성전자");
  });

  test("an MCP tool error is passed to the agent", async () => {
    const { client } = await fakeMarketMcp();

    await expect(
      analyzeTechnicalsTool(sql, async () => client).execute("c", { name: "SK하이닉스" }),
    ).rejects.toThrow("no bars for 000660");
  });

  test("an unreachable MCP server becomes a tool error", async () => {
    const tool = analyzeTechnicalsTool(sql, async () => {
      throw new Error("connect ECONNREFUSED");
    });

    await expect(tool.execute("c", { name: "삼성전자" })).rejects.toThrow(
      "market-analyzer-mcp is unavailable",
    );
  });
});
