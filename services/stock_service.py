from __future__ import annotations

from models import Company, StockPricePoint, StockSeries
import yfinance as yf


_LOOKBACK_TO_PERIOD = {
    "1M": "1mo",
    "3M": "3mo",
    "6M": "6mo",
    "1Y": "1y",
}


def _normalize_period(price_lookback: str) -> str:
    return _LOOKBACK_TO_PERIOD.get((price_lookback or "3M").upper(), "3mo")


def fetch_stock_series(companies: list[Company], price_lookback: str) -> list[StockSeries]:
    period = _normalize_period(price_lookback)
    stock_series: list[StockSeries] = []

    for company in companies:
        ticker = company.ticker.upper()
        points: list[StockPricePoint] = []

        try:
            history = yf.Ticker(ticker).history(
                period=period,
                interval="1d",
                auto_adjust=False,
                actions=False,
            )
        except Exception:
            history = None

        if history is not None and not history.empty and "Close" in history.columns:
            closes = history["Close"].dropna()
            base_close = float(closes.iloc[0]) if len(closes) else 0.0

            for index, close in closes.items():
                date_str = index.date().isoformat() if hasattr(index, "date") else str(index)[:10]
                close_value = float(close)
                indexed_close = round((close_value / base_close) * 100.0, 4) if base_close else 0.0
                points.append(
                    StockPricePoint(
                        date=date_str,
                        close=round(close_value, 4),
                        indexed_close=indexed_close,
                    )
                )

        stock_series.append(
            StockSeries(
                ticker=ticker,
                company_name=company.name,
                points=points,
            )
        )

    return stock_series
