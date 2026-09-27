import {
  Agent,
  type AgentTool,
  type StreamFn,
  type ThinkingLevel,
} from "@earendil-works/pi-agent-core";
import type { Api, AssistantMessage, Model } from "@earendil-works/pi-ai";
import type { Log } from "./log.ts";

export type UsageTotals = {
  input: number;
  output: number;
  cache_read: number;
  cache_write: number;
  cost: number;
};
export type RunResult = {
  outcome: "saved" | "max_turns" | "error";
  portfolioId: number | null;
  turns: number;
  usage: UsageTotals;
  error?: string;
};

const NUDGE =
  "You stopped without a saved portfolio. Keep investigating with the tools if you need to, then call submit_portfolio.";

// Each request resends the whole transcript; the transcript is logged once per message instead.
function summarizePayload(payload: unknown): Record<string, unknown> {
  if (typeof payload !== "object" || payload === null) return {};
  const { input, messages, instructions, tools, ...rest } = payload as Record<string, unknown>;
  const history = (input ?? messages) as unknown[] | undefined;
  return {
    ...rest,
    tools: Array.isArray(tools)
      ? tools.map(
          (t: { name?: string; function?: { name?: string } }) => t?.name ?? t?.function?.name,
        )
      : undefined,
    message_count: Array.isArray(history) ? history.length : undefined,
  };
}

export async function runAgent(options: {
  model: Model<Api>;
  streamFn: StreamFn;
  thinkingLevel: ThinkingLevel;
  systemPrompt: string;
  briefing: string;
  tools: AgentTool<any>[];
  maxTurns: number;
  log: Log;
}): Promise<RunResult> {
  const { log, maxTurns } = options;
  const usage: UsageTotals = { input: 0, output: 0, cache_read: 0, cache_write: 0, cost: 0 };
  const toolStarts = new Map<string, { at: number; args: unknown }>();
  let turns = 0;
  let turnStartedAt = 0;
  let portfolioId: number | null = null;
  let lastAssistant: AssistantMessage | undefined;

  const agent: Agent = new Agent({
    initialState: {
      systemPrompt: options.systemPrompt,
      model: options.model,
      thinkingLevel: options.thinkingLevel,
      tools: options.tools,
    },
    streamFn: options.streamFn,
    onPayload: (payload) => {
      log("llm_request", { turn: turns + 1, ...summarizePayload(payload) });
      return undefined;
    },
    finishTurn: ({ message, toolResults }) => {
      turns += 1;
      const saved = toolResults.find((r) => r.toolName === "submit_portfolio" && !r.isError);
      if (saved) portfolioId = Number((saved.details as { portfolio_id: number }).portfolio_id);
      if (portfolioId !== null || turns >= maxTurns) return { action: "end" };
      if (message.stopReason === "stop" && toolResults.length === 0) {
        agent.followUp({ role: "user", content: NUDGE, timestamp: Date.now() });
      }
      return undefined;
    },
  });

  agent.subscribe((event) => {
    if (event.type === "turn_start") turnStartedAt = performance.now();
    if (event.type === "message_end" && event.message.role === "assistant") {
      const message = event.message as AssistantMessage;
      lastAssistant = message;
      usage.input += message.usage.input;
      usage.output += message.usage.output;
      usage.cache_read += message.usage.cacheRead;
      usage.cache_write += message.usage.cacheWrite;
      usage.cost += message.usage.cost.total;
      log("llm_response", {
        turn: turns + 1,
        model: message.responseModel ?? message.model,
        stop_reason: message.stopReason,
        error: message.errorMessage,
        text: message.content.flatMap((c) => (c.type === "text" ? [c.text] : [])).join("\n"),
        reasoning: message.content
          .flatMap((c) => (c.type === "thinking" ? [c.thinking] : []))
          .join("\n"),
        tool_calls: message.content.flatMap((c) =>
          c.type === "toolCall" ? [{ name: c.name, arguments: c.arguments }] : [],
        ),
        latency_ms: Math.round(performance.now() - turnStartedAt),
        usage: {
          input: message.usage.input,
          output: message.usage.output,
          cache_read: message.usage.cacheRead,
          cache_write: message.usage.cacheWrite,
          reasoning: message.usage.reasoning,
          total: message.usage.totalTokens,
          cost: message.usage.cost.total,
        },
      });
    }
    if (event.type === "tool_execution_start") {
      toolStarts.set(event.toolCallId, { at: performance.now(), args: event.args });
    }
    if (event.type === "tool_execution_end") {
      const result = event.result as {
        content?: { type: string; text?: string }[];
        details?: unknown;
      };
      const start = toolStarts.get(event.toolCallId);
      log(
        "tool_call",
        {
          turn: turns + 1,
          name: event.toolName,
          args: start?.args,
          result: result.content?.map((c) => c.text ?? "").join("\n"),
          details: result.details,
          is_error: event.isError,
          duration_ms: start ? Math.round(performance.now() - start.at) : null,
        },
        event.isError ? "WARNING" : "INFO",
      );
    }
  });

  await agent.prompt(options.briefing);

  if (portfolioId !== null) return { outcome: "saved", portfolioId, turns, usage };
  if (turns >= maxTurns) return { outcome: "max_turns", portfolioId, turns, usage };
  return {
    outcome: "error",
    portfolioId,
    turns,
    usage,
    error:
      lastAssistant?.errorMessage ??
      agent.state.errorMessage ??
      "agent stopped without a portfolio",
  };
}
