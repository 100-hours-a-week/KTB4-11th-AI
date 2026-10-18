# Daily stock snapshots

The 08:55 KST market-collector pass requests `ka10001` once for each KOSPI 200 symbol.
It stores the five raw fields `base_pric`, `upl_pric`, `lst_pric`, `eps`, and `bps` in
QuestDB `corporations`. The `ts` deduplication key is midnight UTC on the KST trade date;
`trade_date` is the KST date in `YYYY-MM-DD` form, and `fetched_at` is the actual UTC
request completion time. A rerun replaces the same symbol and trade date.

The three price fields are stored as positive whole KRW amounts. `eps` and `bps` are
stored as raw integers without scaling, including zero or negative values; empty or
fractional responses become NULL because their unit and precision are not yet established.
No PER, PBR, or other derived metric is stored or calculated. The official
[Kiwoom `ka10001` example](https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/main/examples/%EA%B5%AD%EB%82%B4%EC%A3%BC%EC%8B%9D/%EC%A2%85%EB%AA%A9%EC%A0%95%EB%B3%B4/get_domestic_stock_info.py)
identifies these response fields but does not establish pre-open trade-date freshness.
That freshness needs a live 08:55 KST response check before production use.
