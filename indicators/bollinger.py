def calculate_bollinger(df, period=20, std_multiplier=2):
    middle = df["close"].rolling(window=period).mean()

    std = df["close"].rolling(window=period).std()

    upper = middle + (std * std_multiplier)
    lower = middle - (std * std_multiplier)

    return upper, middle, lower