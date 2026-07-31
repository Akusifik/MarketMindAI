"""Causal support and resistance zone detection for OHLCV data.

Invalid market data raises ``ValueError`` rather than producing zones. Swing
pivots are emitted only after their right-side confirmation window has closed.
For backtests, pass candles only up to the current bar.
"""

import numpy as np
import pandas as pd


DEFAULT_SWING_WINDOW = 2
DEFAULT_ATR_MULTIPLIER = 0.75
DEFAULT_FALLBACK_TOLERANCE_PCT = 0.003
DEFAULT_CONFIRMATION_BARS = 2
MAX_ZONE_WIDTH_TOLERANCE_MULTIPLIER = 3.0
MATERIAL_OVERLAP_RATIO = 0.6


def validate_ohlc_data(df):
    """Return validated numeric candle data or raise ValueError predictably."""
    required_columns = {"open", "high", "low", "close"}
    missing_columns = required_columns - set(df.columns)
    if missing_columns:
        raise ValueError(f"Missing OHLC columns: {sorted(missing_columns)}")

    validated = df.copy()
    for column in required_columns:
        validated[column] = pd.to_numeric(validated[column], errors="coerce")

    ohlc = validated[list(required_columns)]
    if not np.isfinite(ohlc.to_numpy(dtype=float)).all():
        raise ValueError("OHLC values must be finite numeric values.")
    if (ohlc <= 0).any().any():
        raise ValueError("OHLC prices must be positive.")
    if (validated["high"] < validated["low"]).any():
        raise ValueError("Candle high cannot be below candle low.")
    if (
        (validated["open"] < validated["low"]).any()
        or (validated["open"] > validated["high"]).any()
        or (validated["close"] < validated["low"]).any()
        or (validated["close"] > validated["high"]).any()
    ):
        raise ValueError("Candle open and close must be within high/low bounds.")

    if "volume" in validated.columns:
        validated["volume"] = pd.to_numeric(
            validated["volume"], errors="coerce",
        )
        volume = validated["volume"].to_numpy(dtype=float)
        if not np.isfinite(volume).all() or (volume < 0).any():
            raise ValueError("Volume values must be finite and non-negative.")

    if "timestamp" in validated.columns:
        timestamps = pd.to_datetime(validated["timestamp"], errors="coerce")
        if timestamps.isna().any() or not timestamps.is_monotonic_increasing:
            raise ValueError("Timestamps must be valid and chronologically ordered.")
        if timestamps.duplicated().any():
            raise ValueError("Timestamps must not contain duplicates.")
        validated["timestamp"] = timestamps
    elif (
        not validated.index.is_monotonic_increasing
        or validated.index.duplicated().any()
    ):
        raise ValueError("Index must be chronologically ordered and unique.")

    return validated


def _validate_data(df):
    """Backward-compatible internal alias for shared OHLC validation."""
    return validate_ohlc_data(df)


def _empty_zones_if_insufficient(df, swing_window):
    required_candles = (swing_window * 2) + 1
    return [] if len(df) < required_candles else None


def _swing_points(df, swing_window):
    """Return pivots only once the right-side confirmation window is known."""
    swings = []

    for index in range(swing_window, len(df) - swing_window):
        left = index - swing_window
        right = index + swing_window
        high = df["high"].iloc[index]
        low = df["low"].iloc[index]
        window_highs = df["high"].iloc[left:right + 1]
        window_lows = df["low"].iloc[left:right + 1]

        if high == window_highs.max() and high > df["high"].iloc[left:index].max():
            swings.append({
                "kind": "HIGH",
                "price": float(high),
                "index": index,
                "confirmed_at": right,
            })

        if low == window_lows.min() and low < df["low"].iloc[left:index].min():
            swings.append({
                "kind": "LOW",
                "price": float(low),
                "index": index,
                "confirmed_at": right,
            })

    return swings


def detect_confirmed_swings(df, swing_window=DEFAULT_SWING_WINDOW):
    """Return causal confirmed pivots for analysis engines sharing OHLCV data."""
    if swing_window < 1:
        raise ValueError("swing_window must be at least 1.")
    if len(df) < (swing_window * 2) + 1:
        return []
    return _swing_points(df, swing_window)


