"""
search_actual_result() : tout dernier recours pour le "résultat réel", via une
vraie recherche web Gemini (voir calendar_fetcher.fetch_actual_result). Aucun
appel réseau réel ici (call_gemini mocké) — le fait que use_search_grounding
active bien l'outil de recherche a été vérifié à la main contre l'API réelle.
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import ai_analyzer


def test_search_actual_result_returns_value_when_found():
    with patch("ai_analyzer.call_gemini", return_value={"actual": "3.2%"}) as mock_call:
        result = ai_analyzer.search_actual_result("USD", "CPI m/m", datetime(2026, 8, 1, tzinfo=timezone.utc))
    assert result == "3.2%"
    mock_call.assert_called_once()
    assert mock_call.call_args.kwargs.get("use_search_grounding") is True


def test_search_actual_result_returns_none_when_not_found_sentinel():
    with patch("ai_analyzer.call_gemini", return_value={"actual": ai_analyzer._ACTUAL_NOT_FOUND}):
        result = ai_analyzer.search_actual_result("USD", "CPI m/m", datetime(2026, 8, 1, tzinfo=timezone.utc))
    assert result is None


def test_search_actual_result_returns_none_when_call_gemini_fails():
    with patch("ai_analyzer.call_gemini", return_value=None):
        result = ai_analyzer.search_actual_result("USD", "CPI m/m", datetime(2026, 8, 1, tzinfo=timezone.utc))
    assert result is None


def test_search_actual_result_prompt_includes_event_details():
    with patch("ai_analyzer.call_gemini", return_value={"actual": "3.2%"}) as mock_call:
        ai_analyzer.search_actual_result(
            "USD", "CPI m/m", datetime(2026, 8, 1, tzinfo=timezone.utc), forecast="0.3%", previous="0.2%"
        )
    prompt = mock_call.call_args.args[0]
    assert "CPI m/m" in prompt
    assert "USD" in prompt
    assert "0.3%" in prompt
    assert "0.2%" in prompt


def test_call_gemini_adds_search_tool_only_when_requested(monkeypatch):
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value.text = '{"actual": "1%"}'
    monkeypatch.setattr(ai_analyzer, "_client", fake_client)

    ai_analyzer.call_gemini("prompt", {"type": "OBJECT"}, use_search_grounding=True)
    kwargs = fake_client.models.generate_content.call_args.kwargs
    assert kwargs["config"].tools is not None

    fake_client.models.generate_content.reset_mock()
    ai_analyzer.call_gemini("prompt", {"type": "OBJECT"}, use_search_grounding=False)
    kwargs = fake_client.models.generate_content.call_args.kwargs
    assert kwargs["config"].tools is None
