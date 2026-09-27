import type { ThinkingLevel } from "@earendil-works/pi-agent-core";
import { isLogLevel, type LogLevel } from "./log.ts";

const PREFIX = "PORTFOLIO_BUILDER_";
const THINKING_LEVELS: readonly string[] = ["off", "minimal", "low", "medium", "high", "xhigh"];

export type Settings = {
  postgresDsn: string;
  marketMcpUrl: string;
  llmModel: string;
  thinkingLevel: ThinkingLevel;
  openaiAccessToken?: string;
  openaiRefreshToken?: string;
  openaiTokenExpiresEpoch?: number;
  credentialsPath: string;
  newsWindowDays: number;
  maxTurns: number;
  logLevel: LogLevel;
};

export function loadSettings(env: Record<string, string | undefined> = process.env): Settings {
  const errors: string[] = [];
  const get = (name: string) => env[PREFIX + name] || undefined;
  const required = (name: string) => {
    const value = get(name);
    if (value === undefined) errors.push(`${PREFIX}${name} is required`);
    return value ?? "";
  };
  const positiveInt = (name: string, fallback?: number) => {
    const raw = get(name);
    if (raw === undefined) return fallback;
    const value = Number(raw);
    if (!Number.isInteger(value) || value <= 0)
      errors.push(`${PREFIX}${name} must be a positive integer`);
    return value;
  };
  const oneOf = <T extends string>(name: string, allowed: (v: string) => v is T, fallback: T) => {
    const raw = get(name) ?? fallback;
    if (!allowed(raw)) errors.push(`${PREFIX}${name} has an invalid value ${JSON.stringify(raw)}`);
    return raw as T;
  };

  const postgresDsn = required("POSTGRES_DSN");
  const marketMcpUrl = required("MARKET_MCP_URL");
  const llmModel = required("LLM_MODEL");
  const missing = errors.splice(0);
  if (missing.length) {
    errors.push(`missing required settings: ${missing.map((m) => m.split(" ")[0]).join(", ")}`);
  }

  const settings: Settings = {
    postgresDsn,
    marketMcpUrl,
    llmModel,
    thinkingLevel: oneOf(
      "THINKING_LEVEL",
      (v): v is ThinkingLevel => THINKING_LEVELS.includes(v),
      "medium",
    ),
    openaiAccessToken: get("OPENAI_ACCESS_TOKEN"),
    openaiRefreshToken: get("OPENAI_REFRESH_TOKEN"),
    openaiTokenExpiresEpoch: positiveInt("OPENAI_TOKEN_EXPIRES_EPOCH"),
    credentialsPath: get("CREDENTIALS_PATH") ?? "/data/auth.json",
    newsWindowDays: positiveInt("NEWS_WINDOW_DAYS", 7) as number,
    maxTurns: positiveInt("MAX_TURNS", 150) as number,
    logLevel: oneOf("LOG_LEVEL", isLogLevel, "INFO"),
  };
  if (errors.length) throw new Error(errors.join("; "));
  return settings;
}
