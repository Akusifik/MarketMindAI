"""Causal, swing-based market structure analysis.

Structure strength is an evidence score, not a probability or trade signal.
All swing-derived information is actionable no earlier than its confirmation
candle. Break-of-structure events are close-based and actionable at the close
that breaks an already-confirmed structural level.
"""

from analysis.support_resistance import (
    calculate_trailing_atr,
    detect_confirmed_swings,
    validate_ohlc_data,
)


UP_LABELS = {"HH", "HL"}
DOWN_LABELS = {"LH", "LL"}
RECENT_SWING_COUNT = 6
RANGE_ATR_MULTIPLIER = 2.0


def _timestamp(df, index):
    return df["timestamp"].iloc[index] if "timestamp" in df.columns else index


def _annotate_swings(df, swings):
    previous_by_kind = {}
    annotated = []

    for swing in sorted(swings, key=lambda item: item["index"]):
        kind = swing["kind"]
        previous = previous_by_kind.get(kind)

        if previous is None:
            label = kind
        elif kind == "HIGH":
            label = "HH" if swing["price"] > previous["price"] else "LH"
        else:
            label = "HL" if swing["price"] > previous["price"] else "LL"

        item = {
            "type": kind,
            "label": label,
            "price": swing["price"],
            "index": swing["index"],
            "timestamp": _timestamp(df, swing["index"]),
            "confirmed_at": swing["confirmed_at"],
            "confirmed_timestamp": _timestamp(df, swing["confirmed_at"]),
            "actionable_index": swing["confirmed_at"],
            "actionable_timestamp": _timestamp(df, swing["confirmed_at"]),
        }
        annotated.append(item)
        previous_by_kind[kind] = item

    return annotated


def _event_from_breakout(df, source, direction, breakout_index):
    event_type = "BULLISH_BOS" if direction == "BULLISH" else "BEARISH_BOS"
    return {
        "type": event_type,
        "direction": direction,
        "broken_level": source["price"],
        "breakout_price": float(df["close"].iloc[breakout_index]),
        "breakout_index": breakout_index,
        "breakout_timestamp": _timestamp(df, breakout_index),
        "actionable_index": breakout_index,
        "actionable_timestamp": _timestamp(df, breakout_index),
        "source_swing": {
            "type": source["type"],
            "label": source["label"],
            "price": source["price"],
            "pivot_index": source["index"],
            "pivot_timestamp": source["timestamp"],
            "confirmation_index": source["confirmed_at"],
            "confirmation_timestamp": source["confirmed_timestamp"],
        },
    }


def _detect_breaks_of_structure(df, swings):
    """Detect close-based breaks only after their source swings are confirmed."""
    available_swings = []
    events = []
    broken_highs = set()
    broken_lows = set()
    by_confirmation = {}

    for swing in swings:
        by_confirmation.setdefault(swing["confirmed_at"], []).append(swing)

    for candle_index in range(len(df)):
        available_swings.extend(by_confirmation.get(candle_index, []))
        close = df["close"].iloc[candle_index]

        crossed_highs = [
            swing for swing in available_swings
            if swing["type"] == "HIGH"
            and swing["index"] not in broken_highs
            and candle_index > swing["confirmed_at"]
            and close > swing["price"]
        ]
        if crossed_highs:
            source = max(crossed_highs, key=lambda swing: swing["index"])
            broken_highs.update(swing["index"] for swing in crossed_highs)
            events.append(_event_from_breakout(
                df, source, "BULLISH", candle_index,
            ))

        crossed_lows = [
            swing for swing in available_swings
            if swing["type"] == "LOW"
            and swing["index"] not in broken_lows
            and candle_index > swing["confirmed_at"]
            and close < swing["price"]
        ]
        if crossed_lows:
            source = max(crossed_lows, key=lambda swing: swing["index"])
            broken_lows.update(swing["index"] for swing in crossed_lows)
            events.append(_event_from_breakout(
                df, source, "BEARISH", candle_index,
            ))

    return sorted(events, key=lambda event: event["actionable_index"])


def _recent_swings(swings):
    return swings[-RECENT_SWING_COUNT:]


def _is_range(df, swings):
    highs = [swing["price"] for swing in swings if swing["type"] == "HIGH"]
    lows = [swing["price"] for swing in swings if swing["type"] == "LOW"]
    if len(highs) < 2 or len(lows) < 2:
        return False

    latest_confirmation = min(len(df) - 1, swings[-1]["confirmed_at"])
    atr = calculate_trailing_atr(df, latest_confirmation)
    if not atr or atr <= 0:
        return False

    span = max(highs) - min(lows)
    return span <= atr * RANGE_ATR_MULTIPLIER


def _classify_trend(df, swings):
    recent = _recent_swings(swings)
    labels = [swing["label"] for swing in recent]
    if len(labels) < 4:
        return "UNCLEAR"

    recent_labels = labels[-4:]
    if (
        recent_labels.count("HH") >= 2
        and recent_labels.count("HL") >= 2
        and not any(label in DOWN_LABELS for label in recent_labels)
    ):
        return "UPTREND"
    if (
        recent_labels.count("LH") >= 2
        and recent_labels.count("LL") >= 2
        and not any(label in UP_LABELS for label in recent_labels)
    ):
        return "DOWNTREND"
    if _is_range(df, recent):
        return "RANGE"
    return "UNCLEAR"


def _structure_strength(df, swings, trend):
    if trend == "UNCLEAR" or not swings:
        return 0.0

    recent = _recent_swings(swings)
    labels = [swing["label"] for swing in recent]
    if trend == "UPTREND":
        aligned = sum(label in UP_LABELS for label in labels)
    elif trend == "DOWNTREND":
        aligned = sum(label in DOWN_LABELS for label in labels)
    else:
        aligned = len(labels)

    consistency_score = 45 * aligned / len(labels)
    swing_score = min(25, len(recent) * 4)
    latest_confirmation = min(len(df) - 1, recent[-1]["confirmed_at"])
    atr = calculate_trailing_atr(df, latest_confirmation) or 0
    movement = abs(recent[-1]["price"] - recent[0]["price"])
    magnitude_score = min(20, (movement / atr) * 2) if atr > 0 else 0
    recency_score = 10 * (latest_confirmation + 1) / len(df)

    return round(min(
        100,
        consistency_score + swing_score + magnitude_score + recency_score,
    ), 2)


def analyze_market_structure(df, swing_window=2):
    """Return causal structure data; invalid OHLC input raises ``ValueError``."""
    validated = validate_ohlc_data(df)
    raw_swings = detect_confirmed_swings(validated, swing_window)
    swings = _annotate_swings(validated, raw_swings)
    events = _detect_breaks_of_structure(validated, swings)
    trend = _classify_trend(validated, swings)
    strength = _structure_strength(validated, swings, trend)

    reasons = [f"{len(swings)} confirmed structural swing(s) were found."]
    if trend == "UNCLEAR":
        reasons.append("Recent confirmed swing evidence is insufficient or mixed.")
    elif trend == "RANGE":
        reasons.append("Recent swings fit within a volatility-normalized range.")
    else:
        reasons.append(f"Recent confirmed swing sequence supports a {trend.lower()}.")
    if events:
        reasons.append(f"Latest structural event: {events[-1]['type']}.")

    return {
        "trend": trend,
        "strength": strength,
        "swings": swings,
        "structure_sequence": [swing["label"] for swing in swings],
        "events": events,
        "last_event": events[-1] if events else None,
        "reasons": reasons,
    }
