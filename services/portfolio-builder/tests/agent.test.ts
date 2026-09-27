import { beforeEach, describe, expect, test } from "bun:test";
import {
  createModels,
  fauxAssistantMessage,
  fauxProvider,
  fauxText,
  fauxToolCall,
} from "@earendil-works/pi-ai";
import { runAgent } from "../src/agent.ts";
import { createLog } from "../src/log.ts";
import { submitPortfolioTool } from "../src/tools/submit_portfolio.ts";
import { hasDb, SAMSUNG, seedFixture, testSql } from "./db.ts";

function scriptedModel(...steps: Parameters<ReturnType<typeof fauxProvider>["setResponses"]>[0]) {
  const faux = fauxProvider();
  faux.setResponses(steps);
  const models = createModels();
  models.setProvider(faux.provider);
  return { model: faux.getModel(), streamFn: models.streamSimple.bind(models) };
}

const valid = {
  holdings: [{ company_id: SAMSUNG, weight: 3, reason: "HBM 공급 확대", cited_cluster_ids: [1] }],
  exits: [],
  cash_weight: 1,
  commentary: "HBM 수요에 집중",
};

describe.skipIf(!hasDb)("runAgent", () => {
  const sql = hasDb ? testSql() : (undefined as never);
  beforeEach(() => seedFixture(sql));

  function setup(
    maxTurns: number,
    ...steps: Parameters<ReturnType<typeof fauxProvider>["setResponses"]>[0]
  ) {
    const lines: string[] = [];
    const log = createLog("run-test", "DEBUG", (line) => lines.push(line));
    const { model, streamFn } = scriptedModel(...steps);
    const tools = [submitPortfolioTool({ sql, previous: new Set(), model: "faux", log })];
    const run = () =>
      runAgent({
        model,
        streamFn,
        thinkingLevel: "off",
        systemPrompt: "sys",
        briefing: "brief",
        tools,
        maxTurns,
        log,
      });
    return { run, events: () => lines.map((l) => JSON.parse(l)) };
  }

  test("an invalid submission is fed back, the corrected one is saved", async () => {
    const { run, events } = setup(
      10,
      fauxAssistantMessage(
        fauxToolCall("submit_portfolio", {
          ...valid,
          holdings: [{ ...valid.holdings[0], reason: "" }],
        }),
        { stopReason: "toolUse" },
      ),
      fauxAssistantMessage(fauxToolCall("submit_portfolio", valid), { stopReason: "toolUse" }),
    );

    const result = await run();

    expect(result.outcome).toBe("saved");
    expect(result.turns).toBe(2);
    const [row] = await sql`SELECT id, cash_weight FROM portfolios`;
    expect(Number(row.id)).toBe(result.portfolioId as number);
    expect(row.cash_weight).toBeCloseTo(0.25);
    const names = events().map((e) => e.event);
    expect(names).toContain("validation_failed");
    expect(names).toContain("llm_response");
    expect(names).toContain("tool_call");
  });

  test("an agent that stops talking is told to submit, and hits the turn limit", async () => {
    const { run } = setup(
      3,
      fauxAssistantMessage(fauxText("thinking about it"), { stopReason: "stop" }),
      fauxAssistantMessage(fauxText("still thinking"), { stopReason: "stop" }),
      fauxAssistantMessage(fauxText("almost"), { stopReason: "stop" }),
      fauxAssistantMessage(fauxText("never reached"), { stopReason: "stop" }),
    );

    const result = await run();

    expect(result).toMatchObject({ outcome: "max_turns", portfolioId: null, turns: 3 });
    const [{ count }] = await sql`SELECT count(*)::int AS count FROM portfolios`;
    expect(count).toBe(0);
  });

  test("an LLM error ends the run", async () => {
    const { run } = setup(
      10,
      fauxAssistantMessage([], { stopReason: "error", errorMessage: "upstream 500" }),
    );

    const result = await run();

    expect(result).toMatchObject({ outcome: "error", portfolioId: null, error: "upstream 500" });
  });
});
