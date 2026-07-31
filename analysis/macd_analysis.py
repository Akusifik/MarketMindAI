def analyze_macd(macd, signal):

    if macd > signal:
        return {
            "status": "bullish",
            "signal": "BUY",
            "message": "🟢 Бычий сигнал",
            "score": 2,
            "reasons": [
                f"MACD = {macd:.4f}.",
                f"Сигнальная линия = {signal:.4f}.",
                "Линия MACD находится выше сигнальной линии.",
                "Это указывает на усиление восходящего импульса."
            ]
        }

    elif macd < signal:
        return {
            "status": "bearish",
            "signal": "SELL",
            "message": "🔴 Медвежий сигнал",
            "score": -2,
            "reasons": [
                f"MACD = {macd:.4f}.",
                f"Сигнальная линия = {signal:.4f}.",
                "Линия MACD находится ниже сигнальной линии.",
                "Это указывает на усиление нисходящего импульса."
            ]
        }

    return {
        "status": "neutral",
        "signal": "NEUTRAL",
        "message": "🟡 Нейтрально",
        "score": 0,
        "reasons": [
            f"MACD = {macd:.4f}.",
            f"Сигнальная линия = {signal:.4f}.",
            "Линии практически совпадают.",
            "Явного бычьего или медвежьего сигнала нет."
        ]
    }