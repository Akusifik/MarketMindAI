from exchanges.manager import get_candles
from data.converter import candles_to_dataframe
from analysis.analyzer import analyze_market
from analysis.report import generate_report


class Market:

    def __init__(self, symbol, timeframe="1h", limit=200):
        self.symbol = symbol
        self.timeframe = timeframe
        self.limit = limit

        self.df = None
        self.result = None

        self.multi_results = None
        self.consensus = None

    def load_data(self):
        candles = get_candles(
            self.symbol,
            self.timeframe,
            self.limit
        )

        self.df = candles_to_dataframe(candles)

    def analyze(self, multi_timeframe=False):

        if multi_timeframe:

            from analysis.multi_timeframe import analyze_multi_timeframe
            from analysis.timeframe_consensus import analyze_consensus

            self.multi_results = analyze_multi_timeframe(self.symbol)
            self.consensus = analyze_consensus(self.multi_results)

        else:

            self.df, self.result = analyze_market(self.df)

    def report(self):
        return generate_report(self.df, self.result)