def _trailing_atr(df, index, period=14):
    """Estimate true range using only candles at or before ``index``."""
    start = max(1, index - period + 1)
    true_ranges = []

    for candle_index in range(start, index + 1):
        high = df["high"].iloc[candle_index]
        low = df["low"].iloc[candle_index]
        previous_close = df["close"].iloc[candle_index - 1]
        true_ranges.append(max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close),
        ))

    return sum(true_ranges) / len(true_ranges) if true_ranges else None


def calculate_trailing_atr(df, index, period=14):
    """Return causal trailing ATR for analysis engines sharing OHLCV data."""
    return _trailing_atr(df, index, period)


def _swing_tolerance(df, swing, atr_column, atr_multiplier, fallback_tolerance_pct):
    atr = None
    if atr_column in df.columns:
        candidate = pd.to_numeric(
            pd.Series([df[atr_column].iloc[swing["confirmed_at"]]]),
            errors="coerce",
        ).iloc[0]
        if pd.notna(candidate) and np.isfinite(candidate) and candidate > 0:
            atr = float(candidate)

    if atr is None:
        atr = _trailing_atr(df, swing["confirmed_at"])

    if atr is not None and atr > 0:
        return atr * atr_multiplier

    return swing["price"] * fallback_tolerance_pct


def _cluster_swings(swings):
    """Merge same-type pivots without exceeding their causal width budget."""
    clusters = []

    for kind in ("HIGH", "LOW"):
        type_clusters = []
        type_swings = sorted(
            (swing for swing in swings if swing["kind"] == kind),
            key=lambda item: item["price"],
        )

        for swing in type_swings:
            if not type_clusters:
                type_clusters.append([swing])
                continue

            cluster = type_clusters[-1]
            center = sum(item["price"] for item in cluster) / len(cluster)
            proximity_tolerance = np.median(
                [item["tolerance"] for item in cluster] + [swing["tolerance"]],
            )
            candidate = cluster + [swing]
            lower_bound, upper_bound, _ = _zone_bounds(candidate)

            if (
                abs(swing["price"] - center) <= proximity_tolerance
                and upper_bound - lower_bound <= _max_zone_width(candidate)
            ):
                cluster.append(swing)
            else:
                type_clusters.append([swing])

        clusters.extend(type_clusters)

    return clusters


def _zone_bounds(cluster):
    average_tolerance = sum(item["tolerance"] for item in cluster) / len(cluster)
    prices = [item["price"] for item in cluster]
    return (
        min(prices) - (average_tolerance / 2),
        max(prices) + (average_tolerance / 2),
        sum(prices) / len(prices),
    )


def _max_zone_width(cluster):
    """Use only formation-time tolerances to bound a cluster's total width."""
    causal_tolerance = np.median([item["tolerance"] for item in cluster])
    return causal_tolerance * MAX_ZONE_WIDTH_TOLERANCE_MULTIPLIER


def _interaction_groups(df, lower_bound, upper_bound, start_index, seed_indices, interaction_gap=1):
    """Group only causal contacts; source swings seed the first interaction."""
    interaction_indices = set(seed_indices)
    interaction_indices.update(
        index
        for index in range(start_index, len(df))
        if df["low"].iloc[index] <= upper_bound
        and df["high"].iloc[index] >= lower_bound
    )
    interaction_indices = sorted(interaction_indices)

    if not interaction_indices:
        return []

    groups = [[interaction_indices[0]]]
    for index in interaction_indices[1:]:
        if index - groups[-1][-1] <= interaction_gap + 1:
            groups[-1].append(index)
        else:
            groups.append([index])

    return groups


def _zone_type(current_price, lower_bound, upper_bound):
    if current_price >= upper_bound:
        return "SUPPORT"
    if current_price <= lower_bound:
        return "RESISTANCE"

    center = (lower_bound + upper_bound) / 2
    return "SUPPORT" if current_price >= center else "RESISTANCE"


def _initial_role(cluster):
    high_count = sum(item["kind"] == "HIGH" for item in cluster)
    low_count = len(cluster) - high_count
    return "RESISTANCE" if high_count >= low_count else "SUPPORT"


def _role_timeline(df, initial_role, lower_bound, upper_bound, start_index, confirmation_bars):
    """Record every confirmed boundary break, retaining it through retests."""
    role = initial_role
    transitions = []
    consecutive = 0
    breakout_index = None

    for index in range(start_index, len(df)):
        close = df["close"].iloc[index]
        crossed_boundary = (
            close > upper_bound if role == "RESISTANCE" else close < lower_bound
        )
        if crossed_boundary:
            consecutive += 1
            if consecutive == 1:
                breakout_index = index
        else:
            consecutive = 0
            breakout_index = None

        if consecutive >= confirmation_bars:
            new_role = "SUPPORT" if role == "RESISTANCE" else "RESISTANCE"
            transitions.append({
                "confirmed_at": index,
                "breakout_index": breakout_index,
                "from": role,
                "to": new_role,
            })
            role = new_role
            consecutive = 0
            breakout_index = None

    return transitions


