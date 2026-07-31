# MarketMindAI Architecture

## Purpose

MarketMindAI is a command-line technical-analysis prototype for cryptocurrency markets. It retrieves OHLCV candles from a configured exchange, calculates technical indicators, interprets them as signals, combines those signals into a market assessment and decision, and prints a human-readable report.

## Runtime Data Flow

```text
main.py
  -> config.py
  -> core.market.Market
       -> exchanges.manager
            -> selected CCXT exchange adapter
       -> data.converter.candles_to_dataframe
       -> analysis.analyzer.analyze_market
            -> indicators/*
            -> analysis/*_analysis.py and trend.py
            -> analysis.evaluate_market
            -> analysis.decision_engine
       -> analysis.report.generate_report
  -> terminal output
```

`main.py` constructs a `Market` with the configured symbol, timeframe, and candle limit. `Market.load_data()` fetches OHLCV rows and normalizes them into a pandas DataFrame. `Market.analyze()` enriches that DataFrame with indicator columns and stores an `AnalysisResult`. Finally, `Market.report()` creates the text output.

## Project Structure

```text
MarketMindAI/
├── main.py                 # CLI entry point
├── config.py               # Exchange, symbol, timeframe, and candle settings
├── requirements.txt        # Runtime dependencies
├── core/
│   └── market.py           # Main application facade and orchestration
├── exchanges/
│   ├── manager.py          # Chooses adapter from configuration
│   ├── bybit.py            # Bybit CCXT adapter (candles and ticker)
│   ├── binance.py          # Binance CCXT adapter (ticker only at present)
│   └── okx.py              # Reserved adapter placeholder
├── data/
│   └── converter.py        # Converts CCXT OHLCV arrays to DataFrames
├── indicators/             # Raw technical-indicator calculations
├── analysis/               # Signal interpretation, evaluation, decision, reporting
├── logs/
│   └── logger.py           # Logging configuration
├── ai/                     # Reserved for future AI capabilities
└── database/               # Reserved for persistence capabilities
```

## Module Responsibilities

### Configuration and entry point

- `main.py` runs a single market analysis and prints its report.
- `config.py` supplies global defaults: exchange, trading pair, timeframe, and number of candles.
- `logs/logger.py` configures the named application logger.

### Core

- `core.market.Market` owns the symbol, timeframe, candle limit, source DataFrame, analysis result, and optional multi-timeframe results. It provides `load_data()`, `analyze()`, and `report()`.

### Exchange and data boundary

- `exchanges.manager` selects an exchange module according to `config.EXCHANGE`.
- `exchanges.bybit` wraps CCXT's Bybit client and provides ticker and OHLCV retrieval.
- `exchanges.binance` currently provides ticker retrieval only.
- `data.converter` assigns OHLCV column names (`timestamp`, `open`, `high`, `low`, `close`, `volume`) and converts millisecond timestamps to pandas datetimes.

### Indicator calculations

Each module in `indicators/` calculates numeric series from the market DataFrame:

- `ema.py`: exponential moving averages.
- `rsi.py`: relative strength index.
- `macd.py`: MACD, signal line, and histogram.
- `bollinger.py`: upper, middle, and lower Bollinger Bands.
- `atr.py`: average true range.
- `adx.py`: average directional index.
- `obv.py`: on-balance volume.
- `vwap.py`: volume-weighted average price over the supplied data window.
- `rvol.py`: relative volume against a rolling average.
- `supertrend.py`: SuperTrend line and direction.

### Analysis pipeline

- `analysis.analyzer` is the active coordinator. It invokes every indicator calculator, stores its output as DataFrame columns, interprets the latest values, evaluates the market, and generates a decision.
- `analysis.result` defines `AnalysisResult`, the mutable result object consumed by evaluation and reporting. It also contains currently unused generic result classes for a future structured model.
- `analysis.trend` classifies price and EMA alignment into a trend label.
- `analysis.rsi_analysis`, `macd_analysis`, `bollinger_analysis`, `atr_analysis`, `adx_analysis`, `volume_analysis`, `vwap_analysis`, and `rvol_analysis` transform raw values into status, signal, score, message, and explanatory reasons.
- `analysis.supertrend_analysis` converts SuperTrend direction into a bullish/bearish label.

### Evaluation and decisions

- `analysis.evaluate_market` is the active market evaluator. It applies configured weights to indicator scores, counts BUY/SELL/NEUTRAL signals, assigns market status, produces a summary, and derives a simple confidence value.
- `analysis.decision_engine` converts selected signal agreement into a `BUY`, `SELL`, or `HOLD` action with reasons.

### Reporting and multi-timeframe analysis

- `analysis.report` formats the active single-timeframe result as a terminal report.
- `analysis.multi_timeframe` runs analyses for the fixed 1d, 4h, 1h, and 15m intervals.
- `analysis.timeframe_consensus` produces a majority decision and average confidence across those intervals.

### Legacy or inactive paths

The following modules are present but are not used by the active `main.py` execution flow:

- `analysis.analyze_market`: an earlier unweighted score and market-status aggregator.
- `analysis.score`: alternative score and market-status logic that also includes trend and SuperTrend.
- `analysis.volume_engine`: alternative aggregation of OBV, VWAP, and RVOL.

## Current Design Notes

- The analyzer currently mutates the market DataFrame in place by adding indicator columns.
- Adding a new indicator requires wiring it into several places: indicator calculation, result population, evaluation, and report formatting.
- The active evaluator does not include EMA trend or SuperTrend in its weighted score, even though both are calculated.
- The multi-timeframe path stores consensus separately from the normal single-timeframe `result`; it therefore needs a dedicated output path if it is to be reported directly.

## Extension Points

- Add a complete exchange adapter implementing the same candle-fetching interface as Bybit.
- Add indicator modules plus their signal interpreters, then register them in the analysis pipeline.
- Replace the mutable result object and repeated dictionaries with typed signal/result models.
- Move scoring rules and thresholds into a single strategy configuration or registry.
- Introduce persistence under `database/` and model/AI integrations under `ai/` when those features are implemented.
