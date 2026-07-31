"""Causal, descriptive interpretation of normalized order-flow data.

This module is deliberately provider-neutral and analysis-only. It consumes
``orderflow`` models and does not create exchange connections or trade signals.
"""

from statistics import median

from orderflow.models import OrderBookSnapshot, Trade
from orderflow.order_book import calculate_order_book_metrics
from orderflow.trades import calculate_trade_flow, cumulative_delta


def _ordered_snapshots(snapshot, history):
    snapshots = list(history or []) + [snapshot]
    if any(not isinstance(item, OrderBookSnapshot) for item in snapshots):
        raise ValueError("snapshot history must contain OrderBookSnapshot instances.")
    if any(snapshots[index].timestamp >= snapshots[index + 1].timestamp for index in range(len(snapshots) - 1)):
        raise ValueError("Snapshot history must be strictly chronological.")
    sequences = [item.sequence for item in snapshots]
    known_sequences = [item for item in sequences if item is not None]
    if known_sequences and any(item is None for item in sequences):
        raise ValueError("Snapshot sequences must be supplied consistently when available.")
    if known_sequences and any(sequences[index] >= sequences[index + 1] for index in range(len(sequences) - 1)):
        raise ValueError("Snapshot sequences must be strictly increasing.")
    return snapshots


def _matching_levels(snapshot, side, price, tolerance):
    levels = snapshot.bids if side == "BID" else snapshot.asks
    return [level for level in levels if abs(level.price - price) <= tolerance]


def _wall_levels(snapshot, mid, snapshots=()):
    """Return only persistent walls against a causal local-depth baseline."""
    snapshots = list(snapshots or [snapshot])
    walls = []
    for side, levels in (("BID", snapshot.bids), ("ASK", snapshot.asks)):
        if len(levels) < 2:
            continue
        for level in levels:
            tolerance = max(level.price * .0005, 1e-9)
            observations = []
            local_depth = []
            for observed in snapshots:
                observed_levels = observed.bids if side == "BID" else observed.asks
                if observed_levels:
                    local_depth.append(median(item.quantity for item in observed_levels))
                matches = _matching_levels(observed, side, level.price, tolerance)
                if matches:
                    observations.append((observed, max(matches, key=lambda item: item.quantity)))
            baseline = median(local_depth) if local_depth else 0
            persistence_ratio = len(observations) / len(snapshots) if snapshots else 0
            relative_size = level.quantity / baseline if baseline else 0
            if relative_size >= 3 and len(observations) >= 2 and persistence_ratio >= .6:
                walls.append({
                    "side": side, "price": level.price, "quantity": level.quantity,
                    "relative_size": round(relative_size, 2),
                    "distance_from_mid": abs(level.price - mid) / mid * 100 if mid else None,
                    "persistence": {
                        "observations": len(observations),
                        "first_seen": observations[0][0].timestamp,
                        "last_seen": observations[-1][0].timestamp,
                        "persistence_ratio": round(persistence_ratio, 3),
                        "price_tolerance": tolerance,
                    },
                    "strength": min(70.0, round(15 + relative_size * 7 + persistence_ratio * 15, 2)),
                    "reasons": ["Displayed depth is persistently large relative to causal local depth."],
                })
    return walls


def _book_state(snapshot, top_n):
    metrics = calculate_order_book_metrics(snapshot, top_n)
    near_total = metrics["top_n_bid_depth"] + metrics["top_n_ask_depth"]
    near_imbalance = ((metrics["top_n_bid_depth"] - metrics["top_n_ask_depth"]) / near_total if near_total else 0.0)
    imbalance = metrics["imbalance"]
    if not snapshot.bids or not snapshot.asks:
        state = "INSUFFICIENT"
    elif imbalance >= .2 or near_imbalance >= .25:
        state = "BID_HEAVY"
    elif imbalance <= -.2 or near_imbalance <= -.25:
        state = "ASK_HEAVY"
    else:
        state = "BALANCED"
    return {**metrics, "near_mid_imbalance": near_imbalance, "state": state}


