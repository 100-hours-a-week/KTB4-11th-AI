// Bun.sql's sql.array() does not document empty arrays; a text literal cast in SQL always works.
export function pgArray(values: readonly (string | number)[]): string {
  const quoted = values.map(
    (v) => `"${String(v).replaceAll("\\", "\\\\").replaceAll('"', '\\"')}"`,
  );
  return `{${quoted.join(",")}}`;
}
