import httpx


def fetch_kospi(
    client: httpx.Client, *, base_uri: str, app_key: str, secret_key: str
) -> list[tuple[str, str]]:
    base_uri = base_uri.rstrip("/")
    headers = {"content-type": "application/json;charset=UTF-8"}
    reply = (
        client.post(
            f"{base_uri}/oauth2/token",
            json={"grant_type": "client_credentials", "appkey": app_key, "secretkey": secret_key},
            headers=headers,
        )
        .raise_for_status()
        .json()
    )
    if "token" not in reply:
        raise RuntimeError(
            f"Kiwoom token refused: {reply.get('return_code')} {reply.get('return_msg')}"
        )

    headers |= {
        "authorization": f"Bearer {reply['token']}",
        "api-id": "ka10099",
        "cont-yn": "N",
        "next-key": "",
    }
    rows: list[tuple[str, str]] = []
    while True:
        response = client.post(
            f"{base_uri}/api/dostk/stkinfo", json={"mrkt_tp": "0"}, headers=headers
        ).raise_for_status()
        page = response.json()
        if page.get("return_code") != 0:
            raise RuntimeError(
                f"Kiwoom ka10099 failed: {page.get('return_code')} {page.get('return_msg')}"
            )
        rows += [(item["code"], item["name"]) for item in page["list"]]
        if response.headers.get("cont-yn") != "Y":
            break
        headers |= {"cont-yn": "Y", "next-key": response.headers["next-key"]}
    return rows
