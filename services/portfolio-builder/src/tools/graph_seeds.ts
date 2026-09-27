import { SQL } from "bun";
import { normalizeCompanyName } from "./normalize_company_name.ts";

const likeEscape = (text: string) => text.replace(/[\\%_]/g, "\\$&");

export async function findSeedEntities(sql: SQL, name: string): Promise<number[]> {
  const normalized = normalizeCompanyName(name);
  if (!normalized) throw new Error("name must not be empty");
  const rows =
    await sql`SELECT e.id FROM entities e WHERE e.name LIKE ${`%${likeEscape(normalized)}%`}
    UNION
    SELECT e.id FROM company_aliases a JOIN entities e ON e.corp_code = a.corp_code
    WHERE a.alias = ${normalized}
    ORDER BY id`;
  if (rows.length) return rows.map((r: { id: unknown }) => Number(r.id));
  const candidates = await sql`SELECT DISTINCT raw_name FROM entities
    WHERE name LIKE ${`%${likeEscape(normalized.slice(0, 2))}%`} ORDER BY raw_name LIMIT 5`;
  const names = candidates.map((c: { raw_name: string }) => c.raw_name);
  throw new Error(
    `no entity matches "${name}". Candidates: ${names.length ? names.join(", ") : "none"}`,
  );
}

// ponytail: fixed 10 s cap, not a setting (spec §5.4); a hub entity at high depth is the only slow case.
export async function withGraphTimeout<T>(sql: SQL, run: (tx: SQL) => Promise<T>): Promise<T> {
  try {
    return await sql.begin(async (tx) => {
      await tx`SET LOCAL statement_timeout = '10s'`;
      return run(tx as unknown as SQL);
    });
  } catch (error) {
    if (error instanceof SQL.PostgresError && (error.errno ?? error.code) === "57014") {
      throw new Error("graph query timed out; use a smaller depth or a more specific name");
    }
    throw error;
  }
}
