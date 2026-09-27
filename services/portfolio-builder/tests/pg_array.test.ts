import { expect, test } from "bun:test";
import { pgArray } from "../src/pg_array.ts";

test("quotes every element and escapes quotes and backslashes", () => {
  expect(pgArray([1, 2])).toBe('{"1","2"}');
  expect(pgArray(['a"b', "c\\d"])).toBe('{"a\\"b","c\\\\d"}');
  expect(pgArray([])).toBe("{}");
});
