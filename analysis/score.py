def calculate_score(result):
    score = 0

    # EMA
    if "Сильный восходящий" in result.trend:
        score += 3

    elif "Краткосрочный восходящий" in result.trend:
        score += 1

    elif "Сильный нисходящий" in result.trend:
        score -= 3

    elif "Краткосрочный нисходящий" in result.trend:
        score -= 1

    if result.supertrend_analysis == "Bullish":
        score += 2

    elif result.supertrend_analysis == "Bearish":
        score -= 2

    # Индикаторы
    score += result.rsi_analysis["score"]
    score += result.macd_analysis["score"]
    score += result.bollinger_analysis["score"]
    score += result.atr_analysis["score"]
    score += result.adx_analysis["score"]
    score += result.obv_analysis["score"]
    score += result.vwap_analysis["score"]
    score += result.rvol_analysis["score"]

    return score


def market_status(score):

    if score >= 6:
        return "🟢 Очень сильный бычий рынок"

    elif score >= 3:
        return "🟢 Бычий рынок"

    elif score >= -2:
        return "🟡 Нейтральный рынок"

    elif score >= -5:
        return "🔴 Медвежий рынок"

    return "🔴 Очень сильный медвежий рынок"