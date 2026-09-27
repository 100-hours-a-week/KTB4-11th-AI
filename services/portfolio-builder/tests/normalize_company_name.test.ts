import { expect, test } from "bun:test";
import { normalizeCompanyName } from "../src/tools/normalize_company_name.ts";
import cases from "./fixtures/normalize_cases.json";

test.each(cases as [string, string][])("normalizes %p to %p", (raw, expected) => {
  expect(normalizeCompanyName(raw)).toBe(expected);
});
