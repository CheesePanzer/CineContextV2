import re
from uuid import UUID

import httpx

from ..types import EvidenceDraft, EvidenceKind, FilmProjection, film_title_key
from .local import LocalResearchProvider

API_URL = "https://en.wikipedia.org/w/api.php"
HEADERS = {"User-Agent": "CineContext/0.1 (https://github.com/CheesePanzer/CineContextV2)"}


class WikipediaSearchProvider:
    def __init__(self, http: httpx.AsyncClient, local: LocalResearchProvider | None = None):
        self.http = http
        self.local = local

    async def search(self, query: str, film_id: UUID | None = None, *, include_setting=False) -> list[EvidenceDraft]:
        film = await self.local.lookup_movie(film_id) if film_id and self.local else None
        if film:
            titles = {film_title_key(film.title), film_title_key(film.original_title)}
            title = film.title.replace('"', '')
            catalog_query = f'intitle:"{title}" {film.year} film'
            pages = await self._find_pages(catalog_query)
            rows = await self._film_evidence(film, titles, pages)
            if rows or film.original_title == film.title:
                return rows
            original_title = film.original_title.replace('"', '')
            pages = await self._find_pages(f'intitle:"{original_title}" {film.year} film')
            return await self._film_evidence(film, titles, pages)
        pages = await self._find_pages(query)
        evidence = []
        for rank, page in enumerate(pages):
            body = page.get("extract", "").strip()
            if include_setting and rank == 0 and (page_id := page.get("pageid")):
                body = await self._setting_excerpt(page_id, body)
            if body:
                evidence.append(self._evidence(page, body, None, include_setting))
        return evidence

    async def film_reference_urls(self, film: FilmProjection) -> list[str]:
        titles = {film.title, film.original_title} - {""}
        variants = list(dict.fromkeys(
            candidate for title in sorted(titles)
            for candidate in (title, f"{title} ({film.year} film)", f"{title} (film)")
        ))
        aliases = {film_title_key(title) for title in titles}
        response = await self.http.get(API_URL, headers=HEADERS, params={
            "action": "query", "titles": "|".join(variants), "redirects": 1,
            "prop": "extracts|extlinks", "exintro": 1, "explaintext": 1,
            "exlimit": "max", "ellimit": "max", "format": "json", "formatversion": 2,
        }, timeout=5)
        response.raise_for_status()
        for page in response.json().get("query", {}).get("pages", []):
            heading = re.sub(r"\s*\([^)]*\)\s*$", "", page["title"])
            intro = page.get("extract", "")[:650]
            if film_title_key(heading) in aliases and str(film.year) in intro and "film" in intro.lower():
                return [link["url"] for link in page.get("extlinks", [])]
        return []

    async def _find_pages(self, query: str) -> list[dict]:
        response = await self.http.get(
            API_URL,
            headers=HEADERS,
            params={
                "action": "query", "generator": "search", "gsrsearch": query[:250],
                "gsrnamespace": 0, "gsrlimit": 2, "prop": "extracts|info", "inprop": "url",
                "exintro": 1, "explaintext": 1, "exlimit": 2, "format": "json", "formatversion": 2,
            },
        )
        response.raise_for_status()
        pages = response.json().get("query", {}).get("pages", [])
        return sorted(pages, key=lambda item: item.get("index", 0))

    async def _film_evidence(self, film: FilmProjection, titles: set[str], pages: list[dict]) -> list[EvidenceDraft]:
        for page in pages:
            heading = re.sub(r"\s*\([^)]*\)\s*$", "", page["title"])
            introduction = page.get("extract", "").strip()
            if (film_title_key(heading) not in titles or str(film.year) not in introduction[:600]
                    or "film" not in introduction[:400].lower()):
                continue
            rows = [self._evidence(page, introduction, film.id, False)]
            if page_id := page.get("pageid"):
                excerpt = await self._article_section(page_id, "Plot|Synopsis|Setting")
                if excerpt and excerpt not in introduction:
                    rows.append(self._evidence(page, excerpt, film.id, True))
            return rows
        return []

    @staticmethod
    def _evidence(page: dict, body: str, film_id: UUID | None, extended: bool) -> EvidenceDraft:
        locator, limit = "Introduction", 1600
        if extended:
            locator, limit = ("Plot or setting", 1200) if film_id else ("Article excerpt", 6000)
        return EvidenceDraft(
            film_id=film_id, source_title=f'Wikipedia · {page["title"]}', source_url=page["fullurl"],
            locator=locator, body=body[:limit], kind=EvidenceKind.WEB_EXCERPT,
        )

    async def _article_section(self, page_id: int, section_names: str) -> str:
        article = await self._article_text(page_id)
        pattern = rf"^={{2,}}\s*(?:{section_names})\s*=+[^\n]*\n(.+?)(?=\n=+\s|\Z)"
        section = re.search(pattern, article, re.M | re.S | re.I)
        return section[1].strip() if section else ""

    async def _setting_excerpt(self, page_id: int, introduction: str) -> str:
        article = await self._article_text(page_id)
        setting = re.search(r"^=+\s*Setting\s*=+[^\n]*\n(.+?)(?=\n=+\s|\Z)", article, re.M | re.S)
        if setting:
            return f"{introduction}\n\nSetting\n{setting[1].strip()[:2400]}"
        return article[:5000] or introduction

    async def _article_text(self, page_id: int) -> str:
        try:
            response = await self.http.get(API_URL, headers=HEADERS, params={
                "action": "query", "pageids": page_id, "prop": "extracts", "explaintext": 1,
                "exsectionformat": "wiki", "format": "json", "formatversion": 2,
            })
            response.raise_for_status()
            pages = response.json().get("query", {}).get("pages", [])
            return pages[0].get("extract", "") if pages else ""
        except (ValueError, httpx.HTTPError):
            return ""
