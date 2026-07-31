"""Legacy OBV helper plus causal, current-state volume interpretation."""

import numpy as np

from analysis.support_resistance import calculate_trailing_atr, validate_ohlc_data


def analyze_obv(obv):
    current = obv[-1]
    previous = obv[-2]

    if current > previous:
        return {
            "status": "bullish",
            "signal": "BUY",
            "message": "🟢 Объем подтверждает рост",
            "score": 1,
            "reasons": [
                f"Текущее значение OBV = {current:.2f}.",
                f"Предыдущее значение OBV = {previous:.2f}.",
                "OBV увеличивается.",
                "Рост объема подтверждает восходящее движение цены."
            ]
        }

    elif current < previous:
        return {
            "signal": "SELL",
            "message": "🔴 Объем подтверждает снижение",
            "score": -1,
            "reasons": [
                f"Текущее значение OBV = {current:.2f}.",
                f"Предыдущее значение OBV = {previous:.2f}.",
                "OBV снижается.",
                "Объем подтверждает нисходящее движение цены."
            ]
        }

    return {
        "status": "neutral",
        "signal": "NEUTRAL",
        "message": "🟡 Объем без изменений",
        "score": 0,
        "reasons": [
            f"Текущее значение OBV = {current:.2f}.",
            f"Предыдущее значение OBV = {previous:.2f}.",
            "OBV практически не изменился.",
            "Объем не подтверждает ни рост, ни снижение."
        ]
    }


def _require_volume(df):
    validated = validate_ohlc_data(df)
    if "volume" not in validated.columns:
        raise ValueError("Volume Analysis requires a volume column.")
    return validated


def _causal_volume_ratios(df, period=20):
    """Return ratios against prior-only rolling medians; never future volume."""
    ratios = []
    for index, volume in enumerate(df["volume"]):
        history = df["volume"].iloc[max(0, index - period):index]
        baseline = float(history.median()) if not history.empty else None
        ratios.append(float(volume) / baseline if baseline and baseline > 0 else None)
    return ratios


def _existing_rvol(df, index):
    """Use the pipeline RVOL as supplemental context when it is valid."""
    if "RVOL" not in df.columns:
        return None
    try:
        value = float(df["RVOL"].iloc[index])
    except (TypeError, ValueError):
        return None
    return value if np.isfinite(value) and value > 0 else None


def _volume_state(ratio, volume):
    if volume == 0:
        return "NO_VOLUME"
    if ratio is None:
        return "NORMAL"
    if ratio >= 4:
        return "EXTREME_SPIKE"
    if ratio >= 2.5:
        return "HIGH"
    if ratio >= 1.5:
        return "ELEVATED"
    return "NORMAL"


def _price_volume_relation(df, ratio):
    if len(df) < 2 or df["volume"].iloc[-1] == 0:
        return "INSUFFICIENT_EVIDENCE"
    change = float(df["close"].iloc[-1] - df["close"].iloc[-2])
    atr = calculate_trailing_atr(df, len(df) - 1) or max(float(df["close"].iloc[-1]) * .001, 1e-9)
    movement = abs(change) / atr
    if movement >= 1 and (ratio is None or ratio < .8):
        return "LARGE_MOVE_WEAK_VOLUME"
    if change > 0:
        return "PRICE_UP_VOLUME_EXPANDING" if ratio is not None and ratio >= 1.2 else "PRICE_UP_VOLUME_CONTRACTING"
    if change < 0:
        return "PRICE_DOWN_VOLUME_EXPANDING" if ratio is not None and ratio >= 1.2 else "PRICE_DOWN_VOLUME_CONTRACTING"
    if ratio is not None and ratio >= 2.5:
        return "SMALL_MOVE_HIGH_VOLUME"
    return "FLAT_PRICE_NORMAL_VOLUME"


def _effort_result(df, ratio):
    if len(df) < 2 or ratio is None or df["volume"].iloc[-1] == 0:
        return {"type": "INSUFFICIENT_EVIDENCE", "strength": 0, "reasons": ["No established causal volume baseline."]}
    change = float(df["close"].iloc[-1] - df["close"].iloc[-2])
    atr = calculate_trailing_atr(df, len(df) - 1) or max(float(df["close"].iloc[-1]) * .001, 1e-9)
    result = abs(change) / atr
    if ratio >= 2.5 and result < .4:
        return {"type": "HIGH_EFFORT_LOW_RESULT", "strength": 70, "direction": "NEUTRAL", "reasons": ["High relative volume produced little price progress."]}
    if ratio >= 2 and result >= 1:
        return {"type": "HIGH_EFFORT_STRONG_RESULT", "strength": 70, "direction": "BULLISH" if change > 0 else "BEARISH", "reasons": ["High relative volume accompanied a large directional move."]}
    if result >= 1 and ratio < .8:
        return {"type": "LARGE_MOVE_WEAK_VOLUME", "strength": 55, "direction": "BULLISH" if change > 0 else "BEARISH", "reasons": ["Large move occurred on weak relative volume."]}
    return {"type": "BALANCED_EFFORT_RESULT", "strength": 25, "direction": "NEUTRAL", "reasons": ["Price progress is proportionate to recent volume."]}


