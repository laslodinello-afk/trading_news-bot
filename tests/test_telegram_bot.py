"""
Couvre broadcast() : le fait qu'il archive (ou non) dans message_log selon le
succès de l'envoi et le paramètre `log`. Aucun appel réseau réel : send() est
mocké (voir video_scripts/message_log pour pourquoi ce comportement compte :
c'est ce journal qui sert de source aux vidéos DEBRIEF).
"""
from unittest.mock import patch

import config
import telegram_bot


def test_broadcast_logs_message_when_send_succeeds(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_CHANNEL_ID", "")  # canal payant réel possible en .env local
    with patch("telegram_bot.send", return_value=True) as mock_send, \
         patch("telegram_bot.message_log.log_message") as mock_log:
        ok = telegram_bot.broadcast("texte de test")
    assert ok is True
    mock_send.assert_called_once_with("texte de test")
    mock_log.assert_called_once_with("perso", "texte de test")


def test_broadcast_does_not_log_when_send_fails(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_CHANNEL_ID", "")
    with patch("telegram_bot.send", return_value=False), \
         patch("telegram_bot.message_log.log_message") as mock_log:
        ok = telegram_bot.broadcast("texte de test")
    assert ok is False
    mock_log.assert_not_called()


def test_broadcast_log_false_skips_archiving_even_on_success(monkeypatch):
    """Réservé aux messages de démo (voir main.py run_test_mode) : envoyés pour
    de vrai sur Telegram (pour vérifier la config), mais jamais archivés — sinon
    du contenu fictif polluerait le journal qui nourrit les vidéos DEBRIEF."""
    monkeypatch.setattr(config, "TELEGRAM_CHANNEL_ID", "")
    with patch("telegram_bot.send", return_value=True), \
         patch("telegram_bot.message_log.log_message") as mock_log:
        ok = telegram_bot.broadcast("texte de démo", log=False)
    assert ok is True
    mock_log.assert_not_called()
