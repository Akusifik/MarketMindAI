class IndicatorResult:
    def __init__(
        self,
        value=None,
        signal="",
        score=0,
        message="",
        reasons=None
    ):
        self.value = value
        self.signal = signal
        self.score = score
        self.message = message
        self.reasons = reasons if reasons else []


class EngineResult:
    def __init__(self, signal="", score=0, summary="", details=None):
        self.signal = signal
        self.score = score
        self.summary = summary
        self.details = details if details is not None else []


class AnalysisResult:

    def __init__(self):

        self.score = 0
        self.confidence = 0

        self.market_status = ""
        self.summary = []

        self.trend = ""

        self.signals = []

        self.indicators = {}
        self.engines = {}
        self.metadata = {}
        self.decision = {}
        self.support_resistance_zones = []
        self.market_structure = {}
        self.price_action = {}
        self.volume_analysis = {}

        # --------------------------
        # Старые поля (временно!)
        # --------------------------

        self.rsi = 0
        self.macd = 0
        self.signal = 0
        self.histogram = 0

        self.upper_band = 0
        self.middle_band = 0
        self.lower_band = 0

        self.atr = 0
        self.adx = 0
        self.obv = 0
        self.vwap = 0
        self.rvol = 0

        self.rsi_analysis = {}
        self.macd_analysis = {}
        self.bollinger_analysis = {}
        self.atr_analysis = {}
        self.adx_analysis = {}
        self.obv_analysis = {}
        self.vwap_analysis = {}
        self.rvol_analysis = {}


