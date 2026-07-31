def analyze_vwap(price, vwap):
    difference = ((price - vwap) / vwap) * 100

    if price > vwap:
        return {
            "signal": "BUY",
            "score": 1,
            "message": (
                f"Цена выше VWAP на "
                f"{difference:.2f}%"
            ),
            "reasons": [
                f"Текущая цена = {price:.2f}.",
                f"VWAP = {vwap:.2f}.",
                f"Цена находится выше VWAP на {difference:.2f}%.",
                "Это говорит о том, что покупатели контролируют рынок."
            ]
        }

    elif price < vwap:
        return {
            "signal": "SELL",
            "score": -1,
            "message": (
                f"Цена ниже VWAP на "
                f"{abs(difference):.2f}%"
            ),
            "reasons": [
                f"Текущая цена = {price:.2f}.",
                f"VWAP = {vwap:.2f}.",
                f"Цена находится ниже VWAP на {abs(difference):.2f}%.",
                "Это говорит о том, что продавцы контролируют рынок."
            ]
        }

    return {
        "signal": "NEUTRAL",
        "score": 0,
        "message": "Цена совпадает с VWAP",
        "reasons": [
            f"Текущая цена = {price:.2f}.",
            f"VWAP = {vwap:.2f}.",
            "Цена практически совпадает с VWAP.",
            "На рынке отсутствует выраженное преимущество покупателей или продавцов."
        ]
    }