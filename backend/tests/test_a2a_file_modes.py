"""A2A input-mode and MIME acceptance helpers."""

from types import SimpleNamespace

from common.utils.a2a_file_modes import (
    agent_accepts_required_input_modes,
    agent_input_modes,
    agent_supports_any_file,
    mime_type_is_accepted,
)


def test_mime_type_is_accepted_cases():
    assert mime_type_is_accepted("application/pdf", ["file"]) is True
    assert mime_type_is_accepted("image/png", ["*/*"]) is True
    assert mime_type_is_accepted("application/pdf", ["application/pdf"]) is True
    assert mime_type_is_accepted("image/png", ["application/pdf"]) is False
    assert mime_type_is_accepted("image/png", ["image/*"]) is True
    assert mime_type_is_accepted("image/png", ["image/"]) is True
    assert mime_type_is_accepted("application/pdf", ["text"]) is False


def test_file_mode_helpers_normalize_object_cards():
    card = SimpleNamespace(default_input_modes=[" Application/JSON ", " TEXT "])

    assert agent_input_modes(card) == {"application/json", "text"}
    assert agent_supports_any_file(card) is True
    assert agent_accepts_required_input_modes(card, [" APPLICATION/JSON "]) is True
