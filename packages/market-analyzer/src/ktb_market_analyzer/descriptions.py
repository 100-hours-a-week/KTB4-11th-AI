"""One-line, LLM-readable descriptions for every indicator output field."""

DESCRIPTIONS: dict[str, str] = {
    "rsi": (
        "Relative Strength Index (0-100): momentum oscillator; above 70 is typically "
        "overbought, below 30 is typically oversold."
    ),
    "macd": (
        "MACD line: difference between the 12- and 26-period exponential moving averages of price."
    ),
    "macd_signal": (
        "MACD signal line: a 9-period EMA of the MACD line, used to spot "
        "bullish/bearish crossovers."
    ),
    "macd_histogram": (
        "MACD histogram: MACD line minus its signal line; shows momentum strength and direction."
    ),
    "stochastic_k": (
        "Stochastic %K (0-100): closing price relative to its recent high-low range; "
        ">80 overbought, <20 oversold."
    ),
    "stochastic_d": (
        "Stochastic %D (0-100): a smoothed moving average of %K, used to spot crossovers with %K."
    ),
    "roc": (
        "Rate of Change: percent price change over the lookback period; positive is "
        "upward momentum, negative is downward."
    ),
    "williams_r": (
        "Williams %R (-100 to 0): momentum oscillator; above -20 is typically "
        "overbought, below -80 is typically oversold."
    ),
}
