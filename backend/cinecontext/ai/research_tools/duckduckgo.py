from html.parser import HTMLParser
import logging
from urllib.parse import parse_qs, unquote, urljoin, urlsplit
from uuid import UUID

from ...settings import CrawlerOptions, DuckDuckGoOptions
from ...observability import safe_url
from ..types import EvidenceDraft, EvidenceKind, SearchResult
from .crawler import WebCrawler

logger = logging.getLogger(__name__)


def _classes(attributes):
    return set(dict(attributes).get("class", "").split())


def decode_duckduckgo_url(href, endpoint="https://html.duckduckgo.com/html/"):
    url = urljoin(endpoint, href)
    target = parse_qs(urlsplit(url).query).get("uddg")
    return unquote(target[0]) if target else url


class DuckDuckGoHTMLParser(HTMLParser):
    def __init__(self, endpoint="https://html.duckduckgo.com/html/"):
        super().__init__(convert_charrefs=True)
        self.endpoint = endpoint
        self.results = []
        self._current = None
        self._capture = None
        self._parts = []

    def handle_starttag(self, tag, attrs):
        classes = _classes(attrs)
        if tag == "a" and "result__a" in classes:
            self._finish_result()
            href = dict(attrs).get("href", "")
            self._current = {
                "url": decode_duckduckgo_url(href, self.endpoint),
                "title": "",
                "snippet": "",
            }
            self._capture = "title"
            self._parts = []
        elif self._current and "result__snippet" in classes:
            self._capture = "snippet"
            self._parts = []

    def handle_endtag(self, tag):
        if self._capture == "title" and tag == "a":
            self._store_capture()
        elif self._capture == "snippet" and tag in {"a", "div", "span"}:
            self._store_capture()

    def handle_data(self, data):
        if self._capture:
            self._parts.append(data)

    def close(self):
        super().close()
        self._store_capture()
        self._finish_result()

    def _store_capture(self):
        if self._capture and self._current is not None:
            self._current[self._capture] = " ".join("".join(self._parts).split())
        self._capture = None
        self._parts = []

    def _finish_result(self):
        self._store_capture()
        if self._current and self._current["url"] and self._current["title"]:
            self.results.append(SearchResult(**self._current))
        self._current = None


class DuckDuckGoSearchProvider:
    def __init__(
        self,
        options: DuckDuckGoOptions,
        crawler_options: CrawlerOptions,
        crawler: WebCrawler,
    ):
        self.options = options
        self.crawler_options = crawler_options
        self.crawler = crawler

    async def search(self, query: str, film_id: UUID | None = None) -> list[EvidenceDraft]:
        if not self.options.enabled:
            logger.debug("DuckDuckGo search skipped provider_disabled=true")
            return []
        endpoint = safe_url(self.options.endpoint)
        logger.debug("DuckDuckGo search started endpoint=%s", endpoint)
        fetched = await self.crawler.fetch_text(
            self.options.endpoint,
            params={"q": query[: self.options.query_length]},
            byte_limit=self.crawler_options.response_byte_limit,
        )
        if fetched is None:
            logger.debug("DuckDuckGo search completed endpoint=%s results=0 evidence=0", endpoint)
            return []
        _, html = fetched
        parser = DuckDuckGoHTMLParser(self.options.endpoint)
        parser.feed(html)
        parser.close()
        results = []
        seen = set()
        for result in parser.results:
            if result.url not in seen:
                seen.add(result.url)
                results.append(result)
            if len(results) >= self.options.result_limit:
                break

        evidence = []
        crawled_count = 0
        snippet_count = 0
        for index, result in enumerate(results):
            should_crawl = self.crawler_options.enabled and index < self.crawler_options.max_pages
            page = await self.crawler.fetch_page(result) if should_crawl else None
            if page and page.text:
                crawled_count += 1
                evidence.append(
                    EvidenceDraft(
                        passage_id=None,
                        film_id=film_id,
                        source_title=page.title or result.title,
                        source_url=page.url,
                        locator="DuckDuckGo · Page excerpt",
                        body=page.text[: self.crawler_options.excerpt_length],
                        kind=EvidenceKind.WEB_EXCERPT,
                    )
                )
            elif result.snippet:
                snippet_count += 1
                evidence.append(
                    EvidenceDraft(
                        passage_id=None,
                        film_id=film_id,
                        source_title=result.title,
                        source_url=result.url,
                        locator="DuckDuckGo · Search summary",
                        body=result.snippet[: self.crawler_options.excerpt_length],
                        kind=EvidenceKind.SEARCH_SUMMARY,
                    )
                )
        logger.debug(
            "DuckDuckGo search completed endpoint=%s results=%d evidence=%d crawled=%d snippets=%d",
            endpoint, len(results), len(evidence), crawled_count, snippet_count,
        )
        return evidence
