const LEVELS = { DEBUG: 10, INFO: 20, WARNING: 30, ERROR: 40 } as const;

export type LogLevel = keyof typeof LEVELS;
export type Log = (event: string, fields?: Record<string, unknown>, level?: LogLevel) => void;

export function isLogLevel(value: string): value is LogLevel {
  return value in LEVELS;
}

export function createLog(
  runId: string,
  minLevel: LogLevel,
  write: (line: string) => void = (line) => process.stdout.write(`${line}\n`),
): Log {
  return (event, fields = {}, level = "INFO") => {
    if (LEVELS[level] < LEVELS[minLevel]) return;
    write(JSON.stringify({ ts: new Date().toISOString(), level, run_id: runId, event, ...fields }));
  };
}