def _trade_pressure(trades):
    flow = calculate_trade_flow(trades)
    known = flow["buy_volume"] + flow["sell_volume"]
    if flow["trade_count"] < 2 or known == 0:
        pressure = "INSUFFICIENT"
    elif flow["buy_sell_imbalance"] >= .2:
        pressure = "BUY_PRESSURE"
    elif flow["buy_sell_imbalance"] <= -.2:
        pressure = "SELL_PRESSURE"
    else:
        pressure = "BALANCED"
    sizes = [trade.quantity for trade in trades]
    typical = median(sizes) if sizes else 0
    large_volume = sum(trade.quantity for trade in trades if typical and trade.quantity >= typical * 2)
    return {**flow, "pressure": pressure, "average_trade_size": flow["total_volume"] / flow["trade_count"] if flow["trade_count"] else 0.0, "large_trade_concentration": large_volume / flow["total_volume"] if flow["total_volume"] else 0.0}


def _cumulative_delta(trades):
    points = cumulative_delta(trades)
    if len(points) < 4:
        return {"direction": "INSUFFICIENT", "acceleration": "INSUFFICIENT", "price_relation": "INSUFFICIENT", "current": points[-1].cumulative_delta if points else 0.0, "points": points}
    current = points[-1].cumulative_delta
    direction = "POSITIVE" if current > 0 else "NEGATIVE" if current < 0 else "FLAT"
    split = len(points) // 2
    first_delta = points[split - 1].cumulative_delta - points[0].cumulative_delta
    second_delta = points[-1].cumulative_delta - points[split - 1].cumulative_delta
    acceleration = "ACCELERATING" if abs(second_delta) > abs(first_delta) * 1.2 else "DECELERATING" if abs(second_delta) < abs(first_delta) * .8 else "STEADY"
    prices = [trade.price for trade in trades]
    price_change = prices[-1] - prices[0]
    minimum_move = max(abs(prices[0]) * .0005, 1e-9)
    price_relation = "CONFIRMS" if abs(price_change) >= minimum_move and ((price_change > 0 and current > 0) or (price_change < 0 and current < 0)) else "DIVERGES" if abs(price_change) >= minimum_move and ((price_change > 0 and current < 0) or (price_change < 0 and current > 0)) else "NEUTRAL"
    return {"direction": direction, "acceleration": acceleration, "price_relation": price_relation, "current": current, "points": points}


def _near_zone(price, zones, zone_type):
    for zone in zones or []:
        if zone.get("type") == zone_type and zone["lower_bound"] * .995 <= price <= zone["upper_bound"] * 1.005:
            return zone
    return None


def _absorption(trades, walls, zones, book, snapshots):
    if len(snapshots) < 2:
        return {"type": "INSUFFICIENT", "strength": 0, "reasons": ["A previous snapshot is required to define the absorption observation window."]}
    previous, current = snapshots[-2:]
    interval_trades = [trade for trade in trades if previous.timestamp < trade.timestamp <= current.timestamp]
    flow = _trade_pressure(interval_trades)
    if flow["trade_count"] < 3 or flow["pressure"] == "INSUFFICIENT" or not interval_trades:
        return {"type": "NONE", "strength": 0, "reasons": ["Insufficient aggressive-flow evidence for absorption heuristic."]}
    known_volume = flow["buy_volume"] + flow["sell_volume"]
    displayed_depth = book["total_bid_depth"] + book["total_ask_depth"]
    if displayed_depth <= 0 or known_volume < displayed_depth * .05:
        return {"type": "NONE", "strength": 0, "reasons": ["Aggressive flow is too small relative to displayed local depth."]}
    start, end = interval_trades[0].price, interval_trades[-1].price
    micro_range = max(book["spread"] or 0, start * .0005)
    small_progress = abs(end - start) <= micro_range
    if not small_progress:
        return {"type": "NONE", "strength": 0, "reasons": ["Price progress was too large for an absorption heuristic."]}
    if flow["pressure"] == "BUY_PRESSURE" and (any(wall["side"] == "ASK" for wall in walls) or _near_zone(end, zones, "RESISTANCE")):
        return {"type": "POSSIBLE_BUY_ABSORPTION", "strength": 55, "reasons": ["Aggressive buying met limited upward progress near ask-side liquidity or resistance; heuristic only."]}
    if flow["pressure"] == "SELL_PRESSURE" and (any(wall["side"] == "BID" for wall in walls) or _near_zone(end, zones, "SUPPORT")):
        return {"type": "POSSIBLE_SELL_ABSORPTION", "strength": 55, "reasons": ["Aggressive selling met limited downward progress near bid-side liquidity or support; heuristic only."]}
    return {"type": "NONE", "strength": 0, "reasons": ["No matching liquidity context for absorption heuristic."]}


