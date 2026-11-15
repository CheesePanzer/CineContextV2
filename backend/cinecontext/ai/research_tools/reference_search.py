import asyncio
from urllib.parse import urlsplit

import httpx
from alcedo import Options

from ...settings import ToolsOptions
from ..types import EvidenceDraft
from .brave import BraveSearchProvider
from .crawler import WebCrawler, resolve_host
from .duckduckgo import DuckDuckGoSearchProvider
from .wikipedia import WikipediaSearchProvider


class WebReferenceSearch:
    def __init__(self, options: Options[ToolsOptions]):
        self.options = options.value

    async def search(self, queries: list[str]) -> list[EvidenceDraft]:
        terms = list(dict.fromkeys(query.strip()[:250] for query in queries if query.strip()))
        if not terms:
            return []
        async with httpx.AsyncClient(timeout=min(self.options.timeout_seconds, 7)) as http:
            crawler = WebCrawler(http, self.options.crawler, resolve_host)
            browser_search = (
                BraveSearchProvider(http, self.options.brave)
                if self.options.brave.enabled
                else DuckDuckGoSearchProvider(self.options.duckduckgo, self.options.crawler, crawler)
            )
            wikipedia = WikipediaSearchProvider(http)

            async def find(query):
                try:
                    rows = await asyncio.wait_for(browser_search.search(query), timeout=8)
                    if rows:
                        return rows[:2]
                except (TimeoutError, httpx.HTTPError, ValueError):
                    pass
                if self.options.wikipedia_enabled:
                    try:
                        return await wikipedia.search(query, include_setting=True)
                    except (httpx.HTTPError, ValueError):
                        pass
                return []

            results = await asyncio.gather(*(find(query) for query in terms))
        sources = {}
        for rows in results:
            for row in rows:
                url = urlsplit(row.source_url)
                if url.scheme in {"http", "https"} and url.hostname and url.username is None and url.password is None:
                    sources.setdefault(row.source_url, row)
        return list(sources.values())
