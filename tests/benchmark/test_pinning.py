"""Tests for model pinning (f5-16 §5.2 F3)."""

from __future__ import annotations

import pytest

from seahorse.benchmark.reproducibility.pinning import ModelPin, pin_ollama_model


def test_model_pin_canonical():
    pin = ModelPin(provider="ollama", model_tag="qwen3:1.7b", digest_sha256="a" * 64)
    assert pin.canonical == f"ollama/qwen3:1.7b@sha256:{'a' * 64}"


def test_pin_ollama_model(monkeypatch):
    digest = "abc123" + "0" * 58
    out = f"name: qwen3:1.7b\ndigest: sha256:{digest}\n"

    class _Proc:
        pass

    monkeypatch.setattr(
        "subprocess.check_output", lambda *a, **kw: out
    )
    pin = pin_ollama_model("qwen3:1.7b")
    assert pin.provider == "ollama"
    assert pin.model_tag == "qwen3:1.7b"
    assert pin.digest_sha256 == digest


def test_pin_ollama_model_raises_without_digest(monkeypatch):
    monkeypatch.setattr("subprocess.check_output", lambda *a, **kw: "no digest here")
    with pytest.raises(RuntimeError, match="Could not resolve digest"):
        pin_ollama_model("qwen3:1.7b")
