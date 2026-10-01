"""HTTP 抓取基础设施:统一 UA、重试、限速、编码处理。"""

from __future__ import annotations

import logging
import time
from urllib.parse import urljoin, urlparse

import httpx

log = logging.getLogger("uavwatch.fetch")

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class Fetcher:
    """带限速与重试的轻量 HTTP 客户端。"""

    def __init__(self, delay: float = 0.6, timeout: float = 20.0,
                 headers: dict[str, str] | None = None):
        self.delay = delay
        self.timeout = timeout
        self._last = 0.0
        # 并行抓取时节流要全局生效，见 _throttle 的说明
        import threading as _th
        self._lock = _th.Lock()
        self.client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            cookies=httpx.Cookies(),
            headers={
                "User-Agent": DEFAULT_UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                **(headers or {}),
            },
            verify=False,   # 部分政府站点证书链不完整
        )

    def _throttle(self) -> None:
        """全局节流 —— 加锁，让多线程下发起的请求也保持统一间隔。

        原来没有锁：8 个线程同时进来，各自读到同一个 self._last，
        一起判断"该等了"，一起 sleep，醒来一起发 —— 节流等于没有，
        对目标站点是 8 倍瞬时压力；而且 self._last 被互相覆盖，
        耗时在 13 秒和 65 秒之间乱跳。

        关键：**在锁内只预约时间，在锁外 sleep**。
        锁内 sleep 的话并行完全退化成串行（实测 51.7 秒，跟改造
        前一样）；锁外 sleep 则每个线程各自等到自己的时隙再发，
        请求间隔依旧是 delay，网络往返却重叠起来了。
        """
        with self._lock:
            now = time.time()
            # 预约下一个可用时隙
            at = max(now, self._last + self.delay)
            self._last = at
        wait = at - time.time()
        if wait > 0:
            time.sleep(wait)

    def get(self, url: str, *, retries: int = 2, **kw) -> httpx.Response | None:
        for attempt in range(retries + 1):
            self._throttle()
            try:
                resp = self.client.get(url, **kw)
                if resp.status_code == 200:
                    return resp
                log.debug("HTTP %s for %s", resp.status_code, url)
                if resp.status_code in (403, 404, 410):
                    return None
            except Exception as e:  # noqa: BLE001
                log.debug("fetch error %s: %s", url, e)
                # 国密证书站点（深圳 sz.gov.cn 等）用 SM2，Python 的
                # OpenSSL 无法协商，报 SSL: BAD_ECPOINT。这些站点走
                # 明文 HTTP 反而正常，降级重试一次即可。
                if "BAD_ECPOINT" in str(e) and url.startswith("https://"):
                    got = self._get_plain_http(url, **kw)
                    if got is not None:
                        return got
                if attempt == retries:
                    return None
                time.sleep(1.2 * (attempt + 1))
        return None

    def _get_plain_http(self, url: str, **kw) -> httpx.Response | None:
        """把 https:// 降级为 http:// 重试（国密证书站点的唯一可行路径）。"""
        plain = "http://" + url[len("https://"):]
        try:
            self._throttle()
            resp = self.client.get(plain, **kw)
            if resp.status_code == 200:
                log.debug("降级 http 成功: %s", plain)
                return resp
        except Exception as e:                  # noqa: BLE001
            log.debug("降级 http 也失败 %s: %s", plain, e)
        return None

    def post_text(self, url: str, data: dict, *, retries: int = 2, **kw) -> str:
        """POST 表单并返回文本(民航局检索接口要求 POST)。"""
        for attempt in range(retries + 1):
            self._throttle()
            try:
                resp = self.client.post(url, data=data, **kw)
                if resp.status_code == 200:
                    return self._decode(resp)
            except Exception as e:  # noqa: BLE001
                log.debug("post error %s: %s", url, e)
                if attempt == retries:
                    return ""
                time.sleep(1.2 * (attempt + 1))
        return ""

    def get_text(self, url: str, **kw) -> str:
        r = self.get(url, **kw)
        if r is None:
            return ""
        return self._decode(r)

    @staticmethod
    def _decode(r: httpx.Response) -> str:
        """站点编码混乱时(部分民航局页面是 gb2312 却声明 utf-8),做一次兜底。"""
        try:
            text = r.text
        except Exception:  # noqa: BLE001
            text = r.content.decode("gb18030", errors="replace")
        if "\ufffd" in text[:3000]:
            for enc in ("gb18030", "utf-8", "latin-1"):
                try:
                    cand = r.content.decode(enc)
                    if "\ufffd" not in cand[:3000]:
                        return cand
                except Exception:  # noqa: BLE001
                    continue
        return text

    def close(self) -> None:
        try:
            self.client.close()
        except Exception:  # noqa: BLE001
            pass

    def __enter__(self) -> "Fetcher":
        return self

    def __exit__(self, *a) -> None:
        self.close()


def absolutize(base: str, href: str) -> str:
    """把相对链接转成绝对链接。"""
    if not href or href.startswith(("javascript:", "mailto:", "#")):
        return ""
    return urljoin(base, href)


def same_host(url: str, base: str) -> bool:
    try:
        return urlparse(url).netloc == urlparse(base).netloc
    except Exception:  # noqa: BLE001
        return False
