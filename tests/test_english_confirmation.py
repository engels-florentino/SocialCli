"""English approval prompts preserve explicit consent and legacy inputs."""

import pytest

from socialctl.cli import _confirmar


@pytest.mark.parametrize("answer", ["y", "yes", "YES", "s", "si", "sí"])
def test_confirmation_accepts_explicit_affirmative(monkeypatch, capsys, answer):
    replies = iter([answer, "n"])
    monkeypatch.setattr("builtins.input", lambda: next(replies))
    assert _confirmar("Publish?") is True
    assert "[y/N]" in capsys.readouterr().out


@pytest.mark.parametrize("answer", ["n", "no", ""])
def test_confirmation_declines_negative_or_empty(monkeypatch, answer):
    monkeypatch.setattr("builtins.input", lambda: answer)
    assert _confirmar("Publish?") is False


def test_confirmation_declines_eof(monkeypatch):
    def end_of_input():
        raise EOFError
    monkeypatch.setattr("builtins.input", end_of_input)
    assert _confirmar("Publish?") is False


def test_confirmation_invalid_input_requires_explicit_answer(monkeypatch, capsys):
    replies = iter(["maybe", "yes", "n"])
    monkeypatch.setattr("builtins.input", lambda: next(replies))
    assert _confirmar("Publish?") is True
    assert "Unrecognized answer (enter 'y' or 'n')" in capsys.readouterr().out
