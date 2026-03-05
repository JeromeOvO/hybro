import pytest
from unittest.mock import MagicMock
from services.a2a_service import A2AService


@pytest.fixture
def a2a_svc():
    return A2AService()


def _card(output_modes=None):
    card = MagicMock()
    card.default_output_modes = output_modes
    return card


class TestResolveAcceptedModes:
    def test_text_only(self, a2a_svc):
        result = a2a_svc._resolve_accepted_modes(_card(["text"]))
        assert result == ["text/plain"]

    def test_image_shorthand_expands(self, a2a_svc):
        result = a2a_svc._resolve_accepted_modes(_card(["image"]))
        assert set(result) == {"image/png", "image/jpeg", "image/gif", "image/webp"}

    def test_text_and_image(self, a2a_svc):
        result = a2a_svc._resolve_accepted_modes(_card(["text", "image"]))
        assert "text/plain" in result
        assert "image/png" in result

    def test_explicit_mime(self, a2a_svc):
        result = a2a_svc._resolve_accepted_modes(_card(["application/json"]))
        assert result == ["application/json"]

    def test_unsupported_mode_falls_back(self, a2a_svc):
        result = a2a_svc._resolve_accepted_modes(_card(["audio/mp3"]))
        assert result == ["text/plain"]

    def test_video_shorthand_expands(self, a2a_svc):
        result = a2a_svc._resolve_accepted_modes(_card(["video"]))
        assert set(result) == {"video/mp4", "video/webm"}

    def test_truly_unknown_shorthand_falls_back(self, a2a_svc):
        result = a2a_svc._resolve_accepted_modes(_card(["hologram"]))
        assert result == ["text/plain"]

    def test_none_defaults_to_text(self, a2a_svc):
        result = a2a_svc._resolve_accepted_modes(_card(None))
        assert result == ["text/plain"]

    def test_empty_list_defaults_to_text(self, a2a_svc):
        card = MagicMock()
        card.default_output_modes = []
        result = a2a_svc._resolve_accepted_modes(card)
        assert result == ["text/plain"]

    def test_mixed_supported_unsupported(self, a2a_svc):
        result = a2a_svc._resolve_accepted_modes(_card(["text", "audio/wav"]))
        assert "text/plain" in result
