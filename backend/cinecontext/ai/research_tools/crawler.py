import asyncio
import ipaddress
import logging
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

from ...settings import CrawlerOptions
from ...observability import safe_url
from ..types import PageContent, SearchResult

_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_ACCEPTED_CONTENT_TYPES = {"text/html", "text/plain"}
logger = logging.getLogger(__name__)


class VisibleHTMLParser(HTMLParser):
    _IGNORED = {"script", "style", "noscript", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.text = ""
        self._ignored_depth = 0
        self._in_title = False
        self._title_parts = []
        self._text_parts = []

    def handle_starttag(self, tag, attrs):
        if tag in self._IGNORED:
            self._ignored_depth += 1
        elif not self._ignored_depth and tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag in self._IGNORED and self._ignored_depth:
            self._ignored_depth -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._ignored_depth:
            return
        if self._in_title:
            self._title_parts.append(data)
        else:
            self._text_parts.append(data)

    def close(self):
        super().close()
        self.title = " ".join(" ".join(self._title_parts).split())
        self.text = " ".join(" ".join(self._text_parts).split())


async def resolve_host(hostname: str) -> set[str]:
    rows = await asyncio.to_thread(socket.getaddrinfo, hostname, None, type=socket.SOCK_STREAM)
    return {row[4][0] for row in rows}


async def validate_public_url(url: str, resolver=resolve_host) -> bool:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    if parsed.username is not None or parsed.password is not None:
        return False
    hostname = parsed.hostname.rstrip(".").casefold()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        return False
    try:
        addresses = await resolver(hostname)
        if not addresses:
            return False
        return all(ipaddress.ip_address(address).is_global for address in addresses)
    except (OSError, ValueError, UnicodeError):
        logger.debug("Crawler DNS validation failed hostname=%s", hostname)
        return False


class WebCrawler:
    def __init__(self, http: httpx.AsyncClient, options: CrawlerOptions, resolver=resolve_host):
        self.http = http
        self.options = options
        self.resolver = resolver

    async def fetch_page(self, result: SearchResult) -> PageContent | None:
        fetched = await self.fetch_text(result.url, byte_limit=self.options.response_byte_limit)
        if fetched is None:
            return None
        url, body = fetched
        parser = VisibleHTMLParser()
        parser.feed(body)
        parser.close()
        return PageContent(url=url, title=parser.title or result.title, text=parser.text)

    async def fetch_text(self, url: str, *, params=None, byte_limit: int):
        current = url
        for redirect in range(self.options.max_redirects + 1):
            if not await validate_public_url(current, self.resolver):
                logger.debug("Crawler skipped unsafe URL origin=%s", safe_url(current))
                return None
            try:
                async with self.http.stream(
                    "GET",
                    current,
                    params=params if redirect == 0 else None,
                    headers={"User-Agent": self.options.user_agent},
                    follow_redirects=False,
                ) as response:
                    if response.status_code in _REDIRECT_STATUSES:
                        location = response.headers.get("location")
                        if not location or redirect == self.options.max_redirects:
                            reason = "missing" if not location else "limit"
                            logger.debug(
                                "Crawler redirect skipped reason=%s origin=%s redirects=%d",
                                reason, safe_url(current), redirect,
                            )
                            return None
                        current = urljoin(str(response.url), location)
                        continue
                    if response.is_error:
                        logger.debug(
                            "Crawler HTTP status skipped status=%d origin=%s",
                            response.status_code, safe_url(str(response.url)),
                        )
                        return None
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    if content_type not in _ACCEPTED_CONTENT_TYPES:
                        logger.debug(
                            "Crawler content type skipped content_type=%s origin=%s",
                            content_type or "missing", safe_url(str(response.url)),
                        )
                        return None
                    chunks = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > byte_limit:
                            logger.debug(
                                "Crawler byte limit exceeded bytes=%d limit=%d origin=%s",
                                size, byte_limit, safe_url(str(response.url)),
                            )
                            return None
                        chunks.append(chunk)
                    text = b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
                    return str(response.url), text
            except httpx.HTTPError as error:
                logger.debug(
                    "Crawler HTTP exception error_type=%s origin=%s",
                    type(error).__name__, safe_url(current),
                )
                return None
        return None
