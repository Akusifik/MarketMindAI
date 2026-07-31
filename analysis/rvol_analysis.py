def analyze_rvol(rvol):

    if rvol >= 2:
        return {
            "status": "very_high",
            "signal": "NEUTRAL",
            "score": 2,
            "message": f"Очень высокий объем ({rvol:.2f}x)",
            "reasons": [
                f"RVOL = {rvol:.2f}x.",
                "Объем более чем в 2 раза превышает средний.",
                "На рынке наблюдается очень высокая активность участников.",
                "Сильные движения имеют повышенную вероятность продолжения."
            ]
        }

    elif rvol >= 1.2:
        return {
            "status": "high",
            "signal": "NEUTRAL",
            "score": 1,
            "message": f"Объем выше среднего ({rvol:.2f}x)",
            "reasons": [
                f"RVOL = {rvol:.2f}x.",
                "Объем выше среднего значения.",
                "Повышенная активность участников поддерживает текущее движение."
            ]
        }

    elif rvol >= 0.8:
        return {
            "status": "normal",
            "signal": "NEUTRAL",
            "score": 0,
            "message": f"Средний объем ({rvol:.2f}x)",
            "reasons": [
                f"RVOL = {rvol:.2f}x.",
                "Объем находится около среднего уровня.",
                "Рынок не демонстрирует необычной активности."
            ]
        }

    return {
        "status": "low",
        "signal": "NEUTRAL",
        "score": -1,
        "message": f"Низкий объем ({rvol:.2f}x)",
        "reasons": [
            f"RVOL = {rvol:.2f}x.",
            "Объем ниже среднего.",
            "Текущее движение может быть недостаточно подтверждено объемом."
        ]
    }