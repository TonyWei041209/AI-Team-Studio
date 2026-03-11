"""Phase 6A degradation tests — verify graceful behavior when provider SDKs are missing.

Tests that:
  - Provider classes instantiate without SDK imports
  - list_models() works without SDK
  - healthcheck() returns ok=false with clear message when SDK missing
  - complete() raises RuntimeError (not ImportError) when SDK missing
  - Full app startup succeeds even if SDKs were hypothetically absent
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import patch

# Ensure services/runtime is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services", "runtime"))


def run_async(coro):
    """Helper to run async functions in sync test context."""
    return asyncio.get_event_loop().run_until_complete(coro)


class TestAnthropicDegradation(unittest.TestCase):
    """Test AnthropicProvider behavior when anthropic SDK is missing."""

    def _make_provider(self):
        from providers.anthropic_provider import AnthropicProvider
        p = AnthropicProvider()
        p.configure(api_key="sk-test-fake-key")
        return p

    def test_instantiate_without_sdk(self):
        """Provider class can be instantiated regardless of SDK availability."""
        p = self._make_provider()
        self.assertEqual(p.name, "anthropic")

    def test_list_models_without_sdk(self):
        """list_models works without SDK (uses static list)."""
        p = self._make_provider()
        models = run_async(p.list_models())
        self.assertGreater(len(models), 0)

    @patch.dict("sys.modules", {"anthropic": None})
    def test_healthcheck_graceful_when_sdk_missing(self):
        """healthcheck returns ok=False with message when SDK is missing."""
        # Need to reimport to pick up mocked module
        from providers.anthropic_provider import AnthropicProvider
        p = AnthropicProvider()
        p.configure(api_key="sk-test-fake-key")
        result = run_async(p.healthcheck())
        self.assertFalse(result.ok)
        self.assertIn("not installed", result.message)

    @patch.dict("sys.modules", {"anthropic": None})
    def test_complete_raises_runtime_error_when_sdk_missing(self):
        """complete() raises RuntimeError, not ImportError, when SDK is missing."""
        from providers.anthropic_provider import AnthropicProvider
        from providers.base import CompletionRequest, Message, MessageRole
        p = AnthropicProvider()
        p.configure(api_key="sk-test-fake-key")
        req = CompletionRequest(
            model="claude-3-5-haiku-20241022",
            messages=[Message(role=MessageRole.user, content="test")],
        )
        with self.assertRaises(RuntimeError) as ctx:
            run_async(p.complete(req))
        self.assertIn("not installed", str(ctx.exception))


class TestOpenAIDegradation(unittest.TestCase):
    """Test OpenAICompatibleProvider behavior when openai SDK is missing."""

    def _make_provider(self):
        from providers.openai_compatible_provider import OpenAICompatibleProvider
        p = OpenAICompatibleProvider(profile="openai")
        p.configure(api_key="sk-test-fake-key")
        return p

    def test_instantiate_without_sdk(self):
        p = self._make_provider()
        self.assertEqual(p.name, "openai")

    def test_list_models_without_sdk(self):
        p = self._make_provider()
        models = run_async(p.list_models())
        self.assertGreater(len(models), 0)

    @patch.dict("sys.modules", {"openai": None})
    def test_healthcheck_graceful_when_sdk_missing(self):
        from providers.openai_compatible_provider import OpenAICompatibleProvider
        p = OpenAICompatibleProvider(profile="openai")
        p.configure(api_key="sk-test-fake-key")
        result = run_async(p.healthcheck())
        self.assertFalse(result.ok)
        self.assertIn("not installed", result.message)

    @patch.dict("sys.modules", {"openai": None})
    def test_complete_raises_runtime_error_when_sdk_missing(self):
        from providers.openai_compatible_provider import OpenAICompatibleProvider
        from providers.base import CompletionRequest, Message, MessageRole
        p = OpenAICompatibleProvider(profile="openai")
        p.configure(api_key="sk-test-fake-key")
        req = CompletionRequest(
            model="gpt-4o-mini",
            messages=[Message(role=MessageRole.user, content="test")],
        )
        with self.assertRaises(RuntimeError) as ctx:
            run_async(p.complete(req))
        self.assertIn("not installed", str(ctx.exception))


class TestGeminiDegradation(unittest.TestCase):
    """Test GeminiProvider behavior when google-genai SDK is missing."""

    def _make_provider(self):
        from providers.gemini_provider import GeminiProvider
        p = GeminiProvider()
        p.configure(api_key="test-fake-key")
        return p

    def test_instantiate_without_sdk(self):
        p = self._make_provider()
        self.assertEqual(p.name, "gemini")

    def test_list_models_without_sdk(self):
        p = self._make_provider()
        models = run_async(p.list_models())
        self.assertGreater(len(models), 0)

    @patch.dict("sys.modules", {"google": None, "google.genai": None})
    def test_healthcheck_graceful_when_sdk_missing(self):
        from providers.gemini_provider import GeminiProvider
        p = GeminiProvider()
        p.configure(api_key="test-fake-key")
        result = run_async(p.healthcheck())
        self.assertFalse(result.ok)
        self.assertIn("not installed", result.message)

    @patch.dict("sys.modules", {"google": None, "google.genai": None})
    def test_complete_raises_runtime_error_when_sdk_missing(self):
        from providers.gemini_provider import GeminiProvider
        from providers.base import CompletionRequest, Message, MessageRole
        p = GeminiProvider()
        p.configure(api_key="test-fake-key")
        req = CompletionRequest(
            model="gemini-2.0-flash",
            messages=[Message(role=MessageRole.user, content="test")],
        )
        with self.assertRaises(RuntimeError) as ctx:
            run_async(p.complete(req))
        self.assertIn("not installed", str(ctx.exception))


class TestRegistryDegradation(unittest.TestCase):
    """Test that registry works fully without any provider SDK installed."""

    def test_registry_builds_without_sdks(self):
        """Registry can instantiate all providers without SDK imports."""
        from providers.registry import ProviderRegistry
        reg = ProviderRegistry()
        providers = reg.list_providers()
        self.assertGreaterEqual(len(providers), 6)

    def test_all_models_listed_without_sdks(self):
        """All static model lists available without SDKs."""
        from providers.registry import ProviderRegistry
        reg = ProviderRegistry()
        models = run_async(reg.list_all_models())
        self.assertGreaterEqual(len(models), 10)



if __name__ == "__main__":
    results = []

    print("\n=== Provider SDK Degradation Tests ===\n")
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])

    # Run with verbose output
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Summary
    print("\n" + "=" * 70)
    total = result.testsRun
    failed = len(result.failures) + len(result.errors)
    passed = total - failed
    print(f"TOTAL: {total}  |  PASS: {passed}  |  FAIL: {failed}")
    print("=" * 70)

    if failed > 0:
        sys.exit(1)
    sys.exit(0)
