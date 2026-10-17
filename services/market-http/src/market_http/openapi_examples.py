PRICE_EXAMPLE = {
    "code": "005930",
    "price": 82100,
    "change": -400,
    "rate": -0.48,
    "volume": 500,
    "timestamp": "2026-10-15T09:01:23+09:00",
}

ORDERBOOK_EXAMPLE = {
    "code": "005930",
    "asks": [
        {"price": 82100, "qty": 1000},
        {"price": 82200, "qty": 1010},
        {"price": 82300, "qty": 1020},
        {"price": 82400, "qty": 1030},
        {"price": 82500, "qty": 1040},
        {"price": 82600, "qty": 1050},
        {"price": 82700, "qty": 1060},
        {"price": 82800, "qty": 1070},
        {"price": 82900, "qty": 1080},
        {"price": 83000, "qty": 1090},
    ],
    "bids": [
        {"price": 82000, "qty": 1200},
        {"price": 81900, "qty": 1210},
        {"price": 81800, "qty": 1220},
        {"price": 81700, "qty": 1230},
        {"price": 81600, "qty": 1240},
        {"price": 81500, "qty": 1250},
        {"price": 81400, "qty": 1260},
        {"price": 81300, "qty": 1270},
        {"price": 81200, "qty": 1280},
        {"price": 81100, "qty": 1290},
    ],
    "timestamp": "2026-10-15T09:01:00+09:00",
}

PAGE_EXAMPLE = {
    "items": [
        {
            "timestamp": "2026-10-15T09:01:00+09:00",
            "open": 82000.0,
            "high": 82300.0,
            "low": 81900.0,
            "close": 82100.0,
            "volume": 15000,
        }
    ],
    "next_cursor": "MjAyNi0xMC0xNVQwOTowMTowMCswOTowMA==",
}

INDEX_EXAMPLE = {
    "name": "KOSPI",
    "value": 2750.12,
    "change": -12.34,
    "rate": -0.45,
    "timestamp": "2026-10-15T09:01:00+09:00",
}

RANKING_EXAMPLE = {
    "as_of": "2026-10-15",
    "updated_at": "2026-10-15T09:01:00+09:00",
    "items": [{"rank": 1, "code": "005930", "score": 150000.0}],
}

HEALTH_EXAMPLE = {"status": "ok"}
