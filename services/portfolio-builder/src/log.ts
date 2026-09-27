const LEVELS = { DEBUG: 10, INFO: 20, WARNING: 30, ERROR: 40 } as const;

// Tolerates quotes escaped by an enclosing JSON.stringify (e.g. a token response
// dumped into an error message string) as well as unescaped top-level fields.
const TOKEN_FIELD = /(\\?)"(access|refresh|id)_token(\\?)"\s*:\s*(\\?)"[^"\\]*(\\?)"/g;
const JWT_SHAPED = /eyJ[\w-]+\.[\w-]+\.[\w-]+/g;

function redact(line: string): string {
  return line
    .replace(
      TOKEN_FIELD,
      (_match, b1: string, name: string, b2: string, b3: string, b4: string) =>
        `${b1}"${name}_token${b2}":${b3}"[redacted]${b4}"`,
    )
    .replace(JWT_SHAPED, "[redacted-jwt]");
}

export type LogLevel = keyof typeof LEVELS;
export type Log = (event: string, fields?: Record<string, unknown>, level?: LogLevel) => void;

export function isLogLevel(value: string): value is LogLevel {
  return Object.hasOwn(LEVELS, value);
}

export function createLog(
  runId: string,
  minLevel: LogLevel,
  write: (line: string) => void = (line) => process.stdout.write(`${line}\n`),
): Log {
  return (event, fields = {}, level = "INFO") => {
    if (LEVELS[level] < LEVELS[minLevel]) return;
    const line = JSON.stringify({
      ts: new Date().toISOString(),
      level,
      run_id: runId,
      event,
      ...fields,
    });
    write(redact(line));
  };
}
