import pandas as pd
import numpy as np

from indicators.atr import calculate_atr


def calculate_supertrend(df, period=10, multiplier=3):

    atr = calculate_atr(df, period)

    hl2 = (df["high"] + df["low"]) / 2

    upperband = hl2 + multiplier * atr
    lowerband = hl2 - multiplier * atr

    supertrend = pd.Series(index=df.index, dtype=float)
    direction = pd.Series(index=df.index, dtype=int)

    direction.iloc[0] = 1
    supertrend.iloc[0] = lowerband.iloc[0]

    for i in range(1, len(df)):

        if df["close"].iloc[i] > upperband.iloc[i - 1]:
            direction.iloc[i] = 1

        elif df["close"].iloc[i] < lowerband.iloc[i - 1]:
            direction.iloc[i] = -1

        else:
            direction.iloc[i] = direction.iloc[i - 1]

            if (
                direction.iloc[i] > 0
                and lowerband.iloc[i] < lowerband.iloc[i - 1]
            ):
                lowerband.iloc[i] = lowerband.iloc[i - 1]

            if (
                direction.iloc[i] < 0
                and upperband.iloc[i] > upperband.iloc[i - 1]
            ):
                upperband.iloc[i] = upperband.iloc[i - 1]

        if direction.iloc[i] > 0:
            supertrend.iloc[i] = lowerband.iloc[i]
        else:
            supertrend.iloc[i] = upperband.iloc[i]

    return supertrend, direction