def analyze_adx(adx):

    if adx >= 40:
        return {
            "status": "very_strong",
            "signal": "NEUTRAL",
            "message": "🔥 Очень сильный тренд",
            "score": 2,
            "reasons": [
                f"ADX = {adx:.2f}.",
                "Значение выше 40.",
                "Тренд очень сильный и имеет высокий импульс."
            ]
        }

    elif adx >= 25:
        return {
            "status": "strong",
            "signal": "NEUTRAL",
            "message": "🟢 Сильный тренд",
            "score": 1,
            "reasons": [
                f"ADX = {adx:.2f}.",
                "Значение выше 25.",
                "На рынке наблюдается сильный тренд."
            ]
        }

    elif adx >= 20:
        return {
            "status": "developing",
            "signal": "NEUTRAL",
            "message": "🟡 Формирующийся тренд",
            "score": 0,
            "reasons": [
                f"ADX = {adx:.2f}.",
                "Значение находится между 20 и 25.",
                "Тренд начинает формироваться, но пока недостаточно силен."
            ]
        }

    return {
        "status": "weak",
        "signal": "NEUTRAL",
        "message": "🔵 Слабый тренд / Флэт",
        "score": -1,
        "reasons": [
            f"ADX = {adx:.2f}.",
            "Значение ниже 20.",
            "Сильного тренда нет, рынок находится во флэте или движется слабо."
        ]
    }