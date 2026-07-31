def make_decision(result):

    action = "HOLD"
    reasons = []

    rsi = result.rsi_analysis
    macd = result.macd_analysis
    vwap = result.vwap_analysis
    obv = result.obv_analysis
    adx = result.adx_analysis
    rvol = result.rvol_analysis

    buy = 0
    sell = 0

    indicators = [rsi, macd, vwap, obv]

    for indicator in indicators:

        signal = indicator.get("signal")

        if signal == "BUY":
            buy += 1

        elif signal == "SELL":
            sell += 1

    if buy >= 3:
        action = "BUY"
        reasons.append("Большинство ключевых индикаторов поддерживает покупку.")

    elif sell >= 3:
        action = "SELL"
        reasons.append("Большинство ключевых индикаторов поддерживает продажу.")

    else:
        reasons.append("Сигналы противоречат друг другу.")

    if adx.get("status") == "weak":
        reasons.append("Рынок находится во флэте. Сигналы менее надежны.")

    if rvol.get("status") == "low":
        reasons.append("Текущее движение сопровождается низким объемом.")

    if obv.get("signal") == action:
        reasons.append("Объем подтверждает принятое решение.")

    return {
        "action": action,
        "reasons": reasons
    }