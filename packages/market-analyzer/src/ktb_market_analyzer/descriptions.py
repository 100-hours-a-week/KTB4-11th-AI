"""What each indicator output field measures.

These say what is being counted, not what the number means. The verdict — whether
72.4 on the RSI is high — belongs to ``comments.py``. Keeping the two apart means
a reader is never handed a threshold and left to apply it.
"""

DESCRIPTIONS: dict[str, str] = {
    "rsi": (
        "Relative Strength Index, 0-100: compares the average size of recent gains "
        "with the average size of recent losses over the lookback window."
    ),
    "macd": (
        "MACD line: the 12-period exponential moving average of price minus the 26-period one."
    ),
    "macd_signal": (
        "MACD signal line: a 9-period exponential moving average of the MACD line, "
        "that is, a smoothed copy of it."
    ),
    "macd_histogram": (
        "MACD histogram: the MACD line minus its signal line, measuring how far MACD "
        "has moved from its own smoothed copy."
    ),
    "stochastic_k": (
        "Stochastic %K, 0-100: where the close sits inside the high-low range of the "
        "lookback window."
    ),
    "stochastic_d": (
        "Stochastic %D, 0-100: a 3-period moving average of %K, that is, a smoothed copy of it."
    ),
    "roc": (
        "Rate of Change: the percent difference between the current close and the close "
        "n periods earlier."
    ),
    "williams_r": (
        "Williams %R, -100 to 0: where the close sits inside the high-low range of the "
        "lookback window, measured downward from the high."
    ),
}
