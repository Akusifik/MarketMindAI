def calculate_rvol(df, period=20):
    average_volume = df["volume"].rolling(period).mean()

    rvol = df["volume"] / average_volume

    return rvol