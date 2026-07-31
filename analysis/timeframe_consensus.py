TIMEFRAME_WEIGHTS = {
    "1d": 4,
    "4h": 3,
    "1h": 2,
    "15m": 1,
}

HIGHER_TIMEFRAMES = ("1d", "4h")
LOWER_TIMEFRAMES = ("1h", "15m")
DIRECTIONAL_ACTIONS = ("BUY", "SELL")
MIN_DIRECTIONAL_ADVANTAGE = 2


def _vote_totals(entries):
    return {
        action: sum(
            entry["weight"]
            for entry in entries
            if entry["action"] == action
        )
        for action in ("BUY", "SELL", "HOLD")
    }


def _weighted_action(entries):
    """Return a clear directional leader; weak directional leads become HOLD."""
    votes = _vote_totals(entries)
    highest_vote = max(votes.values(), default=0)
    leaders = [
        action
        for action, vote in votes.items()
        if vote == highest_vote
    ]

    if len(leaders) != 1:
        return "HOLD"

    leader = leaders[0]
    if leader == "HOLD":
        return "HOLD"

    runner_up = max(
        vote
        for action, vote in votes.items()
        if action != leader
    )

    if highest_vote - runner_up < MIN_DIRECTIONAL_ADVANTAGE:
        return "HOLD"

    return leader


def _group_entries(summary, timeframes):
    return [
        entry
        for entry in summary
        if entry["timeframe"].lower() in timeframes
    ]


def _has_directional_opposition(higher_entries, lower_entries):
    higher_directions = {
        entry["action"]
        for entry in higher_entries
        if entry["action"] in DIRECTIONAL_ACTIONS
    }
    lower_directions = {
        entry["action"]
        for entry in lower_entries
        if entry["action"] in DIRECTIONAL_ACTIONS
    }

    return bool(
        ("BUY" in higher_directions and "SELL" in lower_directions)
        or ("SELL" in higher_directions and "BUY" in lower_directions)
    )


def _calculate_confidence(summary, overall, correction, conflict):
    total_weight = sum(entry["weight"] for entry in summary)
    weighted_confidence = sum(
        entry["confidence"] * entry["weight"]
        for entry in summary
    ) / total_weight

    agreement_weight = sum(
        entry["weight"]
        for entry in summary
        if entry["action"] == overall
    )
    agreement_ratio = agreement_weight / total_weight
    fully_aligned = agreement_ratio == 1

    if fully_aligned:
        adjustment = 5
    else:
        adjustment = -round((1 - agreement_ratio) * 30)

    if correction:
        adjustment -= 10
    elif conflict:
        adjustment -= 15

    return max(0, min(100, round(weighted_confidence + adjustment)))


def _explain(overall, higher_action, lower_action, conflict, correction, votes):
    parts = [
        "Weighted votes: "
        f"BUY {votes['BUY']}, SELL {votes['SELL']}, HOLD {votes['HOLD']}."
    ]

    if overall == "HOLD":
        parts.append(
            "No directional action reached the required weighted advantage, "
            "so the final decision is HOLD."
        )
    else:
        parts.append(
            f"The final decision is {overall} because it has a clear "
            "weighted advantage."
        )

    parts.append(
        "Higher timeframes (1D and 4H) are "
        f"{higher_action}; lower timeframes (1H and 15M) are {lower_action}."
    )

    if correction:
        parts.append(
            "The clear lower-timeframe opposition is classified as a pullback "
            "against the higher-timeframe trend."
        )
    elif conflict:
        parts.append(
            "Timeframes disagree, but the groups are not clear enough to "
            "classify the disagreement as a pullback."
        )
    else:
        parts.append("The timeframe groups are aligned or neutral.")

    return " ".join(parts)


def _build_summary(results):
    summary = []

    for timeframe, result in results.items():
        action = result.decision.get("action", "HOLD").upper()
        if action not in ("BUY", "SELL", "HOLD"):
            action = "HOLD"

        summary.append({
            "timeframe": timeframe,
            "action": action,
            "confidence": result.confidence,
            "weight": TIMEFRAME_WEIGHTS.get(timeframe.lower(), 1),
        })

    return summary


def analyze_consensus(results):
    """Combine timeframe decisions with weighted voting and alignment checks."""
    if not results:
        raise ValueError("At least one timeframe result is required.")

    summary = _build_summary(results)
    votes = _vote_totals(summary)
    overall = _weighted_action(summary)

    higher_entries = _group_entries(summary, HIGHER_TIMEFRAMES)
    lower_entries = _group_entries(summary, LOWER_TIMEFRAMES)
    higher_action = _weighted_action(higher_entries)
    lower_action = _weighted_action(lower_entries)

    correction = (
        higher_action in DIRECTIONAL_ACTIONS
        and lower_action in DIRECTIONAL_ACTIONS
        and higher_action != lower_action
    )
    conflict = (
        not correction
        and _has_directional_opposition(higher_entries, lower_entries)
    )
    confidence = _calculate_confidence(
        summary,
        overall,
        correction,
        conflict,
    )

    return {
        "overall": overall,
        "confidence": confidence,
        "summary": summary,
        "weighted_votes": votes,
        "higher_timeframe_action": higher_action,
        "lower_timeframe_action": lower_action,
        "conflict": conflict,
        "correction": correction,
        "explanation": _explain(
            overall,
            higher_action,
            lower_action,
            conflict,
            correction,
            votes,
        ),
    }
