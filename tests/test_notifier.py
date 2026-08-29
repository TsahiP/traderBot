from unittest.mock import MagicMock, patch
import config
import notifier


def test_telegram_unconfigured(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "")
    assert notifier.send_telegram("test message") is False


def test_telegram_success(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "fake_token")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "12345678")

    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None

    with patch("requests.post", return_value=mock_resp) as mock_post:
        result = notifier.send_telegram("hello telegram")
        assert result is True
        mock_post.assert_called_once()
        assert "sendMessage" in mock_post.call_args[0][0]
        assert mock_post.call_args[1]["json"] == {"chat_id": "12345678", "text": "hello telegram"}


def test_telegram_network_error(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "fake_token")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "12345678")

    with patch("requests.post", side_effect=Exception("Connection timed out")):
        # Should not raise exception; return False gracefully
        result = notifier.send_telegram("hello telegram")
        assert result is False


def test_telegram_targeted_reply_and_message_limit(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "fake_token")
    mock_resp = MagicMock()

    with patch("requests.post", return_value=mock_resp) as mock_post:
        assert notifier.send_telegram_to(999, "x" * 5000) is True

    payload = mock_post.call_args.kwargs["json"]
    assert payload["chat_id"] == "999"
    assert len(payload["text"]) == 4096
