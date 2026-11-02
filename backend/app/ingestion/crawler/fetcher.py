"""Tải trang công khai dezon.vn một cách lịch sự (đặc tả mục 11.2).

- Tôn trọng robots.txt (urllib.robotparser) cho user-agent "DezonAIBot"; tôn trọng Crawl-delay nếu lớn hơn.
- Delay tối thiểu 2 giây giữa hai request tới cùng host (kể cả request robots.txt).
- httpx là mặc định. Playwright chỉ dùng khi bật `use_playwright`, trang có dấu hiệu cần JS và
  thư viện đã được cài (`pip install .[crawler]`) — import lười.
- raw_hash = sha256 của nội dung đã chuẩn hóa (bỏ script/style/comment/bộ đếm lượt xem, gộp khoảng trắng)
  để thay đổi vô nghĩa (nonce, lượt xem) không kích hoạt cập nhật.

Chỉ chạy qua job nền (app.jobs), KHÔNG chạy khi khách đang hỏi.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import urllib.robotparser
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

log = logging.getLogger(__name__)

BOT_NAME = "DezonAIBot"
DEFAULT_USER_AGENT = f"{BOT_NAME}/0.1 (+https://dezon.vn; tro ly AI Dezon)"
MIN_DELAY_SECONDS = 2.0


class RobotsDisallowed(Exception):
    """URL bị robots.txt chặn cho DezonAIBot."""


class FetchBudgetExceeded(Exception):
    """Đã dùng hết số request cho phép trong lượt crawl."""


@dataclass
class FetchResult:
    url: str
    final_url: str
    status_code: int
    html: str
    raw_hash: str
    fetched_at: datetime
    rendered_with: str = "httpx"

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300


# ------------------------------------------------------------------ chuẩn hóa & hash
_DROP_BLOCKS = re.compile(
    r"<script\b.*?</script>|<style\b.*?</style>|<noscript\b.*?</noscript>|<svg\b.*?</svg>|<!--.*?-->",
    re.S | re.I,
)
_VOLATILE = [
    re.compile(r"<header\b.*?</header>|<footer\b.*?</footer>", re.S | re.I),  # menu có bộ đếm thay đổi
    re.compile(r'<div class="post_view[^"]*".*?</div>', re.S),  # lượt xem
    re.compile(r'\s(nonce|data-nonce|data-wpnonce)="[^"]*"', re.I),
    re.compile(r"[?&]ver=[\w.-]+"),
]


def normalize_html(html: str) -> str:
    """Bỏ phần không mang nội dung + phần thay đổi theo mỗi lần tải; gộp khoảng trắng."""
    s = _DROP_BLOCKS.sub(" ", html or "")
    for pat in _VOLATILE:
        s = pat.sub(" ", s)
    m = re.search(r"<main\b.*?</main>", s, re.S | re.I)  # ưu tiên vùng nội dung chính nếu có
    if m:
        head = re.search(r"<head\b.*?</head>", s, re.S | re.I)
        s = (head.group(0) if head else "") + m.group(0)
    return re.sub(r"\s+", " ", s).strip()


def content_hash(html: str) -> str:
    return hashlib.sha256(normalize_html(html).encode("utf-8")).hexdigest()


def payload_hash(payload: Any) -> str:
    """Hash cho dữ liệu có cấu trúc (API, snapshot): JSON sắp xếp khóa."""
    data = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def needs_js(html: str) -> bool:
    """Heuristic: trang gần như rỗng nội dung (SPA) -> cần render JS."""
    text_ = re.sub(r"<[^>]+>", " ", _DROP_BLOCKS.sub(" ", html or ""))
    words = len(text_.split())
    return words < 50 and ("id=\"root\"" in html or "id=\"app\"" in html or "__NEXT_DATA__" in html)


# ------------------------------------------------------------------ robots.txt theo RFC 9309
class RobotsRules:
    """Đánh giá robots.txt theo RFC 9309: chọn nhóm theo product token, quy tắc khớp DÀI NHẤT thắng,
    hỗ trợ wildcard `*` và `$`; hòa thì Allow thắng.

    Lý do: `urllib.robotparser` dùng quy tắc khớp đầu tiên và không hiểu wildcard — với robots.txt của
    dezon.vn (`Allow:/` đứng trước `Disallow: /planning/`) nó sẽ cho phép /planning/. Fetcher yêu cầu cả
    RobotsRules lẫn urllib.robotparser cùng cho phép (lấy kết quả chặt hơn).
    """

    def __init__(self, text: str, agent: str = BOT_NAME):
        self.agent = agent.lower()
        groups: list[tuple[list[str], list[tuple[bool, str]], float | None]] = []
        agents: list[str] = []
        rules: list[tuple[bool, str]] = []
        delay: float | None = None
        last_was_agent = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            key, value = key.strip().lower(), value.strip()
            if key == "user-agent":
                if not last_was_agent and (agents or rules):
                    groups.append((agents, rules, delay))
                    agents, rules, delay = [], [], None
                agents.append(value.lower())
                last_was_agent = True
                continue
            last_was_agent = False
            if key in ("allow", "disallow") and value:
                rules.append((key == "allow", value))
            elif key == "crawl-delay":
                try:
                    delay = float(value)
                except ValueError:
                    pass
        if agents or rules:
            groups.append((agents, rules, delay))
        specific = [g for g in groups if any(a != "*" and a in self.agent for a in g[0])]
        chosen = specific or [g for g in groups if "*" in g[0]]
        self.rules = [r for g in chosen for r in g[1]]
        delays = [g[2] for g in chosen if g[2] is not None]
        self.crawl_delay = max(delays) if delays else None

    @staticmethod
    def _match_len(pattern: str, path: str) -> int:
        anchored = pattern.endswith("$")
        body = pattern[:-1] if anchored else pattern
        regex = "".join(".*" if ch == "*" else re.escape(ch) for ch in body) + ("$" if anchored else "")
        return len(pattern) if re.match(regex, path) else -1

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        if path == "/robots.txt":
            return True
        best_len, best_allow = -1, True
        for allow, pattern in self.rules:
            n = self._match_len(pattern, path)
            if n > best_len or (n == best_len and allow and n >= 0):
                best_len, best_allow = n, allow
        return best_allow


# ------------------------------------------------------------------ fetcher
@dataclass
class Fetcher:
    """Fetcher có robots.txt + delay. Có thể truyền `client`, `sleep`, `clock` giả lập trong test."""

    user_agent: str = DEFAULT_USER_AGENT
    min_delay: float = MIN_DELAY_SECONDS
    max_requests: int | None = None
    timeout: float = 30.0
    use_playwright: bool = False
    client: httpx.Client | None = None
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    requests_made: int = 0
    _robots: dict[str, tuple[urllib.robotparser.RobotFileParser, RobotsRules] | None] = field(
        default_factory=dict)
    _crawl_delay: dict[str, float] = field(default_factory=dict)
    _last_request: dict[str, float] = field(default_factory=dict)
    _own_client: bool = False

    def __post_init__(self) -> None:
        if self.min_delay < MIN_DELAY_SECONDS:
            self.min_delay = MIN_DELAY_SECONDS  # không cho phép nhanh hơn 2 giây/request
        if BOT_NAME not in self.user_agent:
            self.user_agent = f"{BOT_NAME} {self.user_agent}"
        if self.client is None:
            self.client = httpx.Client(timeout=self.timeout, follow_redirects=True)
            self._own_client = True

    # -- vòng đời
    def close(self) -> None:
        if self._own_client and self.client is not None:
            self.client.close()

    def __enter__(self) -> Fetcher:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- nhịp request
    def _host(self, url: str) -> str:
        parts = urlsplit(url)
        return f"{parts.scheme}://{parts.netloc}"

    def _throttle(self, host: str) -> None:
        if self.max_requests is not None and self.requests_made >= self.max_requests:
            raise FetchBudgetExceeded(f"Đã đạt giới hạn {self.max_requests} request")
        delay = max(self.min_delay, self._crawl_delay.get(host, 0.0))
        last = self._last_request.get(host)
        if last is not None:
            wait = delay - (self.clock() - last)
            if wait > 0:
                self.sleep(wait)
        self._last_request[host] = self.clock()
        self.requests_made += 1

    def _get(self, url: str) -> httpx.Response:
        self._throttle(self._host(url))
        return self.client.get(url, headers={"User-Agent": self.user_agent,
                                             "Accept-Language": "vi-VN,vi;q=0.9"})

    # -- robots.txt
    def robots_for(self, url: str) -> tuple[urllib.robotparser.RobotFileParser, RobotsRules] | None:
        """(RobotFileParser, RobotsRules) của host; None = không tải được robots.txt -> coi như chặn."""
        host = self._host(url)
        if host in self._robots:
            return self._robots[host]
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(f"{host}/robots.txt")
        try:
            resp = self._get(f"{host}/robots.txt")
        except httpx.HTTPError as exc:
            log.warning("Không tải được robots.txt của %s: %s — tạm dừng crawl host này", host, exc)
            self._robots[host] = None
            return None
        if resp.status_code in (401, 403) or resp.status_code >= 500:
            # 401/403: bị từ chối; 5xx: robots.txt "unreachable" -> coi như cấm toàn bộ (RFC 9309 §2.3.1.4).
            # Kết quả chỉ cache trong Fetcher của lượt crawl này; lượt sau (Fetcher mới) sẽ thử lại.
            rp.disallow_all = True
            rules = RobotsRules("User-agent: *\nDisallow: /\n")
            if resp.status_code >= 500:
                log.warning("robots.txt của %s trả HTTP %s — tạm dừng crawl host này", host, resp.status_code)
        elif resp.status_code >= 400:
            rp.allow_all = True  # 404: không có robots.txt -> được phép (RFC 9309)
            rules = RobotsRules("")
        else:
            rp.parse(resp.text.splitlines())
            rules = RobotsRules(resp.text, self.user_agent)
        delay = rules.crawl_delay or rp.crawl_delay(self.user_agent)
        if delay:
            self._crawl_delay[host] = float(delay)
        self._robots[host] = (rp, rules)
        return self._robots[host]

    def allowed(self, url: str) -> bool:
        pair = self.robots_for(url)
        if pair is None:
            return False
        rp, rules = pair
        return rules.allowed(url) and rp.can_fetch(self.user_agent, url)

    # -- tải trang
    def fetch(self, url: str) -> FetchResult:
        if not self.allowed(url):
            raise RobotsDisallowed(url)
        resp = self._get(url)
        html = resp.text
        rendered = "httpx"
        if self.use_playwright and resp.status_code == 200 and needs_js(html):
            rendered_html = self._render_with_playwright(url)
            if rendered_html:
                html, rendered = rendered_html, "playwright"
        return FetchResult(url=url, final_url=str(resp.url), status_code=resp.status_code, html=html,
                           raw_hash=content_hash(html), fetched_at=datetime.now(UTC), rendered_with=rendered)

    def _render_with_playwright(self, url: str) -> str | None:
        try:
            from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]
        except ImportError:
            log.info("Trang %s có vẻ cần JS nhưng Playwright chưa được cài — dùng HTML tĩnh", url)
            return None
        self._throttle(self._host(url))
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page(user_agent=self.user_agent)
                page.goto(url, wait_until="networkidle", timeout=int(self.timeout * 1000))
                html = page.content()
                browser.close()
                return html
        except Exception as exc:  # noqa: BLE001 — Playwright lỗi thì quay về HTML tĩnh
            log.warning("Playwright lỗi với %s: %s", url, exc)
            return None