def _sweeps(snapshots, trades):
    if len(snapshots) < 3 or not trades:
        return []
    previous, current = snapshots[-2:]
    previous_walls = _wall_levels(previous, calculate_order_book_metrics(previous)["mid_price"], snapshots[:-1])
    prices = {"BID": {level.price for level in current.bids}, "ASK": {level.price for level in current.asks}}
    events = []
    for wall in previous_walls:
        interval_trades = [trade for trade in trades if previous.timestamp < trade.timestamp <= current.timestamp]
        relevant = [trade for trade in interval_trades if trade.price <= wall["price"] if wall["side"] == "BID"] if wall["side"] == "BID" else [trade for trade in interval_trades if trade.price >= wall["price"]]
        if not relevant:
            continue
        flow = _trade_pressure(relevant)
        crossed = bool(relevant)
        pressure_matches = (wall["side"] == "BID" and flow["pressure"] == "SELL_PRESSURE") or (wall["side"] == "ASK" and flow["pressure"] == "BUY_PRESSURE")
        if crossed and pressure_matches and wall["price"] not in prices[wall["side"]]:
            events.append({"type": "POSSIBLE_LIQUIDITY_SWEEP", "side": wall["side"], "price": wall["price"], "strength": 50, "actionable_timestamp": current.timestamp, "reasons": ["Price crossed a prior liquidity wall during aligned aggressive flow and the displayed level disappeared."]})
    return events


def _anomalies(snapshots, trades):
    if len(snapshots) < 3:
        return [{"type": "INSUFFICIENT", "reasons": ["At least three chronological snapshots are required for anomaly heuristics."]}]
    anomalies = []
    unavailable_reference = False

    if len(snapshots) >= 4:
        first, second, middle, last = snapshots[-4:]
        middle_walls = _wall_levels(middle, calculate_order_book_metrics(middle)["mid_price"], [first, second, middle])
        first_prices = {level.price for level in first.bids + first.asks}
        last_prices = {level.price for level in last.bids + last.asks}
        for wall in middle_walls:
            executions = [trade for trade in trades if abs(trade.price - wall["price"]) <= wall["persistence"]["price_tolerance"]]
            middle_metrics, last_metrics = calculate_order_book_metrics(middle), calculate_order_book_metrics(last)
            middle_reference = middle_metrics["mid_price"]
            last_reference = last_metrics["mid_price"]
            if middle_reference is None or last_reference is None:
                middle_reference = middle_metrics["best_bid"] if wall["side"] == "BID" else middle_metrics["best_ask"]
                last_reference = last_metrics["best_bid"] if wall["side"] == "BID" else last_metrics["best_ask"]
            if middle_reference is None or last_reference is None:
                unavailable_reference = True
                continue
            reference_move = abs(last_reference - middle_reference) / wall["price"]
            if wall["price"] not in first_prices and wall["price"] not in last_prices and not executions and reference_move <= .005:
                anomalies.append({"type": "POSSIBLE_SPOOFING", "strength": 25, "evidence": ["A persistent large displayed level disappeared without execution or meaningful reference-price movement; low-confidence heuristic."]})

    for side in ("BID", "ASK"):
        candidate_prices = set()
        for snap in snapshots:
            candidate_prices.update(level.price for level in (snap.bids if side == "BID" else snap.asks))
        for price in candidate_prices:
            tolerance = max(price * .0005, 1e-9)
            cycles = []
            for before, after in zip(snapshots, snapshots[1:]):
                before_levels = _matching_levels(before, side, price, tolerance)
                after_levels = _matching_levels(after, side, price, tolerance)
                executions = [trade for trade in trades if before.timestamp < trade.timestamp <= after.timestamp and abs(trade.price - price) <= tolerance]
                if before_levels and after_levels and executions:
                    before_quantity = max(level.quantity for level in before_levels)
                    after_quantity = max(level.quantity for level in after_levels)
                    if after_quantity >= before_quantity * .8:
                        cycles.append({"before": before.timestamp, "after": after.timestamp, "executions": len(executions)})
            if len(cycles) >= 2:
                anomalies.append({"type": "POSSIBLE_ICEBERG", "strength": 25, "evidence": ["Multiple execution-to-refill cycles were observed at one displayed level; low-confidence heuristic."], "price": price, "side": side, "refill_cycles": cycles})
                break
    if unavailable_reference and not anomalies:
        return [{"type": "INSUFFICIENT", "reasons": ["No usable market reference was available to evaluate disappearing liquidity."]}]
    return anomalies or [{"type": "NONE", "reasons": ["No low-confidence display anomaly was observed."]}]


