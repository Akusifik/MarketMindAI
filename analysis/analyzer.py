from analysis.result import AnalysisResult

from indicators.ema import calculate_ema
from indicators.rsi import calculate_rsi
from indicators.macd import calculate_macd
from indicators.bollinger import calculate_bollinger

from analysis.trend import analyze_trend
from analysis.rsi_analysis import analyze_rsi
from analysis.macd_analysis import analyze_macd
from analysis.bollinger_analysis import analyze_bollinger

from indicators.obv import calculate_obv
from analysis.volume_analysis import analyze_obv, analyze_volume_analysis

from indicators.atr import calculate_atr
from analysis.atr_analysis import analyze_atr

from indicators.adx import calculate_adx
from analysis.adx_analysis import analyze_adx

from indicators.vwap import calculate_vwap
from analysis.vwap_analysis import analyze_vwap

from indicators.rvol import calculate_rvol
from analysis.rvol_analysis import analyze_rvol

from indicators.supertrend import calculate_supertrend
from analysis.supertrend_analysis import analyze_supertrend
from analysis.support_resistance import detect_support_resistance
from analysis.market_structure import analyze_market_structure
from analysis.price_action import analyze_price_action

from analysis.evaluate_market import evaluate_market
from analysis.decision_engine import make_decision


def calculate_all_indicators(df):
    # EMA
    df["EMA20"] = calculate_ema(df, 20)
    df["EMA50"] = calculate_ema(df, 50)
    df["EMA200"] = calculate_ema(df, 200)

    # RSI
    df["RSI"] = calculate_rsi(df)

    # MACD
    df["MACD"], df["Signal"], df["Histogram"] = calculate_macd(df)

    # Bollinger
    (
        df["UpperBand"],
        df["MiddleBand"],
        df["LowerBand"]
    ) = calculate_bollinger(df)

    # OBV
    df["OBV"] = calculate_obv(df)

    # ATR
    df["ATR"] = calculate_atr(df)

    # ADX
    df["ADX"] = calculate_adx(df)

    # VWAP
    df["VWAP"] = calculate_vwap(df)

    # RVOL
    df["RVOL"] = calculate_rvol(df)

    df["SuperTrend"], df["SuperTrendDirection"] = calculate_supertrend(df)

    

def analyze_all_indicators(df, result):

        # Trend
            result.trend = analyze_trend(df)
        
            # RSI
            result.rsi = df["RSI"].iloc[-1]
            result.rsi_analysis = analyze_rsi(result.rsi)
        
            # MACD
            result.macd = df["MACD"].iloc[-1]
            result.signal = df["Signal"].iloc[-1]
            result.histogram = df["Histogram"].iloc[-1]
        
            result.macd_analysis = analyze_macd(
                result.macd,
                result.signal
            )
        
            # Bollinger
            result.upper_band = df["UpperBand"].iloc[-1]
            result.middle_band = df["MiddleBand"].iloc[-1]
            result.lower_band = df["LowerBand"].iloc[-1]
        
            result.bollinger_analysis = analyze_bollinger(
                df["close"].iloc[-1],
                result.upper_band,
                result.lower_band
            )
        
            # ATR
            result.atr = df["ATR"].iloc[-1]
        
            result.atr_analysis = analyze_atr(
            result.atr,
            df["close"].iloc[-1]
        )
        
            # ADX
            result.adx = df["ADX"].iloc[-1]
        
            result.adx_analysis = analyze_adx(
            result.adx
        )
        
            # OBV
            current_obv = df["OBV"].iloc[-1]
            previous_obv = df["OBV"].iloc[-2]

            result.obv = current_obv

            result.obv_analysis = analyze_obv(
                [previous_obv, current_obv]

        )
        
            # VWAP
            result.vwap = df["VWAP"].iloc[-1]
        
            result.vwap_analysis = analyze_vwap(
            df["close"].iloc[-1],
            result.vwap
        )
        
            # RVOL
            result.rvol = df["RVOL"].iloc[-1]
        
            result.rvol_analysis = analyze_rvol(
            result.rvol
        )

            result.supertrend = df["SuperTrend"].iloc[-1]

            result.supertrend_direction = df["SuperTrendDirection"].iloc[-1]

            result.supertrend_analysis = analyze_supertrend(
                result.supertrend_direction
        )

            result.support_resistance_zones = detect_support_resistance(df)
            result.market_structure = analyze_market_structure(df)
            result.price_action = analyze_price_action(
                df,
                result.support_resistance_zones,
                result.market_structure,
            )
            result.volume_analysis = analyze_volume_analysis(
                df,
                result.market_structure,
                result.support_resistance_zones,
                result.price_action,
            )

            market = evaluate_market(result)

            result.score = market["score"]
            result.market_status = market["status"]
            result.summary = market["summary"]
            result.confidence = market["confidence"]
            result.decision = make_decision(result)


def analyze_market(df):
    result = AnalysisResult()

    calculate_all_indicators(df)

    analyze_all_indicators(df, result)

    return df, result
