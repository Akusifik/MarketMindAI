def analyze_trend(df):
    price = df["close"].iloc[-1]

    ema20 = df["EMA20"].iloc[-1]
    ema50 = df["EMA50"].iloc[-1]
    ema200 = df["EMA200"].iloc[-1]

    if price > ema20 > ema50 > ema200:
        return "📈 Сильный восходящий тренд"

    elif price < ema20 < ema50 < ema200:
        return "📉 Сильный нисходящий тренд"

    elif price > ema20:
        return "📈 Краткосрочный восходящий тренд"

    elif price < ema20:
        return "📉 Краткосрочный нисходящий тренд"

    else:
        return "➡️ Неопределённый тренд"