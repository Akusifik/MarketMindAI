WEIGHTS = {
    "rsi": 1.0,
    "macd": 1.5,
    "bollinger": 1.0,
    "atr": 0.3,
    "adx": 0.5,
    "obv": 1.0,
    "vwap": 1.2,
    "rvol": 0.8,
}


def determine_market_status(score):

    if score >= 5:
        return "🟢 Сильный бычий рынок"

    elif score >= 2:
        return "🟢 Бычий рынок"

    elif score <= -5:
        return "🔴 Сильный медвежий рынок"

    elif score <= -2:
        return "🔴 Медвежий рынок"

    return "🟡 Нейтральный рынок"


def evaluate_market(result):

    score = 0
    summary = []

    buy_signals = 0
    sell_signals = 0
    neutral_signals = 0

    indicators = {
        "rsi": result.rsi_analysis,
        "macd": result.macd_analysis,
        "bollinger": result.bollinger_analysis,
        "atr": result.atr_analysis,
        "adx": result.adx_analysis,
        "obv": result.obv_analysis,
        "vwap": result.vwap_analysis,
        "rvol": result.rvol_analysis,
    }

    def calculate_confidence(score, buy_signals, sell_signals):

        confidence = 50

        confidence += abs(score) * 5

        confidence += abs(buy_signals - sell_signals) * 5

        confidence = max(0, min(100, confidence))

        return round(confidence)

    for name, indicator in indicators.items():

        weight = WEIGHTS.get(name, 1.0)

        score += indicator.get("score", 0) * weight

        signal = indicator.get("signal")

        if signal == "BUY":
            buy_signals += 1

        elif signal == "SELL":
            sell_signals += 1

        else:
            neutral_signals += 1

        if indicator.get("message"):
            summary.append(indicator["message"])

    if buy_signals >= 3 and buy_signals > sell_signals:
        score += 2
        summary.append("🟢 Несколько индикаторов подтверждают покупку.")

    elif sell_signals >= 3 and sell_signals > buy_signals:
        score -= 2
        summary.append("🔴 Несколько индикаторов подтверждают продажу.")

    else:
        summary.append("🟡 Индикаторы не пришли к единому мнению.")

    market_status = determine_market_status(score)

    confidence = calculate_confidence(
        score,
        buy_signals,
        sell_signals
)

    return {
    "score": score,
    "status": market_status,
    "summary": summary,
    "confidence": confidence
}