from opendartreader.dart_list import corp_codes

from news_graph_builder.company.dto import DartCompany


def fetch_corp_codes(api_key: str) -> list[DartCompany]:
    try:
        frame = corp_codes(api_key)
    except Exception as error:
        # requests puts the request URL, which carries the key, into its error messages.
        # DART status errors are a ValueError holding a {'status', 'message'} dict.
        detail = (
            error.args[0]
            if isinstance(error, ValueError) and error.args and isinstance(error.args[0], dict)
            else type(error).__name__
        )
        raise RuntimeError(f"DART corp_codes failed: {detail}") from None
    return [
        DartCompany(
            row.corp_code,
            row.corp_name,
            row.corp_eng_name.strip() or None,
            row.stock_code.strip(),
        )
        # pandas 3 stores a missing string as NaN, which is truthy and has no strip().
        for row in frame.fillna("").itertuples()
        if row.stock_code.strip()
    ]
