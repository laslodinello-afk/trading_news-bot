"""
fetch_actual_result() : vérifie l'ordre de la cascade de sources pour le
"résultat réel" (Alpha Vantage -> EIA -> FMP -> titres RSS -> recherche web
Gemini en tout dernier recours). Aucun réseau réel — chaque étage est mocké.
"""
from datetime import datetime, timezone
from unittest.mock import patch

import calendar_fetcher


def test_fetch_actual_result_short_circuits_on_alphavantage():
    with patch("calendar_fetcher.fetch_actual_from_alphavantage", return_value="180K") as av, \
         patch("calendar_fetcher.fetch_actual_from_eia") as eia, \
         patch("calendar_fetcher._fetch_actual_from_fmp") as fmp, \
         patch("calendar_fetcher.fetch_actual_from_news") as news, \
         patch("calendar_fetcher.ai_analyzer.search_actual_result") as search:
        result = calendar_fetcher.fetch_actual_result("USD", "NFP", datetime(2026, 8, 1, tzinfo=timezone.utc))

    assert result == "180K"
    av.assert_called_once()
    eia.assert_not_called()
    fmp.assert_not_called()
    news.assert_not_called()
    search.assert_not_called()


def test_fetch_actual_result_falls_through_to_grounded_search_last():
    with patch("calendar_fetcher.fetch_actual_from_alphavantage", return_value=None), \
         patch("calendar_fetcher.fetch_actual_from_eia", return_value=None), \
         patch("calendar_fetcher._fetch_actual_from_fmp", return_value=None), \
         patch("calendar_fetcher.fetch_actual_from_news", return_value=None), \
         patch("calendar_fetcher.ai_analyzer.search_actual_result", return_value="3.2%") as search:
        result = calendar_fetcher.fetch_actual_result("EUR", "Core CPI Flash Estimate y/y", datetime(2026, 8, 1, tzinfo=timezone.utc))

    assert result == "3.2%"
    search.assert_called_once()


def test_fetch_actual_result_returns_none_when_every_source_fails():
    with patch("calendar_fetcher.fetch_actual_from_alphavantage", return_value=None), \
         patch("calendar_fetcher.fetch_actual_from_eia", return_value=None), \
         patch("calendar_fetcher._fetch_actual_from_fmp", return_value=None), \
         patch("calendar_fetcher.fetch_actual_from_news", return_value=None), \
         patch("calendar_fetcher.ai_analyzer.search_actual_result", return_value=None):
        result = calendar_fetcher.fetch_actual_result("EUR", "Some Obscure Indicator", datetime(2026, 8, 1, tzinfo=timezone.utc))

    assert result is None
