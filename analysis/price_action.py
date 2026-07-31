"""Causal, latest-candle price action analysis.

Price Action v1 is a current-state analyser: it evaluates only the latest
closed candle and intentionally does not return a historical event timeline.
Strength is an evidence score, not a trade probability.
"""

from analysis.support_resistance import calculate_trailing_atr, validate_ohlc_data


def calculate_candle_anatomy(df):
    """Return safe OHLC anatomy for every closed candle in ``df``."""
    validated = validate_ohlc_data(df)
    anatomy = []

    for position, (index, candle) in enumerate(validated.iterrows()):
        open_price = float(candle["open"])
        high = float(candle["high"])
        low = float(candle["low"])
        close = float(candle["close"])
        total_range = high - low
        body_size = abs(close - open_price)
        upper_wick = high - max(open_price, close)
        lower_wick = min(open_price, close) - low
        timestamp = candle["timestamp"] if "timestamp" in validated.columns else index

        if total_range == 0:
            body_ratio = upper_wick_ratio = lower_wick_ratio = 0.0
        else:
            body_ratio = body_size / total_range
            upper_wick_ratio = upper_wick / total_range
            lower_wick_ratio = lower_wick / total_range

        anatomy.append({
            "index": position,
            "timestamp": timestamp,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "body_size": body_size,
            "total_range": total_range,
            "upper_wick": upper_wick,
            "lower_wick": lower_wick,
            "body_ratio": body_ratio,
            "upper_wick_ratio": upper_wick_ratio,
            "lower_wick_ratio": lower_wick_ratio,
            "direction": (
                "BULLISH" if close > open_price
                else "BEARISH" if close < open_price else "NEUTRAL"
            ),
            "causal_atr": calculate_trailing_atr(validated, position),
        })

    return anatomy


def _pattern(pattern_type, direction, candle, strength, reasons):
    return {
        "type": pattern_type,
        "direction": direction,
        "candle_index": candle["index"],
        "timestamp": candle["timestamp"],
        "actionable_index": candle["index"],
        "actionable_timestamp": candle["timestamp"],
        "strength": float(strength),
        "base_strength": float(strength),
        "context_strength": 0.0,
        "reasons": reasons,
    }


def _meaningful_range(candle):
    """Reject trivial wick geometry using volatility known at candle close."""
    if candle["total_range"] <= 0:
        return False
    atr = candle.get("causal_atr")
    if atr is not None and atr > 1e-12:
        return candle["total_range"] >= atr * 0.5
    return candle["total_range"] >= max(abs(candle["close"]) * 0.0005, 1e-9)


def _detect_latest_patterns(anatomy):
    if not anatomy:
        return []

    current = anatomy[-1]
    if current["total_range"] == 0:
        return []

    patterns = []
    if _meaningful_range(current) and current["lower_wick_ratio"] >= 0.5 and current["body_ratio"] <= 0.35 and (
        current["upper_wick_ratio"] <= 0.25
    ) and current["close"] >= current["low"] + current["total_range"] * 0.65:
        patterns.append(_pattern(
            "BULLISH_PIN_BAR",
            "BULLISH",
            current,
            55,
            ["Long lower wick and a close near the candle high."],
        ))

    if _meaningful_range(current) and current["upper_wick_ratio"] >= 0.5 and current["body_ratio"] <= 0.35 and (
        current["lower_wick_ratio"] <= 0.25
    ) and current["close"] <= current["low"] + current["total_range"] * 0.35:
        patterns.append(_pattern(
            "BEARISH_PIN_BAR",
            "BEARISH",
            current,
            55,
            ["Long upper wick and a close near the candle low."],
        ))

    if len(anatomy) < 2:
        return patterns

    previous = anatomy[-2]
    if (
        previous["direction"] == "BEARISH"
        and current["direction"] == "BULLISH"
        and current["open"] <= previous["close"]
        and current["close"] >= previous["open"]
        and current["body_size"] >= previous["body_size"]
    ):
        patterns.append(_pattern(
            "BULLISH_ENGULFING",
            "BULLISH",
            current,
            65,
            ["Bullish body fully engulfs the previous bearish body."],
        ))

    if (
        previous["direction"] == "BULLISH"
        and current["direction"] == "BEARISH"
        and current["open"] >= previous["close"]
        and current["close"] <= previous["open"]
        and current["body_size"] >= previous["body_size"]
    ):
        patterns.append(_pattern(
            "BEARISH_ENGULFING",
            "BEARISH",
            current,
            65,
            ["Bearish body fully engulfs the previous bullish body."],
        ))

    if current["high"] < previous["high"] and current["low"] > previous["low"]:
        patterns.append(_pattern(
            "INSIDE_BAR",
            "NEUTRAL",
            current,
            35,
            ["Current range is contained within the previous candle."],
        ))

    if current["high"] > previous["high"] and current["low"] < previous["low"]:
        patterns.append(_pattern(
            "OUTSIDE_BAR",
            "NEUTRAL",
            current,
            35,
            ["Current range contains the previous candle range."],
        ))

    return patterns


