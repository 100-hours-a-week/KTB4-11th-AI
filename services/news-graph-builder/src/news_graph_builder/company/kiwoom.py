import httpx

from news_graph_builder.kiwoom import KiwoomSettings, fetch_pages


def fetch_kospi(
    client: httpx.Client, *, token: str, settings: KiwoomSettings | None = None
) -> list[tuple[str, str]]:
    rows = fetch_pages(
        client,
        token=token,
        api_id="ka10099",
        path="stkinfo",
        body={"mrkt_tp": "0"},
        list_key="list",
        settings=settings,
    )
    return [(row["code"], row["name"]) for row in rows]
