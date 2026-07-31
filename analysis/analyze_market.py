def analyze_market(result):

    score = 0
    summary = []

    indicators = [
        result.rsi_analysis,
        result.macd_analysis,
        result.bollinger_analysis,
        result.atr_analysis,
        result.adx_analysis,
        result.obv_analysis,
        result.vwap_analysis,
        result.rvol_analysis,
    ]

    for indicator in indicators:

        score += indicator.get("score", 0)

        if indicator.get("message"):
            summary.append(indicator["message"])

    if score >= 5:
        market_status = "🟢 Сильный бычий рынок"

    elif score >= 2:
        market_status = "🟢 Бычий рынок"

    elif score <= -5:
        market_status = "🔴 Сильный медвежий рынок"

    elif score <= -2:
        market_status = "🔴 Медвежий рынок"

    else:
        market_status = "🟡 Нейтральный рынок"

    return {
        "score": score,
        "status": market_status,
        "summary": summary
    }