def analyze_order_flow(snapshot, trades=(), snapshot_history=(), *, support_resistance_zones=None, market_structure=None, price_action=None, volume_analysis=None, top_n=5):
    """Return causal descriptive order-flow analysis for a normalized snapshot."""
    snapshots = _ordered_snapshots(snapshot, snapshot_history)
    trades = list(trades)
    if any(not isinstance(trade, Trade) for trade in trades):
        raise ValueError("trades must contain normalized Trade instances.")
    if any(trades[index].timestamp > trades[index + 1].timestamp for index in range(len(trades) - 1)):
        raise ValueError("Trades must be chronological for causal analysis.")
    if any(trade.timestamp > snapshot.timestamp for trade in trades):
        raise ValueError("Trades cannot be later than the analysed snapshot.")
    book = _book_state(snapshot, top_n)
    walls = _wall_levels(snapshot, book["mid_price"], snapshots)
    flow = _trade_pressure(trades)
    delta = _cumulative_delta(trades)
    absorption = _absorption(trades, walls, support_resistance_zones, book, snapshots)
    sweeps = _sweeps(snapshots, trades)
    anomalies = _anomalies(snapshots, trades)

    book_score = 25 if book["state"] in {"BID_HEAVY", "ASK_HEAVY"} else 0
    flow_score = 35 if flow["pressure"] in {"BUY_PRESSURE", "SELL_PRESSURE"} else 0
    # Delta and aggressive flow describe the same trades: one shared cap.
    flow_score = max(flow_score, 35 if delta["direction"] in {"POSITIVE", "NEGATIVE"} and delta["price_relation"] == "CONFIRMS" else 0)
    bullish = (book_score if book["state"] == "BID_HEAVY" else 0) + (flow_score if flow["pressure"] == "BUY_PRESSURE" else 0)
    bearish = (book_score if book["state"] == "ASK_HEAVY" else 0) + (flow_score if flow["pressure"] == "SELL_PRESSURE" else 0)
    if absorption["type"] == "POSSIBLE_BUY_ABSORPTION": bearish = max(bearish, 40)
    if absorption["type"] == "POSSIBLE_SELL_ABSORPTION": bullish = max(bullish, 40)
    if delta["price_relation"] == "DIVERGES":
        if delta["direction"] == "POSITIVE": bullish = max(bullish, 25)
        elif delta["direction"] == "NEGATIVE": bearish = max(bearish, 25)
    bias = "BULLISH" if bullish - bearish >= 15 else "BEARISH" if bearish - bullish >= 15 else "NEUTRAL"
    strength = float(min(75, max(bullish, bearish)))
    reasons = [f"Book state: {book['state']}.", f"Aggressive trade pressure: {flow['pressure']}.", f"Cumulative delta: {delta['direction']} ({delta['price_relation']})."]
    return {"bias": bias, "strength": strength, "book_state": book, "liquidity_walls": walls, "trade_flow": flow, "cumulative_delta": delta, "absorption": absorption, "sweeps": sweeps, "anomalies": anomalies, "reasons": reasons}