def _breakout_confirmation(df, ratios, market_structure, price_action):
    """Confirm only a validated breakout occurring on the analysed candle."""
    index = len(df) - 1
    timestamp = df["timestamp"].iloc[index] if "timestamp" in df.columns else index

    def valid_event(event, structural=False):
        breakout_index = event.get("breakout_index")
        if (
            not isinstance(breakout_index, (int, np.integer))
            or breakout_index != index
            or event.get("actionable_index") != breakout_index
            or event.get("breakout_timestamp") != timestamp
            or event.get("actionable_timestamp") != timestamp
            or event.get("direction") not in {"BULLISH", "BEARISH"}
        ):
            return False
        if structural:
            source = event.get("source_swing", {})
            return isinstance(source.get("confirmation_index"), (int, np.integer)) and source["confirmation_index"] < breakout_index
        return isinstance(event.get("zone_activation_index"), (int, np.integer)) and event["zone_activation_index"] < breakout_index

    events = [event for event in market_structure.get("events", []) if valid_event(event, structural=True)]
    events += [event for event in price_action.get("zone_events", []) if event.get("type", "").startswith("CLOSE_") and valid_event(event)]
    if not events:
        return {"level": "NONE", "events": [], "reasons": ["No current, validated breakout event was supplied."]}
    if df["volume"].iloc[index] == 0:
        return {"level": "NONE", "events": events, "reasons": ["Breakout candle has zero volume and cannot be volume-confirmed."]}
    ratio = ratios[index]
    if ratio is None or ratio < 1.2:
        level = "WEAK"
    elif ratio < 2:
        level = "MODERATE"
    else:
        level = "STRONG"
    return {"level": level, "events": events, "reasons": [f"Breakout volume is {level.lower()} relative to its causal baseline."]}


def _divergences(df, ratios, market_structure):
    """Compare only two confirmed, actionable structural highs or lows.

    Bullish divergence requires a confirmed LL with a higher OBV low, or a
    materially smaller causal volume ratio than the prior structural low.
    Bearish divergence mirrors this at a confirmed HH.
    """
    usable = [
        swing for swing in market_structure.get("swings", [])
        if isinstance(swing.get("index"), (int, np.integer))
        and isinstance(swing.get("actionable_index"), (int, np.integer))
        and swing["actionable_index"] <= len(df) - 1
        and swing["index"] < len(df)
    ]
    obv_available = "OBV" in df.columns and np.isfinite(np.asarray(df["OBV"], dtype=float)).all()
    result = []
    for swing_type, label, direction, name in (
        ("LOW", "LL", "BULLISH", "WEAKENING_SELLING_PARTICIPATION"),
        ("HIGH", "HH", "BEARISH", "WEAKENING_BULLISH_PARTICIPATION"),
    ):
        points = [swing for swing in usable if swing.get("type") == swing_type]
        if len(points) < 2 or points[-1].get("label") != label:
            continue
        previous, current = points[-2:]
        previous_ratio, current_ratio = ratios[previous["index"]], ratios[current["index"]]
        if obv_available:
            participation_weaker = (
                df["OBV"].iloc[current["index"]] > df["OBV"].iloc[previous["index"]]
                if direction == "BULLISH"
                else df["OBV"].iloc[current["index"]] < df["OBV"].iloc[previous["index"]]
            )
            evidence = "OBV did not confirm the new structural extreme."
        else:
            participation_weaker = previous_ratio is not None and current_ratio is not None and current_ratio < previous_ratio * .75
            evidence = "Causal relative volume was materially smaller at the new structural extreme."
        if participation_weaker:
            result.append({
                "direction": direction, "type": name, "strength": 55,
                "evidence": [evidence], "source_swings": [previous, current],
                "actionable_index": current["actionable_index"],
                "actionable_timestamp": current.get("actionable_timestamp"),
            })
    return result


