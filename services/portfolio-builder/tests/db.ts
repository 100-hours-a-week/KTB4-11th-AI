import { SQL } from "bun";

const dsn = process.env.KTB_TEST_POSTGRES_DSN?.replace("+psycopg", "");

export const hasDb = Boolean(dsn);
export const SAMSUNG = "00126380";
export const HYNIX = "00164779";
export const LGES = "01515323";

let shared: SQL | undefined;

export function testSql(): SQL {
  if (!dsn) throw new Error("KTB_TEST_POSTGRES_DSN is not set");
  shared ??= new SQL(dsn);
  return shared;
}

export async function resetTables(sql: SQL): Promise<void> {
  await sql`TRUNCATE portfolios, portfolio_holdings, portfolio_exits, relations,
    cluster_entities, entities, cluster_summaries, article_clusters, clusters, articles,
    theme_companies, themes, company_aliases, companies RESTART IDENTITY CASCADE`;
}

export async function seedFixture(sql: SQL): Promise<void> {
  await sql.begin(async (tx) => {
    await resetTables(tx);
    await tx`INSERT INTO companies (corp_code, stock_code, corp_name) VALUES
      (${SAMSUNG}, '005930', '삼성전자'), (${HYNIX}, '000660', 'SK하이닉스'),
      (${LGES}, '373220', 'LG에너지솔루션')`;
    await tx`INSERT INTO company_aliases (alias, corp_code) VALUES
      ('삼성전자', ${SAMSUNG}), ('sk하이닉스', ${HYNIX}), ('lg에너지솔루션', ${LGES})`;
    await tx`INSERT INTO clusters (id, updated_at) OVERRIDING SYSTEM VALUE VALUES
      (1, now()), (2, now() - interval '30 days')`;
    await tx`INSERT INTO cluster_summaries (cluster_id, title, summary, cluster_updated_at) VALUES
      (1, '삼성전자 HBM 공급 확대', '삼성전자가 엔비디아에 HBM을 공급한다.', now()),
      (2, '반도체 수출 둔화', 'SK하이닉스가 HBM 생산을 늘린다.', now() - interval '30 days')`;
    await tx`INSERT INTO articles (id, source, external_id, url, title, body, published_at, raw_payload)
      OVERRIDING SYSTEM VALUE VALUES
      (1, 'yonhap', 'a1', 'https://example.com/1', '삼성 HBM 공급', '본문', now(), '{}'),
      (2, 'yonhap', 'a2', 'https://example.com/2', '엔비디아 HBM 조달', '본문', now(), '{}'),
      (3, 'yonhap', 'a3', 'https://example.com/3', '하이닉스 증산', '본문', now() - interval '30 days', '{}')`;
    await tx`INSERT INTO article_clusters (article_id, cluster_id) VALUES (1, 1), (2, 1), (3, 2)`;
    await tx`INSERT INTO entities (id, raw_name, name, type, corp_code) OVERRIDING SYSTEM VALUE VALUES
      (1, '삼성전자', '삼성전자', 'company', ${SAMSUNG}),
      (2, '엔비디아', '엔비디아', 'company', NULL),
      (3, 'SK하이닉스', 'sk하이닉스', 'company', ${HYNIX}),
      (4, 'HBM', 'hbm', 'product', NULL)`;
    await tx`INSERT INTO cluster_entities (cluster_id, entity_id) VALUES (1, 1), (1, 2), (1, 4), (2, 3), (2, 4)`;
    await tx`INSERT INTO relations (id, cluster_id, source_entity_id, target_entity_id, type, description)
      OVERRIDING SYSTEM VALUE VALUES
      (1, 1, 1, 2, 'supplies', '삼성전자가 엔비디아에 HBM을 공급'),
      (2, 1, 4, 2, 'used_by', 'HBM은 엔비디아 GPU에 쓰인다'),
      (3, 2, 3, 4, 'produces', 'SK하이닉스가 HBM을 생산')`;
    await tx`INSERT INTO themes (theme_code, name) VALUES ('T1', 'HBM')`;
    await tx`INSERT INTO theme_companies (theme_code, corp_code, is_main) VALUES
      ('T1', ${SAMSUNG}, true), ('T1', ${HYNIX}, false)`;
  });
}