def _role_at_index(initial_role, transitions, index):
    role = initial_role
    for transition in transitions:
        if index < transition["confirmed_at"]:
            break
        role = transition["to"]
    return role


def _interaction_quality(df, groups, roles, center, reaction_window=5):
    rejections = []
    reactions = []
    volume_ratios = [] if "volume" in df.columns else None
    average_volume = (
        df["volume"].rolling(20, min_periods=1).mean()
        if volume_ratios is not None else None
    )

    for group_index, group in enumerate(groups):
        role = roles[group_index]
        group_rejections = []
        for index in group:
            high = df["high"].iloc[index]
            low = df["low"].iloc[index]
            candle_range = high - low
            if candle_range <= 0:
                continue

            close = df["close"].iloc[index]
            if role == "SUPPORT":
                group_rejections.append((close - low) / candle_range)
            else:
                group_rejections.append((high - close) / candle_range)

            if average_volume is not None:
                baseline_volume = average_volume.iloc[index]
                if baseline_volume > 0:
                    volume_ratios.append(df["volume"].iloc[index] / baseline_volume)

        if group_rejections:
            rejections.append(sum(group_rejections) / len(group_rejections))

        next_interaction = (
            groups[group_index + 1][0]
            if group_index + 1 < len(groups) else len(df)
        )
        reaction_end = min(
            next_interaction,
            group[-1] + reaction_window + 1,
        )
        future = df.iloc[group[-1] + 1:reaction_end]
        if future.empty:
            continue

        if role == "SUPPORT":
            reactions.append(max(0, future["high"].max() - center))
        else:
            reactions.append(max(0, center - future["low"].min()))

    rejection_quality = sum(rejections) / len(rejections) if rejections else 0
    reaction_magnitude = sum(reactions) / len(reactions) if reactions else 0
    volume_ratio = (
        sum(volume_ratios) / len(volume_ratios)
        if volume_ratios else None
    )
    return rejection_quality, reaction_magnitude, volume_ratio


def _strength(df, groups, initial_role, transitions, center, zone_width, max_width):
    roles = [
        _role_at_index(initial_role, transitions, group[-1])
        for group in groups
    ]
    touches = len(groups)
    touch_score = min(45, touches * 15)
    last_touch = groups[-1][-1]
    recency_score = 15 * (last_touch + 1) / len(df)
    rejection, reaction, volume_ratio = _interaction_quality(
        df, groups, roles, center,
    )
    typical_range = _trailing_atr(df, len(df) - 1) or max(center * 0.001, 1e-9)
    rejection_score = min(20, rejection * 20)
    reaction_score = min(15, (reaction / typical_range) * 5)
    volume_score = (
        min(5, max(0, volume_ratio - 1) * 5)
        if volume_ratio is not None else 0
    )
    width_ratio = zone_width / max_width if max_width > 0 else 1
    width_penalty = min(25, max(0, width_ratio - 0.5) * 30)

    return float(round(min(
        100,
        touch_score + recency_score + rejection_score + reaction_score + volume_score
        - width_penalty,
    ), 2))


def _last_touch_value(df, index):
    if "timestamp" in df.columns:
        return df["timestamp"].iloc[index]
    return index


def _distance_from_price(current_price, lower_bound, upper_bound):
    if lower_bound <= current_price <= upper_bound:
        return 0.0, True
    if current_price < lower_bound:
        return ((lower_bound - current_price) / current_price) * 100, False
    return ((current_price - upper_bound) / current_price) * 100, False


def _materially_overlaps(first, second):
    overlap = max(
        0,
        min(first["upper_bound"], second["upper_bound"])
        - max(first["lower_bound"], second["lower_bound"]),
    )
    if overlap == 0:
        return False

    narrower_width = min(
        first["upper_bound"] - first["lower_bound"],
        second["upper_bound"] - second["lower_bound"],
    )
    return overlap / narrower_width >= MATERIAL_OVERLAP_RATIO


def _zone_priority(zone):
    return zone["strength"] - min(20, zone["distance_from_price"])