def _accumulation_distribution(df, ratios, zones):
    if len(df) < 3 or df["volume"].iloc[-1] == 0:
        return {"state": "NEUTRAL", "strength": 0, "reasons": ["Insufficient recent candles for a volume heuristic."]}
    recent = df.iloc[-3:]
    high_volume = sum(ratio is not None and ratio >= 1.5 for ratio in ratios[-3:]) >= 2
    rising = recent["close"].iloc[-1] > recent["close"].iloc[0]
    falling = recent["close"].iloc[-1] < recent["close"].iloc[0]
    last_range = recent["high"].iloc[-1] - recent["low"].iloc[-1]
    close_position = ((recent["close"].iloc[-1] - recent["low"].iloc[-1]) / last_range) if last_range else .5
    obv_available = "OBV" in df.columns and np.isfinite(
        np.asarray(df["OBV"], dtype=float),
    ).all()
    obv_rising = not obv_available or df["OBV"].iloc[-1] > df["OBV"].iloc[-3]
    obv_falling = not obv_available or df["OBV"].iloc[-1] < df["OBV"].iloc[-3]
    if high_volume and rising and close_position >= .6 and obv_rising:
        return {"state": "ACCUMULATION", "strength": 55, "reasons": ["Repeated elevated volume accompanied constructive recent closes; heuristic only."]}
    if high_volume and falling and close_position <= .4 and obv_falling:
        return {"state": "DISTRIBUTION", "strength": 55, "reasons": ["Repeated elevated volume accompanied weak recent closes; heuristic only."]}
    return {"state": "NEUTRAL", "strength": 15, "reasons": ["Recent price and volume do not support a conservative accumulation/distribution heuristic."]}


def analyze_volume_analysis(df, market_structure=None, support_resistance_zones=None, price_action=None):
    """Interpret the latest closed candle using causal volume and supplied context.

    This is analysis-only. It does not create structural breakouts or recalculate
    OBV/RVOL: existing indicator columns are consumed when supplied.
    """
    df = _require_volume(df)
    market_structure = market_structure or {}
    support_resistance_zones = support_resistance_zones or []
    price_action = price_action or {}
    if len(df) == 0:
        return {"bias": "NEUTRAL", "strength": 0, "volume_state": "NO_VOLUME", "price_volume_relation": "INSUFFICIENT_EVIDENCE", "breakout_confirmation": {"level": "NONE", "events": [], "reasons": []}, "divergences": [], "effort_result": {"type": "INSUFFICIENT_EVIDENCE", "strength": 0, "reasons": []}, "accumulation_distribution": {"state": "NEUTRAL", "strength": 0, "reasons": []}, "reasons": ["No closed candles available."]}

    ratios = _causal_volume_ratios(df)
    causal_ratio = ratios[-1]
    pipeline_rvol = _existing_rvol(df, len(df) - 1)
    # RVOL is supplemental: it can confirm, but cannot replace a prior-only
    # baseline or mask a causal spike.
    ratio = max(
        [value for value in (causal_ratio, pipeline_rvol) if value is not None],
        default=None,
    )
    state = _volume_state(ratio, float(df["volume"].iloc[-1]))
    relation = _price_volume_relation(df, ratio)
    effort = _effort_result(df, ratio)
    breakout = _breakout_confirmation(df, ratios, market_structure, price_action)
    divergences = _divergences(df, ratios, market_structure)
    accumulation = _accumulation_distribution(df, ratios, support_resistance_zones)

    # Relation, effort and breakout can be alternate descriptions of the same
    # latest-candle volume spike. Keep their contribution in one capped group.
    bullish = bearish = 0
    latest_bullish = latest_bearish = 0
    if relation == "PRICE_UP_VOLUME_EXPANDING": latest_bullish = 30
    elif relation == "PRICE_DOWN_VOLUME_EXPANDING": latest_bearish = 30
    elif relation == "PRICE_UP_VOLUME_CONTRACTING": latest_bearish = 10
    elif relation == "PRICE_DOWN_VOLUME_CONTRACTING": latest_bullish = 10
    if effort.get("type") == "HIGH_EFFORT_STRONG_RESULT":
        if effort.get("direction") == "BULLISH": latest_bullish = max(latest_bullish, 35)
        else: latest_bearish = max(latest_bearish, 35)
    if breakout["level"] == "STRONG":
        for event in breakout["events"]:
            if event.get("direction") == "BULLISH": latest_bullish = max(latest_bullish, 55)
            elif event.get("direction") == "BEARISH": latest_bearish = max(latest_bearish, 55)
    bullish += latest_bullish
    bearish += latest_bearish
    # This is multi-candle evidence, but it still shares the latest-volume
    # signal; retain a modest capped incremental contribution.
    if accumulation["state"] == "ACCUMULATION": bullish += 10
    elif accumulation["state"] == "DISTRIBUTION": bearish += 10
    for divergence in divergences:
        if divergence["direction"] == "BULLISH": bullish += 15
        else: bearish += 15
    if df["volume"].iloc[-1] == 0:
        bullish = bearish = 0
    strength = min(100, max(bullish, bearish))
    bias = "BULLISH" if bullish - bearish >= 15 else "BEARISH" if bearish - bullish >= 15 else "NEUTRAL"
    reasons = [f"Volume state: {state}.", f"Price/volume relation: {relation}."] + effort["reasons"] + breakout["reasons"] + accumulation["reasons"]
    return {"bias": bias, "strength": float(strength), "volume_state": state, "volume_ratio": causal_ratio, "rvol": pipeline_rvol, "price_volume_relation": relation, "breakout_confirmation": breakout, "divergences": divergences, "effort_result": effort, "accumulation_distribution": accumulation, "reasons": reasons}
