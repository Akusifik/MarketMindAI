def format_reasons(analysis):
    if not analysis.get("reasons"):
        return ""

    return "\n".join(
        f"  • {reason}"
        for reason in analysis["reasons"]
    )


def format_support_resistance(zones, max_zones=3):
    if not zones:
        return "No meaningful support or resistance zones detected."

    relevant_zones = sorted(
        zones,
        key=lambda zone: (abs(zone["distance_from_price"]), -zone["strength"]),
    )[:max_zones]
    lines = []

    for zone in relevant_zones:
        line = (
            f"{zone['type']} | "
            f"{zone['lower_bound']:.2f} - {zone['upper_bound']:.2f} | "
            f"Strength: {zone['strength']:.1f} | "
            f"Touches: {zone['touches']} | "
            f"Distance: {abs(zone['distance_from_price']):.2f}%"
        )
        if zone.get("role_reversal"):
            line += " | Role reversal"
        lines.append(line)

    return "\n".join(lines)


def format_market_structure(structure):
    if not structure:
        return "No market structure data available."

    lines = [
        f"Trend: {structure['trend']} | Strength: {structure['strength']:.1f}",
    ]
    if structure.get("last_event"):
        lines.append(f"Last event: {structure['last_event']['type']}")
    else:
        lines.append("Last event: None")
    return "\n".join(lines)


def format_price_action(price_action):
    if not price_action:
        return "No price action data available."

    return (
        f"Bias: {price_action['bias']} | Strength: {price_action['strength']:.1f}\n"
        f"Patterns: {len(price_action['patterns'])} | "
        f"Rejections: {len(price_action['rejections'])} | "
        f"Zone events: {len(price_action['zone_events'])}"
    )


def format_volume_analysis(volume_analysis):
    if not volume_analysis:
        return "No volume analysis data available."
    breakout = volume_analysis["breakout_confirmation"]
    return (
        f"Bias: {volume_analysis['bias']} | Strength: {volume_analysis['strength']:.1f}\n"
        f"Volume: {volume_analysis['volume_state']} | "
        f"Relation: {volume_analysis['price_volume_relation']}\n"
        f"Breakout confirmation: {breakout['level']} | "
        f"Divergences: {len(volume_analysis['divergences'])} | "
        f"Flow heuristic: {volume_analysis['accumulation_distribution']['state']}"
    )


def generate_report(df, result):
    return f"""
==============================
MARKETMIND AI REPORT
==============================

Текущая цена : {df['close'].iloc[-1]:.2f}

--------------------------------
EMA
--------------------------------
EMA20  : {df['EMA20'].iloc[-1]:.2f}
EMA50  : {df['EMA50'].iloc[-1]:.2f}
EMA200 : {df['EMA200'].iloc[-1]:.2f}

--------------------------------
RSI
--------------------------------
RSI : {result.rsi:.2f}
Статус : {result.rsi_analysis["message"]}

Почему:
{format_reasons(result.rsi_analysis)}

--------------------------------
MACD
--------------------------------
MACD : {result.macd:.2f}
Signal : {result.signal:.2f}
Histogram : {result.histogram:.2f}
Статус : {result.macd_analysis["message"]}

Почему:
{format_reasons(result.macd_analysis)}

--------------------------------
Bollinger Bands
--------------------------------
Верхняя : {result.upper_band:.2f}
Средняя : {result.middle_band:.2f}
Нижняя  : {result.lower_band:.2f}
Статус : {result.bollinger_analysis["message"]}

Почему:
{format_reasons(result.bollinger_analysis)}

--------------------------------
ATR
--------------------------------
ATR : {result.atr:.2f}
Статус : {result.atr_analysis["message"]}

Почему:
{format_reasons(result.atr_analysis)}

--------------------------------
ADX
--------------------------------
ADX : {result.adx:.2f}
Статус : {result.adx_analysis["message"]}

Почему:
{format_reasons(result.adx_analysis)}

--------------------------------
OBV
--------------------------------
OBV : {result.obv:.2f}
Статус : {result.obv_analysis["message"]}

Почему:
{format_reasons(result.obv_analysis)}

--------------------------------
VWAP
--------------------------------
VWAP : {result.vwap:.2f}
Статус : {result.vwap_analysis["message"]}

Почему:
{format_reasons(result.vwap_analysis)}

--------------------------------
RVOL
--------------------------------
RVOL : {result.rvol:.2f}
Статус : {result.rvol_analysis["message"]}

Почему:
{format_reasons(result.rvol_analysis)}

--------------------------------
Тренд
--------------------------------
{result.trend}

--------------------------------
Итог
--------------------------------
Общий Score : {result.score}
Уверенность : {result.confidence}%
Оценка рынка : {result.market_status}

--------------------------------
Решение AI
--------------------------------
Действие : {result.decision["action"]}

Почему:
{chr(10).join(f"  • {reason}" for reason in result.decision["reasons"])}
--------------------------------
SUPPORT & RESISTANCE
--------------------------------
{format_support_resistance(result.support_resistance_zones)}
--------------------------------
MARKET STRUCTURE
--------------------------------
{format_market_structure(result.market_structure)}
--------------------------------
PRICE ACTION
--------------------------------
{format_price_action(result.price_action)}
--------------------------------
VOLUME ANALYSIS
--------------------------------
{format_volume_analysis(result.volume_analysis)}
"""