def _resolve_overlaps(zones):
    """Keep the best representation of materially overlapping like-kind zones."""
    resolved = []

    for zone in sorted(zones, key=_zone_priority, reverse=True):
        redundant = any(
            zone["type"] == existing["type"]
            and zone["source_type"] == existing["source_type"]
            and _materially_overlaps(zone, existing)
            for existing in resolved
        )
        if not redundant:
            resolved.append(zone)

    return resolved


def detect_support_resistance(
    df,
    swing_window=DEFAULT_SWING_WINDOW,
    atr_column="ATR",
    atr_multiplier=DEFAULT_ATR_MULTIPLIER,
    fallback_tolerance_pct=DEFAULT_FALLBACK_TOLERANCE_PCT,
    confirmation_bars=DEFAULT_CONFIRMATION_BARS,
):
    """Return structured zones; invalid input raises ``ValueError``.

    This function is intentionally independent from reports and decisions. It
    accepts a project-standard OHLCV DataFrame, though volume is optional.
    """
    if swing_window < 1:
        raise ValueError("swing_window must be at least 1.")
    if confirmation_bars < 1:
        raise ValueError("confirmation_bars must be at least 1.")

    df = _validate_data(df)
    insufficient = _empty_zones_if_insufficient(df, swing_window)
    if insufficient is not None:
        return insufficient

    swings = _swing_points(df, swing_window)
    for swing in swings:
        swing["tolerance"] = _swing_tolerance(
            df,
            swing,
            atr_column,
            atr_multiplier,
            fallback_tolerance_pct,
        )

    current_price = float(df["close"].iloc[-1])
    zones = []
    for cluster in _cluster_swings(swings):
        lower_bound, upper_bound, center = _zone_bounds(cluster)
        # Final bounds depend on every member of a cluster, so they cannot be
        # exposed before the last member has been confirmed.
        activation_index = max(item["confirmed_at"] for item in cluster)
        groups = _interaction_groups(
            df,
            lower_bound,
            upper_bound,
            activation_index,
            [item["index"] for item in cluster],
        )
        if not groups:
            continue

        initial_role = _initial_role(cluster)
        transitions = _role_timeline(
            df,
            initial_role,
            lower_bound,
            upper_bound,
            activation_index,
            confirmation_bars,
        )
        transition_metadata = [
            {
                **transition,
                "activation_index": transition["confirmed_at"],
                "activation_timestamp": _last_touch_value(
                    df, transition["confirmed_at"],
                ),
                "breakout_timestamp": _last_touch_value(
                    df, transition["breakout_index"],
                ),
                "breakout_price": float(
                    df["close"].iloc[transition["breakout_index"]],
                ),
                "direction": (
                    "BULLISH" if transition["to"] == "SUPPORT"
                    else "BEARISH"
                ),
            }
            for transition in transitions
        ]
        zone_type = _zone_type(current_price, lower_bound, upper_bound)
        max_width = _max_zone_width(cluster)
        strength = _strength(
            df,
            groups,
            initial_role,
            transitions,
            center,
            upper_bound - lower_bound,
            max_width,
        )
        last_touch_index = groups[-1][-1]
        distance_from_price, inside_zone = _distance_from_price(
            current_price,
            lower_bound,
            upper_bound,
        )
        reasons = [
            f"{len(cluster)} confirmed swing point(s) were clustered into this zone.",
            f"{len(groups)} independent market interaction(s) touched the zone.",
            f"Zone is {distance_from_price:.2f}% from the current price.",
        ]
        if transitions:
            reasons.append(
                f"{len(transitions)} confirmed role reversal(s) occurred."
            )

        zones.append({
            "type": zone_type,
            "lower_bound": round(lower_bound, 8),
            "upper_bound": round(upper_bound, 8),
            "center": round(center, 8),
            "strength": strength,
            "touches": len(groups),
            "last_touch": _last_touch_value(df, last_touch_index),
            "distance_from_price": round(distance_from_price, 4),
            "inside_zone": inside_zone,
            "reasons": reasons,
            "role_reversal": bool(transitions),
            "source_type": cluster[0]["kind"],
            "activation_index": activation_index,
            "activation_timestamp": _last_touch_value(df, activation_index),
            "role_transitions": transition_metadata,
        })

    return sorted(
        _resolve_overlaps(zones),
        key=lambda zone: zone["strength"],
        reverse=True,
    )
