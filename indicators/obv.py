def calculate_obv(df):
    obv = [0]

    for i in range(1, len(df)):
        current_close = df["close"].iloc[i]
        previous_close = df["close"].iloc[i - 1]
        current_volume = df["volume"].iloc[i]

        if current_close > previous_close:
            obv.append(obv[-1] + current_volume)

        elif current_close < previous_close:
            obv.append(obv[-1] - current_volume)

        else:
            obv.append(obv[-1])

    return obv