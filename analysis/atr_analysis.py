def analyze_atr(atr, price):
    atr_percent = (atr / price) * 100

    if atr_percent >= 3:
        return {
            "status": "very_high",
            "signal": "NEUTRAL",
            "message": "🔴 Очень высокая волатильность",
            "score": 0,
            "reasons": [
                f"ATR = {atr:.2f}.",
                f"Это составляет {atr_percent:.2f}% от текущей цены.",
                "Волатильность значительно выше обычной.",
                "Ожидаются сильные ценовые колебания и повышенный риск."
            ]
        }

    elif atr_percent >= 1.5:
        return {
            "status": "high",
            "signal": "NEUTRAL",
            "message": "🟠 Высокая волатильность",
            "score": 0,
            "reasons": [
                f"ATR = {atr:.2f}.",
                f"Это составляет {atr_percent:.2f}% от текущей цены.",
                "Рынок движется активно.",
                "Следует учитывать увеличенные ценовые колебания."
            ]
        }

    elif atr_percent >= 0.7:
        return {
            "status": "normal",
            "signal": "NEUTRAL",
            "message": "🟢 Нормальная волатильность",
            "score": 0,
            "reasons": [
                f"ATR = {atr:.2f}.",
                f"Это составляет {atr_percent:.2f}% от текущей цены.",
                "Волатильность находится в пределах нормы.",
                "Рынок движется без экстремальных колебаний."
            ]
        }

    return {
        "status": "low",
        "signal": "NEUTRAL",
        "message": "🔵 Низкая волатильность",
        "score": 0,
        "reasons": [
            f"ATR = {atr:.2f}.",
            f"Это составляет {atr_percent:.2f}% от текущей цены.",
            "Волатильность низкая.",
            "Рынок движется спокойно, возможен период консолидации."
        ]
    }