def _interacting_zones(candle, zones):
    return [
        zone for zone in zones
        if candle["low"] <= zone["upper_bound"]
        and candle["high"] >= zone["lower_bound"]
    ]


def _zone_activation_index(zone):
    """Unknown lifecycle data is not treated as historical availability."""
    return zone.get("activation_index")


def _zones_for_interaction(candle, zones, zone_type=None):
    """Keep one materially equivalent zone per current structural context."""
    candidates = _interacting_zones(candle, zones)
    if zone_type:
        candidates = [zone for zone in candidates if zone["type"] == zone_type]

    def overlaps(first, second):
        overlap = max(0.0, min(first["upper_bound"], second["upper_bound"])
                      - max(first["lower_bound"], second["lower_bound"]))
        width = min(
            first["upper_bound"] - first["lower_bound"],
            second["upper_bound"] - second["lower_bound"],
        )
        return width > 0 and overlap / width >= 0.6

    selected = []
    priority = lambda zone: (
        zone.get("strength", 0), bool(zone.get("role_reversal")),
        -abs((zone["lower_bound"] + zone["upper_bound"]) / 2 - candle["close"]),
    )
    for zone in sorted(candidates, key=priority, reverse=True):
        if not any(overlaps(zone, existing) for existing in selected):
            selected.append(zone)
    return selected


def _zone_key(zone):
    return (
        zone["type"], round(zone["lower_bound"], 8),
        round(zone["upper_bound"], 8), zone.get("source_type"),
    )


def _apply_pattern_context(patterns, candle, zones, market_structure):
    for pattern in patterns:
        matching_type = "SUPPORT" if pattern["direction"] == "BULLISH" else "RESISTANCE"
        relevant_zones = _zones_for_interaction(candle, zones, matching_type)
        if relevant_zones:
            zone = max(relevant_zones, key=lambda item: item["strength"])
            context_bonus = min(25, 15 + zone["strength"] * 0.1)
            pattern["context_strength"] = context_bonus
            pattern["context_zone"] = _zone_key(zone)
            pattern["strength"] = min(100, pattern["base_strength"] + context_bonus)
            pattern["reasons"].append(
                f"Pattern interacts with {matching_type.lower()} zone "
                f"(strength {zone['strength']:.1f})."
            )
            if zone.get("role_reversal"):
                pattern["context_strength"] = min(25, pattern["context_strength"] + 5)
                pattern["strength"] = min(100, pattern["base_strength"] + pattern["context_strength"])
                pattern["reasons"].append("Zone has confirmed role-reversal history.")

        prior_events = [
            event for event in market_structure.get("events", [])
            if event["actionable_index"] <= pattern["actionable_index"]
        ]
        if prior_events:
            event = prior_events[-1]
            aligned = (
                event["direction"] == pattern["direction"]
                and pattern["actionable_index"] - event["actionable_index"] <= 3
            )
            if aligned:
                pattern["context_strength"] = min(25, pattern["context_strength"] + 5)
                pattern["strength"] = min(100, pattern["base_strength"] + pattern["context_strength"])
                pattern["reasons"].append("Recent aligned structural BOS provides context.")


def _detect_rejections(candle, zones):
    rejections = []
    if not _meaningful_range(candle):
        return rejections

    close_position = (candle["close"] - candle["low"]) / candle["total_range"]
    for zone in _zones_for_interaction(candle, zones):
        if (
            zone["type"] == "SUPPORT"
            and candle["lower_wick_ratio"] >= 0.4
            and close_position >= 0.6
        ):
            rejections.append({
                "type": "SUPPORT_REJECTION",
                "direction": "BULLISH",
                "candle_index": candle["index"],
                "timestamp": candle["timestamp"],
                "actionable_index": candle["index"],
                "actionable_timestamp": candle["timestamp"],
                "strength": round(min(
                    100,
                    40 + candle["lower_wick_ratio"] * 30 + zone["strength"] * 0.2,
                ), 2),
                "zone_key": _zone_key(zone),
                "reasons": ["Lower-wick rejection closes strongly from support."],
            })
        elif (
            zone["type"] == "RESISTANCE"
            and candle["upper_wick_ratio"] >= 0.4
            and close_position <= 0.4
        ):
            rejections.append({
                "type": "RESISTANCE_REJECTION",
                "direction": "BEARISH",
                "candle_index": candle["index"],
                "timestamp": candle["timestamp"],
                "actionable_index": candle["index"],
                "actionable_timestamp": candle["timestamp"],
                "strength": round(min(
                    100,
                    40 + candle["upper_wick_ratio"] * 30 + zone["strength"] * 0.2,
                ), 2),
                "zone_key": _zone_key(zone),
                "reasons": ["Upper-wick rejection closes weakly from resistance."],
            })

    return rejections


