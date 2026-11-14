from urllib.parse import urlsplit
from uuid import UUID

import httpx

from ...settings import BraveOptions
from ..types import EvidenceDraft, EvidenceKind


class BraveSearchProvider:
    def __init__(self, http: httpx.AsyncClient, options: BraveOptions):
        self.http = http
        self.options = options

    async def search(self, query: str, film_id: UUID | None = None) -> list[EvidenceDraft]:
        response = await self.http.get(
            self.options.endpoint,
            headers={"X-Subscription-Token": self.options.api_key},
            params={
                "q": query[: self.options.query_length],
                "maximum_number_of_urls": self.options.maximum_urls,
                "maximum_number_of_tokens": self.options.maximum_tokens,
                "maximum_number_of_tokens_per_url": self.options.maximum_tokens_per_url,
                "enable_local": "false",
            },
        )
        response.raise_for_status()
        items = []
        rows = (response.json().get("grounding") or {}).get("generic", [])
        for row in rows[: self.options.maximum_urls]:
            url = row.get("url", "")
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
                continue
            for number, snippet in enumerate(
                row.get("snippets", [])[: self.options.snippets_per_url], 1
            ):
                if isinstance(snippet, str) and snippet.strip():
                    items.append(
                        EvidenceDraft(
                            passage_id=None,
                            film_id=film_id,
                            source_title=row.get("title") or parsed.hostname,
                            source_url=url,
                            locator=f"Brave Context · Excerpt {number}",
                            body=snippet.strip()[: self.options.excerpt_length],
                            kind=EvidenceKind.WEB_EXCERPT,
                        )
                    )
        return items
