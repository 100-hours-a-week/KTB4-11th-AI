import { expect, test } from "bun:test";
import { loadSettings } from "../src/settings.ts";

const REQUIRED = {
  PORTFOLIO_BUILDER_POSTGRES_DSN: "postgres://ktb:ktb@localhost:5432/news",
  PORTFOLIO_BUILDER_MARKET_MCP_URL: "http://localhost:8000/mcp",
  PORTFOLIO_BUILDER_LLM_MODEL: "gpt-5.5",
};

test("applies defaults when only the required variables are set", () => {
  const settings = loadSettings(REQUIRED);

  expect(settings).toEqual({
    postgresDsn: REQUIRED.PORTFOLIO_BUILDER_POSTGRES_DSN,
    marketMcpUrl: REQUIRED.PORTFOLIO_BUILDER_MARKET_MCP_URL,
    llmModel: "gpt-5.5",
    thinkingLevel: "medium",
    openaiAccessToken: undefined,
    openaiRefreshToken: undefined,
    openaiTokenExpiresEpoch: undefined,
    credentialsPath: "/data/auth.json",
    newsWindowDays: 7,
    maxTurns: 150,
    logLevel: "INFO",
  });
});

test("reads every optional variable", () => {
  const settings = loadSettings({
    ...REQUIRED,
    PORTFOLIO_BUILDER_THINKING_LEVEL: "high",
    PORTFOLIO_BUILDER_OPENAI_ACCESS_TOKEN: "access",
    PORTFOLIO_BUILDER_OPENAI_REFRESH_TOKEN: "refresh",
    PORTFOLIO_BUILDER_OPENAI_TOKEN_EXPIRES_EPOCH: "1790000000000",
    PORTFOLIO_BUILDER_CREDENTIALS_PATH: "/tmp/auth.json",
    PORTFOLIO_BUILDER_NEWS_WINDOW_DAYS: "3",
    PORTFOLIO_BUILDER_MAX_TURNS: "20",
    PORTFOLIO_BUILDER_LOG_LEVEL: "DEBUG",
  });

  expect(settings).toMatchObject({
    thinkingLevel: "high",
    openaiAccessToken: "access",
    openaiRefreshToken: "refresh",
    openaiTokenExpiresEpoch: 1790000000000,
    credentialsPath: "/tmp/auth.json",
    newsWindowDays: 3,
    maxTurns: 20,
    logLevel: "DEBUG",
  });
});

test("names every missing required variable", () => {
  expect(() => loadSettings({})).toThrow(
    "PORTFOLIO_BUILDER_POSTGRES_DSN, PORTFOLIO_BUILDER_MARKET_MCP_URL, PORTFOLIO_BUILDER_LLM_MODEL",
  );
});

test.each([
  ["PORTFOLIO_BUILDER_MAX_TURNS", "0"],
  ["PORTFOLIO_BUILDER_NEWS_WINDOW_DAYS", "seven"],
  ["PORTFOLIO_BUILDER_OPENAI_TOKEN_EXPIRES_EPOCH", "soon"],
  ["PORTFOLIO_BUILDER_LOG_LEVEL", "LOUD"],
  ["PORTFOLIO_BUILDER_THINKING_LEVEL", "extreme"],
])("rejects an invalid %s", (name, value) => {
  expect(() => loadSettings({ ...REQUIRED, [name]: value })).toThrow(name);
});
