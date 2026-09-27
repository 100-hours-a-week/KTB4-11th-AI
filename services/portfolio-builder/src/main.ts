import { SQL } from "bun";
import { runAgent } from "./agent.ts";
import { createCodexModels } from "./credentials.ts";
import { createLog } from "./log.ts";
import { loadBriefing, SYSTEM_PROMPT } from "./portfolio/briefing.ts";
import { loadSettings } from "./settings.ts";
import { analyzeTechnicalsTool, connectMarketMcp } from "./tools/analyze_technicals.ts";
import { findGraphPathsTool } from "./tools/find_graph_paths.ts";
import { getNewsClusterTool } from "./tools/get_news_cluster.ts";
import { searchGraphTool } from "./tools/search_graph.ts";
import { searchNewsClusterTool } from "./tools/search_news_cluster.ts";
import { submitPortfolioTool } from "./tools/submit_portfolio.ts";

export async function main(): Promise<number> {
  const settings = loadSettings();
  const log = createLog(crypto.randomUUID(), settings.logLevel);
  const startedAt = performance.now();
  const sql = new SQL(settings.postgresDsn);
  // Streamable HTTP keeps the process alive once connected, so track it to close on exit.
  const market = connectMarketMcp(settings.marketMcpUrl);
  try {
    const models = await createCodexModels(settings);
    const model = models.getModel("openai-codex", settings.llmModel);
    if (!model) throw new Error(`unknown openai-codex model ${settings.llmModel}`);
    log("run_start", {
      provider: model.provider,
      model: model.id,
      reasoning_level: settings.thinkingLevel,
      temperature: null,
      max_turns: settings.maxTurns,
      news_window_days: settings.newsWindowDays,
    });

    const briefing = await loadBriefing(sql, settings.newsWindowDays);
    log("ingestion", {
      previous_portfolio_id: briefing.previousPortfolioId,
      previous_holdings: briefing.previousHoldings,
      previous_exits: briefing.previousExits,
      cluster_ids: briefing.clusterIds,
      cluster_count: briefing.clusterIds.length,
      company_count: briefing.companyCount,
      theme_count: briefing.themeCount,
      briefing_chars: briefing.text.length,
    });
    log("prompt", { system_prompt: SYSTEM_PROMPT, briefing: briefing.text });

    const tools = [
      analyzeTechnicalsTool(sql, market.get),
      getNewsClusterTool(sql),
      searchNewsClusterTool(sql),
      searchGraphTool(sql),
      findGraphPathsTool(sql),
      submitPortfolioTool({
        sql,
        previous: briefing.previousCompanyIds,
        model: `${model.provider}/${model.id}`,
        log,
      }),
    ];
    const result = await runAgent({
      model,
      streamFn: models.streamSimple.bind(models),
      thinkingLevel: settings.thinkingLevel,
      systemPrompt: SYSTEM_PROMPT,
      briefing: briefing.text,
      tools,
      maxTurns: settings.maxTurns,
      log,
    });
    log(
      "run_end",
      {
        outcome: result.outcome,
        portfolio_id: result.portfolioId,
        turns: result.turns,
        usage: result.usage,
        error: result.error,
        elapsed_ms: Math.round(performance.now() - startedAt),
      },
      result.outcome === "saved" ? "INFO" : "ERROR",
    );
    return result.outcome === "saved" ? 0 : 1;
  } catch (error) {
    log(
      "run_end",
      {
        outcome: "error",
        error: error instanceof Error ? error.message : String(error),
        elapsed_ms: Math.round(performance.now() - startedAt),
      },
      "ERROR",
    );
    return 1;
  } finally {
    await market.close();
    await sql.close();
  }
}

if (import.meta.main) process.exitCode = await main();
