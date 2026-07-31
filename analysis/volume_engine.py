def analyze_volume(result):
    score = 0
    messages = []

    indicators = [
        result.obv_analysis,
        result.vwap_analysis,
        result.rvol_analysis
    ]

    for indicator in indicators:
        score += indicator["score"]
        messages.append(indicator["message"])

    if score >= 3:
        signal = "bullish"
        summary = "Объем уверенно подтверждает рост."

    elif score <= -2:
        signal = "bearish"
        summary = "Объем подтверждает снижение."

    else:
        signal = "neutral"
        summary = "Объем не дает четкого подтверждения."

    return {
        "signal": signal,
        "score": score,
        "summary": summary,
        "details": messages
    }