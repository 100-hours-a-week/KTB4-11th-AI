import time
from typing import Any

import httpx

from news_graph_builder.kiwoom.settings import KiwoomSettings


def fetch_token(client: httpx.Client, *, settings: KiwoomSettings | None = None) -> str:
    settings = settings or KiwoomSettings()
    body = {
        "grant_type": "client_credentials",
        "appkey": settings.kiwoom_app_key.get_secret_value(),
        "secretkey": settings.kiwoom_secret_key.get_secret_value(),
    }
    response = client.post(
        f"{settings.kiwoom_base_uri.rstrip('/')}/oauth2/token",
        json=body,
        headers={"content-type": "application/json;charset=UTF-8"},
    )
    reply = response.raise_for_status().json()
    if "token" not in reply:
        raise RuntimeError(
            f"Kiwoom token refused: {reply.get('return_code')} {reply.get('return_msg')}"
        )
    return reply["token"]


def fetch_pages(
    client: httpx.Client,
    *,
    token: str,
    api_id: str,
    path: str,
    body: dict[str, str],
    list_key: str,
    settings: KiwoomSettings | None = None,
) -> list[dict[str, Any]]:
    settings = settings or KiwoomSettings()
    url = f"{settings.kiwoom_base_uri.rstrip('/')}/api/dostk/{path}"
    headers = {
        "content-type": "application/json;charset=UTF-8",
        "authorization": f"Bearer {token}",
        "api-id": api_id,
        "cont-yn": "N",
        "next-key": "",
    }
    rows: list[dict[str, Any]] = []
    while True:
        time.sleep(settings.kiwoom_request_interval)
        response = client.post(url, json=body, headers=headers).raise_for_status()
        page = response.json()
        if page.get("return_code") != 0:
            raise RuntimeError(
                f"Kiwoom {api_id} failed: {page.get('return_code')} {page.get('return_msg')}"
            )
        rows += page.get(list_key) or []
        if response.headers.get("cont-yn") != "Y":
            break
        headers |= {"cont-yn": "Y", "next-key": response.headers["next-key"]}
    return rows
