import { expect, test } from "bun:test";
import { createLog } from "../src/log.ts";

test("writes one JSON object per event with run id, level and fields", () => {
  const lines: string[] = [];
  const log = createLog("run-1", "INFO", (line) => lines.push(line));

  log("ingestion", { clusters: 3 });

  expect(lines).toHaveLength(1);
  const entry = JSON.parse(lines[0] as string);
  expect(entry).toMatchObject({ level: "INFO", run_id: "run-1", event: "ingestion", clusters: 3 });
  expect(typeof entry.ts).toBe("string");
});

test("drops events below the minimum level", () => {
  const lines: string[] = [];
  const log = createLog("run-1", "WARNING", (line) => lines.push(line));

  log("llm_request", {}, "INFO");
  log("validation_failed", {}, "WARNING");

  expect(lines.map((line) => JSON.parse(line).event)).toEqual(["validation_failed"]);
});
