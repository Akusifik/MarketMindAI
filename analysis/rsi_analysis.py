def analyze_rsi(rsi):

    if rsi >= 70:
        return {
            "status": "overbought",
            "signal": "SELL",
            "message": "🔴 Перекупленность",
            "score": -2,
            "reasons": [
                f"RSI = {rsi:.2f}.",
                "Значение выше уровня 70.",
                "Рынок считается перекупленным.",
                "Вероятность коррекции повышается."
            ]
        }

    elif rsi <= 30:
        return {
            "status": "oversold",
            "signal": "BUY",
            "message": "🟢 Перепроданность",
            "score": 2,
            "reasons": [
                f"RSI = {rsi:.2f}.",
                "Значение ниже уровня 30.",
                "Рынок считается перепроданным.",
                "Возможен разворот вверх."
            ]
        }

    return {
        "status": "neutral",
        "signal": "NEUTRAL",
        "message": "🟡 Нейтрально",
        "score": 0,
        "reasons": [
            f"RSI = {rsi:.2f}.",
            "Индикатор находится между 30 и 70.",
            "Явных признаков перекупленности или перепроданности нет."
        ]
    }