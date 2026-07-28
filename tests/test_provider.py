"""Provider interface + local Ollama tests (spec §4, Phase 1).

No live Ollama daemon needed: the HTTP layer is injected.
"""

import asyncio
import unittest
import urllib.error

from providers import (
    LocalOllamaProvider,
    ProviderAuthError,
    ProviderUnavailable,
    RateLimited,
    get_provider,
    registered_providers,
)


def _http_error(code):
    return urllib.error.HTTPError(
        url="http://x", code=code, msg="err", hdrs=None, fp=None
    )


class LocalOllamaTest(unittest.TestCase):
    def test_complete_maps_response(self):
        def fake_http(method, url, payload, timeout):
            self.assertEqual(method, "POST")
            self.assertTrue(url.endswith("/api/chat"))
            self.assertEqual(payload["model"], "qwen2.5-coder:7b")
            self.assertFalse(payload["stream"])
            return {
                "message": {"role": "assistant", "content": "hello world"},
                "model": "qwen2.5-coder:7b",
                "prompt_eval_count": 11,
                "eval_count": 7,
            }

        provider = LocalOllamaProvider(http_fn=fake_http)
        comp = asyncio.run(provider.complete([{"role": "user", "content": "hi"}]))
        self.assertEqual(comp.content, "hello world")
        self.assertEqual(comp.provider, "local_ollama")
        self.assertEqual(comp.prompt_tokens, 11)
        self.assertEqual(comp.completion_tokens, 7)

    def test_health_available(self):
        provider = LocalOllamaProvider(
            http_fn=lambda m, u, p, t: {"models": [{"name": "qwen2.5-coder:7b"}]}
        )
        health = asyncio.run(provider.health())
        self.assertTrue(health.available)
        self.assertIn("qwen2.5-coder:7b", health.detail)

    def test_health_unavailable_when_daemon_down(self):
        def refuse(method, url, payload, timeout):
            raise urllib.error.URLError("connection refused")

        provider = LocalOllamaProvider(http_fn=refuse)
        health = asyncio.run(provider.health())
        self.assertFalse(health.available)

    def test_error_mapping(self):
        cases = {
            429: RateLimited,
            401: ProviderAuthError,
            500: ProviderUnavailable,
        }
        for code, exc_type in cases.items():
            def fail(method, url, payload, timeout, _code=code):
                raise _http_error(_code)

            provider = LocalOllamaProvider(http_fn=fail)
            with self.assertRaises(exc_type):
                asyncio.run(provider.complete([{"role": "user", "content": "hi"}]))

    def test_connection_refused_is_transient(self):
        def refuse(method, url, payload, timeout):
            raise ConnectionRefusedError("nope")

        provider = LocalOllamaProvider(http_fn=refuse)
        with self.assertRaises(ProviderUnavailable):
            asyncio.run(provider.complete([{"role": "user", "content": "hi"}]))


class RegistryTest(unittest.TestCase):
    def test_local_ollama_registered(self):
        self.assertIn("local_ollama", registered_providers())
        provider = get_provider("local_ollama")
        self.assertIsInstance(provider, LocalOllamaProvider)

    def test_unknown_provider_rejected(self):
        with self.assertRaises(KeyError):
            get_provider("nim_pool")  # arrives Phase 4.5


if __name__ == "__main__":
    unittest.main()