def _detect_zone_events(anatomy, zones, rejections):
    if len(anatomy) < 2:
        return []

    current = anatomy[-1]
    previous = anatomy[-2]
    events = []
    rejection_keys = {(item["direction"], item.get("zone_key")) for item in rejections}

    for zone in _zones_for_interaction(current, zones):
        activation_index = _zone_activation_index(zone)
        # A final zone can only explain price action after its final bounds
        # were available. Missing lifecycle data is deliberately ineligible.
        if activation_index is not None and activation_index < previous["index"]:
            if previous["close"] <= zone["upper_bound"] < current["close"]:
                events.append({
                    "type": "CLOSE_ABOVE_ZONE", "direction": "BULLISH",
                    "zone_type": zone["type"], "zone_key": _zone_key(zone),
                    "candle_index": current["index"], "timestamp": current["timestamp"],
                    "breakout_index": current["index"], "breakout_timestamp": current["timestamp"],
                    "zone_activation_index": activation_index,
                    "actionable_index": current["index"], "actionable_timestamp": current["timestamp"],
                    "strength": 20, "reasons": ["Latest close moved above an active zone boundary."],
                })
            elif previous["close"] >= zone["lower_bound"] > current["close"]:
                events.append({
                    "type": "CLOSE_BELOW_ZONE", "direction": "BEARISH",
                    "zone_type": zone["type"], "zone_key": _zone_key(zone),
                    "candle_index": current["index"], "timestamp": current["timestamp"],
                    "breakout_index": current["index"], "breakout_timestamp": current["timestamp"],
                    "zone_activation_index": activation_index,
                    "actionable_index": current["index"], "actionable_timestamp": current["timestamp"],
                    "strength": 20, "reasons": ["Latest close moved below an active zone boundary."],
                })

        matching_transitions = [
            transition for transition in zone.get("role_transitions", [])
            if transition.get("to") == zone["type"]
            and transition.get("direction") == (
                "BULLISH" if zone["type"] == "SUPPORT" else "BEARISH"
            )
            and transition.get("activation_index", transition.get("confirmed_at", float("inf")))
            < current["index"]
            and transition.get("breakout_index", float("inf"))
            < current["index"]
        ]
        if matching_transitions:
            transition = matching_transitions[-1]
            direction = transition["direction"]
            event_type = (
                "RETEST_REJECTION"
                if (direction, _zone_key(zone)) in rejection_keys else "RETEST"
            )
            events.append({
                "type": event_type,
                "direction": direction,
                "zone_type": zone["type"],
                "zone_key": _zone_key(zone),
                "candle_index": current["index"],
                "timestamp": current["timestamp"],
                "actionable_index": current["index"],
                "actionable_timestamp": current["timestamp"],
                "strength": 20,
                "breakout_index": transition["breakout_index"],
                "transition_index": transition["activation_index"],
                "reasons": ["Latest candle retests a previously confirmed role reversal."],
            })

    return events


def _bias(patterns, rejections, zone_events):
    """Aggregate related candle/zone evidence once, retaining independent zones."""
    grouped = {"BULLISH": {}, "BEARISH": {}}
    for item in patterns + rejections + zone_events:
        direction = item["direction"]
        if direction not in grouped:
            continue
        key = item.get("context_zone") or item.get("zone_key")
        key = (item["candle_index"], key or ("PATTERN", item["type"]))
        grouped[direction][key] = max(
            grouped[direction].get(key, 0), item["strength"],
        )

    bullish = min(100, sum(grouped["BULLISH"].values()))
    bearish = min(100, sum(grouped["BEARISH"].values()))

    if bullish - bearish >= 15:
        return "BULLISH", min(100, round(bullish))
    if bearish - bullish >= 15:
        return "BEARISH", min(100, round(bearish))
    return "NEUTRAL", min(100, round(max(bullish, bearish)))


def analyze_price_action(df, zones=None, market_structure=None):
    """Return latest-candle price action using supplied S/R and structure data."""
    anatomy = calculate_candle_anatomy(df)
    zones = zones or []
    market_structure = market_structure or {}
    if not anatomy:
        return {
            "bias": "NEUTRAL", "strength": 0, "patterns": [],
            "rejections": [], "zone_events": [],
            "reasons": ["No closed candles available for price action analysis."],
        }

    current = anatomy[-1]
    patterns = _detect_latest_patterns(anatomy)
    _apply_pattern_context(patterns, current, zones, market_structure)
    rejections = _detect_rejections(current, zones)
    zone_events = _detect_zone_events(anatomy, zones, rejections)
    bias, strength = _bias(patterns, rejections, zone_events)

    reasons = ["Price action uses the latest closed candle only."]
    if patterns:
        reasons.append(f"{len(patterns)} meaningful candle pattern(s) detected.")
    if rejections:
        reasons.append(f"{len(rejections)} contextual zone rejection(s) detected.")
    if zone_events:
        reasons.append(f"{len(zone_events)} zone interaction event(s) detected.")
    if not patterns and not rejections and not zone_events:
        reasons.append("No meaningful current price action signal detected.")

    return {
        "bias": bias,
        "strength": strength,
        "patterns": patterns,
        "rejections": rejections,
        "zone_events": zone_events,
        "reasons": reasons,
    }
