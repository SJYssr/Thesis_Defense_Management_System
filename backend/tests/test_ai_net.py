"""出网策略单测: 回环地址绕系统代理.

背景(本机实测): macOS 系统代理会被 urllib/httpx 默认读取, 于是
http://127.0.0.1:11434 被代理转走并返回 502 —— 本地 ollama 明明可用也被判"不可达",
auto 策略直接失败。这里锁住"回环直连 / 云端仍走代理"的边界。
"""
import json
import unittest
import urllib.request
from unittest import mock

from app.ai import net
from app.ai.llm.factory import OllamaProbe, OpenAICompatibleLLM, probe_ollama, reset_ollama_probe_cache


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class LoopbackTest(unittest.TestCase):
    def test_loopback_urls(self):
        for url in ("http://127.0.0.1:11434", "http://localhost:8000/v1",
                    "http://[::1]:9000", "http://127.0.1.5:1", "http://0.0.0.0:80"):
            self.assertTrue(net.is_loopback_url(url), url)

    def test_remote_and_malformed(self):
        for url in ("https://open.bigmodel.cn/api/paas/v4", "https://api.deepseek.com",
                    "http://192.168.1.10:11434", "", None, "http://[bad"):
            self.assertFalse(net.is_loopback_url(url), url)

    def test_direct_opener_has_no_proxy(self):
        """build_opener(ProxyHandler({})) 会把默认代理 handler 整个替换掉 -> 结果里没有代理条目.

        这正是直连的实现方式: 没有 ProxyHandler 就不会读系统代理。
        """
        opener = net.direct_opener()
        carried = [h for h in opener.handlers if getattr(h, "proxies", None)]
        self.assertEqual(carried, [], "直连 opener 不应携带任何代理")
        self.assertFalse([h for h in opener.handlers
                          if isinstance(h, urllib.request.ProxyHandler)
                          and getattr(h, "proxies", None)])

    def test_openai_client_trust_env_by_url(self):
        self.assertIsNone(net.openai_http_client("https://open.bigmodel.cn/api/paas/v4"))
        client = net.openai_http_client("http://127.0.0.1:11434/v1")
        try:
            self.assertFalse(client.trust_env)
        finally:
            client.close()


class CompletionTest(unittest.TestCase):
    def test_sync_completion_sends_system_and_user_without_history(self):
        with mock.patch("openai.OpenAI") as sdk:
            service = OpenAICompatibleLLM("https://example.invalid/v1", "test-only", "test-model")
        create = sdk.return_value.chat.completions.create
        create.return_value.choices = [mock.Mock(message=mock.Mock(content="练习问题"))]
        self.assertEqual(service.complete("答辩教师", "请出题"), "练习问题")
        create.assert_called_once_with(model="test-model", temperature=0.3, messages=[
            {"role": "system", "content": "答辩教师"}, {"role": "user", "content": "请出题"},
        ])

    def test_provider_error_omits_upstream_body_and_returns_retryable_status(self):
        import httpx
        from openai import RateLimitError

        from app.core.exceptions import BusinessException

        with mock.patch("openai.OpenAI") as sdk:
            service = OpenAICompatibleLLM("https://example.invalid/v1", "test-only", "test-model")
        response = httpx.Response(429, request=httpx.Request("POST", "https://example.invalid/v1"))
        sdk.return_value.chat.completions.create.side_effect = RateLimitError(
            "sensitive-upstream-detail", response=response, body=None)
        with self.assertRaises(BusinessException) as raised:
            service.complete("教师", "反馈")
        self.assertEqual(raised.exception.code, 50303)
        self.assertIn("供应商配额", raised.exception.message)
        self.assertNotIn("sensitive-upstream-detail", raised.exception.message)


class ProbeBypassesProxyTest(unittest.TestCase):
    """系统代理存在时, ollama 探测不得走 urlopen(默认读系统代理)."""

    def tearDown(self):
        reset_ollama_probe_cache()

    def test_probe_uses_direct_opener(self):
        payload = json.dumps({"models": [{"name": "qwen2.5:7b-instruct"}]}).encode()
        fake_opener = mock.Mock()
        fake_opener.open.return_value = _FakeResponse(payload)
        with mock.patch.object(net, "_direct_opener", fake_opener), \
                mock.patch("urllib.request.urlopen",
                           side_effect=AssertionError("回环探测不允许走系统代理")):
            result = OllamaProbe._fetch("http://127.0.0.1:11434", 1.0)
        self.assertTrue(result["reachable"])
        self.assertEqual(result["models"], ["qwen2.5:7b-instruct"])
        fake_opener.open.assert_called_once()

    def test_probe_reports_connection_refused_not_proxy_error(self):
        """真实场景回归: 无 ollama 时应是 Connection refused, 而不是代理的 502."""
        reset_ollama_probe_cache()
        result = probe_ollama()
        if result["reachable"]:
            self.skipTest("本机有可用的 ollama 服务, 跳过拒绝路径断言")
        self.assertNotIn("502", result["detail"])
        self.assertNotIn("Bad Gateway", result["detail"])


if __name__ == "__main__":
    unittest.main()
