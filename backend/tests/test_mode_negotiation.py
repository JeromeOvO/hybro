from unittest.mock import MagicMock

import pytest

from a2a_adapter.translators import resolve_accepted_output_modes


@pytest.fixture
def resolve_modes():
    return resolve_accepted_output_modes


def _card(output_modes=None):
    card = MagicMock()
    card.default_output_modes = output_modes
    return card


class TestResolveAcceptedModes:
    def test_text_only(self, resolve_modes):
        result = resolve_modes(_card(["text"]))
        assert result == ["text/plain"]

    def test_image_shorthand_expands(self, resolve_modes):
        result = resolve_modes(_card(["image"]))
        assert set(result) == {"image/png", "image/jpeg", "image/gif", "image/webp"}

    def test_text_and_image(self, resolve_modes):
        result = resolve_modes(_card(["text", "image"]))
        assert "text/plain" in result
        assert "image/png" in result

    def test_explicit_mime(self, resolve_modes):
        result = resolve_modes(_card(["application/json"]))
        assert result == ["application/json"]

    def test_unsupported_mode_falls_back(self, resolve_modes):
        result = resolve_modes(_card(["audio/mp3"]))
        assert result == ["text/plain"]

    def test_video_shorthand_expands(self, resolve_modes):
        result = resolve_modes(_card(["video"]))
        assert set(result) == {"video/mp4", "video/webm"}

    def test_truly_unknown_shorthand_falls_back(self, resolve_modes):
        result = resolve_modes(_card(["hologram"]))
        assert result == ["text/plain"]

    def test_none_defaults_to_text(self, resolve_modes):
        result = resolve_modes(_card(None))
        assert result == ["text/plain"]

    def test_empty_list_defaults_to_text(self, resolve_modes):
        card = MagicMock()
        card.default_output_modes = []
        result = resolve_modes(card)
        assert result == ["text/plain"]

    def test_mixed_supported_unsupported(self, resolve_modes):
        result = resolve_modes(_card(["text", "audio/wav"]))
        assert "text/plain" in result
