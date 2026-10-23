"""出网策略: 本机回环地址必须绕过系统代理.

macOS 的系统级代理(Clash/Surge 等)会被 urllib 与 httpx 默认读取(httpx 的 trust_env),
于是探测 http://127.0.0.1:11434 被代理转发并返回 502 —— 真实的本地 ollama 被误判为
"不可达", auto 策略随即失败。云端地址仍走系统代理(访问外网正需要它), 只有回环地址禁用。

实测现场(本机): urllib.request.getproxies() = {'http': 'http://127.0.0.1:12000'},
ollama 探测得到 "HTTP Error 502: Bad Gateway" 而不是 Connection refused。
"""
import urllib.request
from urllib.parse import urlparse

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "0.0.0.0"})


def is_loopback_url(url: str) -> bool:
    """base_url 是否指向本机(只有本机才必须绕代理)."""
    try:
        host = (urlparse(url or "").hostname or "").strip().lower()
    except ValueError:  # 非法 URL 交给调用方按原样处理
        return False
    if not host:
        return False
    return host in LOOPBACK_HOSTS or host.startswith("127.")


_direct_opener: urllib.request.OpenerDirector | None = None


def direct_opener() -> urllib.request.OpenerDirector:
    """不带任何代理的 urllib opener(回环地址专用, 进程内复用)."""
    global _direct_opener
    if _direct_opener is None:
        _direct_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return _direct_opener


def open_url(url: str, timeout: float):
    """回环地址走直连, 其余按系统默认(可带代理)."""
    if is_loopback_url(url):
        return direct_opener().open(url, timeout=timeout)
    return urllib.request.urlopen(url, timeout=timeout)  # noqa: S310 — 地址由配置决定


def openai_http_client(base_url: str):
    """给 OpenAI 兼容客户端准备 httpx 实例: 回环地址禁代理, 云端返回 None(SDK 默认)."""
    if not is_loopback_url(base_url):
        return None
    import httpx

    return httpx.Client(trust_env=False)
