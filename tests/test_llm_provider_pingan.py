"""PingAn provider: failures must surface through LLMResponse.error, not content.

Regression: on a transport failure the provider used to return
``content="Error: ..."`` with ``error=None`` — the detector then treated the
error text as model output and routed it into the JSON parser.
"""

from __future__ import annotations

import sys
import types

import pytest


def _make_provider(monkeypatch):
    """Import + build a PingAnLLMProvider with pycryptodome stubbed out.

    ``pingan.py`` imports Crypto at module level (an optional extra), so the
    stub keeps this test installable in CI without the intranet dependencies.
    Only the signatures are stubbed — none are called on the error path.
    """
    crypto = types.ModuleType("Crypto")
    hash_mod = types.ModuleType("Crypto.Hash")
    pub_mod = types.ModuleType("Crypto.PublicKey")
    sig_mod = types.ModuleType("Crypto.Signature")
    hash_mod.SHA256 = types.SimpleNamespace(new=lambda *a, **k: object())
    pub_mod.RSA = types.SimpleNamespace(import_key=lambda *a, **k: object())
    signer = types.SimpleNamespace(sign=lambda h: b"")
    sig_mod.PKCS1_v1_5 = types.SimpleNamespace(new=lambda *a, **k: signer)
    crypto.Hash, crypto.PublicKey, crypto.Signature = hash_mod, pub_mod, sig_mod
    for name, mod in (
        ("Crypto", crypto),
        ("Crypto.Hash", hash_mod),
        ("Crypto.PublicKey", pub_mod),
        ("Crypto.Signature", sig_mod),
    ):
        monkeypatch.setitem(sys.modules, name, mod)

    from pysanitize.llm.provider.pingan import PingAnLLMProvider

    return PingAnLLMProvider(
        api_key="test-key",
        api_base="http://localhost:9/v1",  # nothing is called over the wire
        default_model="test-model",
        extra_headers={"sceneId": "1"},
    )


def test_invoke_failure_sets_error_channel(monkeypatch):
    provider = _make_provider(monkeypatch)

    def boom(**kwargs):
        raise RuntimeError("gateway down")

    monkeypatch.setattr(provider.client.chat.completions, "create", boom)
    resp = provider.invoke([{"role": "user", "content": "hi"}])
    assert resp.error == "gateway down"
    assert resp.content is None
    assert resp.finish_reason == "error"
