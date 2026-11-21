import asyncio
import logging
import re
from collections.abc import AsyncIterator, Sequence
from codecs import getincrementaldecoder
from dataclasses import dataclass
from html import unescape
from unicodedata import combining, normalize
from urllib.parse import urlsplit

import httpx
from alcedo import Options

from ...contracts import Constraints, FilmHint, FurtherReading
from ...settings import ToolsOptions
from ..types import EvidenceDraft, EvidenceKind, EvidenceRecord, FilmProjection, SearchResult
from .brave import BraveSearchProvider
from .crawler import VisibleHTMLParser, WebCrawler
from .duckduckgo import DuckDuckGoSearchProvider
from .wikipedia import WikipediaSearchProvider


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CuratedReading:
    title: str
    year: int
    reading: FurtherReading

    def matches(self, film: FilmProjection) -> bool:
        return self.year == film.year and search_key(self.title) in {
            search_key(film.title), search_key(film.original_title),
        }


def search_key(value: str) -> str:
    return " ".join(re.findall(r"[^\W_]+", "".join(
        character for character in normalize("NFKD", value.casefold())
        if not combining(character)
    )))


def contains_term(text: str, term: str) -> bool:
    key = search_key(term)
    return bool(key and f" {key} " in f" {text} ")


def curated(title, year, publication, headline, url, summary, author=None):
    return CuratedReading(title, year, FurtherReading(
        publication=publication, title=headline, url=url, summary=summary, author=author, origin="catalogue",
    ))


BFI_MEDIEVAL_LIST = "https://www.bfi.org.uk/lists/10-great-films-set-middle-ages"
MEDIEVAL_BOHEMIA = (
    curated("Medieval", 2022, "BFI / Sight and Sound",
            "Medieval: this blood-soaked Bohemian biopic spares no expense",
            "https://www.bfi.org.uk/sight-and-sound/reviews/medieval-this-blood-soaked-bohemian-biopic-spares-no-expense",
            "The review follows Jan Žižka’s early career amid the rivalry between Wenceslaus IV and Sigismund, and weighs the battle scenes against the tangled court politics.",
            "Michael Brooke"),
    curated("Medieval", 2022, "Czech National Film Archive",
            "Jan Žižka (Medieval)", "https://www.filmovyprehled.cz/cs/film/402240/jan-zizka",
            "The synopsis opens in 1402, as King Wenceslaus IV expects to be crowned in Rome. The film takes considerable liberties with the life of Jan Žižka."),
    curated("Jan Hus", 1955, "Czech National Film Archive",
            "Jan Hus", "https://nfa.cz/en/25469-jan-hus/o-filmu",
            "The first film in Otakar Vávra’s Hussite trilogy follows the Czech church reformer Jan Hus, whose conflict with the church leads to his execution in Konstanz in 1415."),
    curated("Jan Žižka", 1956, "BFI / Sight and Sound",
            "Medieval: this blood-soaked Bohemian biopic spares no expense",
            "https://www.bfi.org.uk/sight-and-sound/reviews/medieval-this-blood-soaked-bohemian-biopic-spares-no-expense",
            "Brooke compares the new film to Otakar Vávra’s earlier treatment of the Bohemian general Jan Žižka, produced in 1955.",
            "Michael Brooke"),
    curated("Marketa Lazarová", 1967, "Criterion",
            "Cinema of the Wolf: The Mystery of Marketa Lazarová",
            "https://www.criterion.com/current/posts/2809-cinema-of-the-wolf-the-mystery-of-marketa-lazarova",
            "Gunning traces how Vláčil’s editing and sound evoke a violent medieval society where Christian and pagan practices coexist.",
            "Tom Gunning"),
    curated("Marketa Lazarová", 1967, "BFI",
            "10 great films set in the middle ages", BFI_MEDIEVAL_LIST,
            "The BFI entry situates Vláčil’s rival clans in 13th-century Europe and discusses the film’s sensory reconstruction of medieval life."),
)
MIDDLE_AGES = MEDIEVAL_BOHEMIA + (
    curated("The Passion of Joan of Arc", 1928, "BFI",
            "10 great films set in the middle ages", BFI_MEDIEVAL_LIST,
            "The BFI discusses how Dreyer uses close-ups to dramatize Joan’s trial during the Hundred Years’ War."),
    curated("The Seventh Seal", 1957, "BFI", "10 great films set in the middle ages", BFI_MEDIEVAL_LIST,
            "The BFI places Bergman’s knight in plague-stricken 14th-century Sweden and reads his encounter with Death as a religious allegory."),
    curated("Andrei Rublev", 1966, "BFI", "10 great films set in the middle ages", BFI_MEDIEVAL_LIST,
            "The BFI describes Tarkovsky’s depiction of a 15th-century icon painter whose faith is tested amid violence in medieval Russia."),
)
NORSE = (
    curated("The Northman", 2022, "BFI / Sight and Sound",
            "The Northman is not your average swords-and-shields epic",
            "https://www.bfi.org.uk/sight-and-sound/reviews/northman-not-your-average-swords-shields-epic",
            "Loayza examines how Eggers uses the Norse legend of Amleth to question the hero’s certainty about revenge.",
            "Beatrice Loayza"),
    curated("Valhalla Rising", 2009, "International Film Festival Rotterdam", "Valhalla Rising",
            "https://iffr.com/en/iffr/2010/films/valhalla-rising",
            "The festival describes an austere Viking-era journey with mystical elements and a group of Christian converts."),
)
ROMANIAN_POLITICAL_CINEMA = (
    curated("Police, Adjective", 2009, "BFI", "10 great Romanian New Wave films",
            "https://www.bfi.org.uk/lists/10-great-romanian-new-wave-films",
            "An undercover detective follows a teenager and questions his orders. BFI connects the film’s attention to bureaucratic language with the legacy of Romanian authoritarianism."),
    curated("12:08 East of Bucharest", 2006, "BFI", "10 great Romanian New Wave films",
            "https://www.bfi.org.uk/lists/10-great-romanian-new-wave-films",
            "BFI discusses Porumboiu’s comedy as a study of selective memory about the Romanian Revolution."),
    curated("The Paper Will Be Blue", 2006, "International Film Festival Rotterdam", "The Paper Will Be Blue",
            "https://iffr.com/en/iffr/2007/films/the-paper-will-be-blue",
            "The festival describes Muntean’s realistic reconstruction of a night in the 1989 Romanian Revolution, when soldiers receive orders over television."),
)
ROMANIAN_NEW_WAVE_READINGS = (
    curated("4 Months, 3 Weeks and 2 Days", 2007, "Criterion",
            "4 Months, 3 Weeks and 2 Days: Late Term",
            "https://www.criterion.com/current/posts/6166-4-months-3-weeks-and-2-days-late-term",
            "Taylor examines how an ordinary birthday dinner and small gestures give a sense of life under Ceaușescu within Mungiu’s tightly observed, single-day story.",
            "Ella Taylor"),
    curated("4 Months, 3 Weeks and 2 Days", 2007, "BFI / Sight and Sound",
            "Sight and Sound’s original verdict on 15 of Cannes’ biggest Palme d’Or winners",
            "https://www.bfi.org.uk/sight-and-sound/lists/original-verdict-15-cannes-biggest-palme-dor-winners",
            "In his original Cannes review, James highlights Mungiu’s attention to everyday detail and Anamaria Marinca’s performance as Otilia.",
            "Nick James"),
    curated("4 Months, 3 Weeks and 2 Days", 2007, "Senses of Cinema",
            "Holding Up the Other Half of the Sky: The 26th Vancouver International Film Festival",
            "https://www.sensesofcinema.com/2008/festival-reports/vancouver-iff-2007/",
            "Reynaud reads Mungiu’s portrait of friendship through its careful staging, bodily gestures, and the practical obstacles Otilia faces in Ceaușescu’s Romania.",
            "Bérénice Reynaud"),
    curated("Tuesday, After Christmas", 2010, "BFI / Sight and Sound", "Radu Muntean: three’s a crowd",
            "https://www.bfi.org.uk/sight-and-sound/interviews/radu-muntean-threes-crowd",
            "Gray connects Muntean’s long takes, rehearsed dialogue, and quietly observed marriage crisis to the Romanian New Wave’s realist style.",
            "Carmen Gray"),
    curated("Child's Pose", 2013, "BFI", "10 great Romanian New Wave films",
            "https://www.bfi.org.uk/lists/10-great-romanian-new-wave-films",
            "Gray situates Netzer’s film in the Romanian New Wave and reads its portrayal of an influential Bucharest family as a satire of privilege and corruption.",
            "Carmen Gray"),
)
ITALIAN_NEOREALISM_READINGS = (
    curated("Rome, Open City", 1945, "BFI", "Rome, Open City: Roberto Rossellini’s great leap for realism on screen",
            "https://www.bfi.org.uk/features/rome-open-city-roberto-rossellinis-great-leap-realism-screen",
            "The BFI traces how Rossellini’s account of resistance in occupied Rome helped launch Italian neorealism."),
    curated("Bicycle Thieves", 1948, "Criterion", "Bicycle Thieves: A Passionate Commitment to the Real",
            "https://www.criterion.com/current/posts/467-bicycle-thieves-a-passionate-commitment-to-the-real",
            "Cheshire connects De Sica’s street locations and nonprofessional cast to the film’s portrayal of unemployment and solidarity in postwar Rome.",
            "Godfrey Cheshire"),
    curated("Bicycle Thieves", 1948, "BFI", "Where to begin with Vittorio De Sica",
            "https://www.bfi.org.uk/features/where-begin-vittorio-de-sica",
            "Iannone recommends this father-and-son story as an introduction to De Sica and to neorealism’s interest in ordinary lives.",
            "Pasquale Iannone"),
    curated("Bicycle Thieves", 1948, "Senses of Cinema", "The Tribulations of the Working Class: Bicycle Thieves",
            "https://www.sensesofcinema.com/2020/cteq/bicycle-thieves-vittorio-de-sica-1948/",
            "The essay approaches De Sica’s search for a stolen bicycle through working-class life, unemployment, and the pressures of postwar Italy."),
    curated("Umberto D.", 1952, "BFI", "Where to begin with Vittorio De Sica",
            "https://www.bfi.org.uk/features/where-begin-vittorio-de-sica",
            "Iannone places De Sica’s film about a retired civil servant among his key neorealist portraits of everyday postwar life.",
            "Pasquale Iannone"),
)
PACIFIC_WAR_READINGS = (
    curated("The Thin Red Line", 1998, "Criterion", "The Thin Red Line: This Side of Paradise",
            "https://www.criterion.com/current/posts/1603-the-thin-red-line-this-side-of-paradise",
            "Sterritt looks at how Malick combines the soldiers’ voice-overs with images of the natural world, creating a war film whose questions outlast its battles.",
            "David Sterritt"),
    curated("The Thin Red Line", 1998, "Senses of Cinema", "The Shape of Fear: Thoughts after The Thin Red Line",
            "https://www.sensesofcinema.com/2000/terrence-malick/thinredline/",
            "Schaffer considers how shifts in point of view and the movement of the soldiers up the hill convey fear as both an individual and a collective experience.",
            "Bill Schaffer"),
    curated("The Thin Red Line", 1998, "BFI", "10 great films of 1998",
            "https://www.bfi.org.uk/lists/10-great-films-1998",
            "Baughan argues that the performances and the visceral depiction of Guadalcanal give Malick’s contemplative, impressionistic war film its coherence.",
            "Nikki Baughan"),
    curated("Hacksaw Ridge", 2016, "BFI", "10 great court-martial dramas",
            "https://www.bfi.org.uk/lists/10-great-court-martial-dramas",
            "Barnard highlights Desmond Doss’s refusal to carry a weapon and the film’s abrupt shift from courtroom melodrama to graphic combat.",
            "Tom Barnard"),
    curated("Midway", 1976, "RogerEbert.com", "Midway (1976)",
            "https://www.rogerebert.com/reviews/midway-1976",
            "Ebert finds the strategy scenes clear but argues that the use of archival footage and the emphasis on spectacle make the battle difficult to follow.",
            "Roger Ebert"),
    curated("Midway", 2019, "RogerEbert.com", "Midway (2019)",
            "https://www.rogerebert.com/reviews/midway-movie-review-2019",
            "Henderson praises the sound mix and finds the Pearl Harbor sequence effective, but criticizes the thin characters and repetitive combat scenes.",
            "Odie Henderson"),
)
BFI_MCU_GUIDE = "https://www.bfi.org.uk/features/where-begin-marvel-cinematic-universe"
MARVEL_READINGS = (
    curated("Iron Man", 2008, "BFI", "Where to begin with the Marvel Cinematic Universe",
            BFI_MCU_GUIDE,
            "O’Callaghan recommends starting with Tony Stark’s origin story, highlighting Downey’s performance and the brisk opening that introduces the character.",
            "Paul O’Callaghan"),
    curated("Thor", 2011, "BFI", "Where to begin with the Marvel Cinematic Universe", BFI_MCU_GUIDE,
            "O’Callaghan discusses how Thor brings Norse mythology and Asgard into the MCU and introduces Loki before the first Avengers crossover.",
            "Paul O’Callaghan"),
    curated("Captain America: The First Avenger", 2011, "BFI",
            "Where to begin with the Marvel Cinematic Universe", BFI_MCU_GUIDE,
            "The guide offers Steve Rogers’s wartime origin as an alternative starting point and examines how the film uses his history as a propaganda figure.",
            "Paul O’Callaghan"),
    curated("The Avengers", 2012, "BFI", "Where to begin with the Marvel Cinematic Universe",
            BFI_MCU_GUIDE,
            "O’Callaghan treats the first ensemble film as a test of whether viewers enjoy the series, and suggests watching a couple of origin stories before it.",
            "Paul O’Callaghan"),
    curated("Guardians of the Galaxy", 2014, "BFI",
            "Where to begin with the Marvel Cinematic Universe", BFI_MCU_GUIDE,
            "The guide singles out James Gunn’s space adventure as a self-contained introduction to the MCU’s comic and cosmic side.",
            "Paul O’Callaghan"),
    curated("Avengers: Infinity War", 2018, "BFI / Sight and Sound",
            "Avengers: Infinity War review: Marvel’s space mosaic punches for the stars",
            "https://www.bfi.org.uk/sight-and-sound/reviews/avengers-infinity-war-review-marvels-space-mosaic-punches-stars",
            "Newman explores how the film ties multiple series together through the quest for the Infinity Stones and singles out the humor between unlikely allies.",
            "Kim Newman"),
    curated("Avengers: Endgame", 2019, "BFI / Sight and Sound",
            "Avengers: Endgame review: the finale these heroes deserve",
            "https://www.bfi.org.uk/sight-and-sound/reviews/avengers-endgame-review-finale-these-heroes-deserve",
            "Newman praises the finale’s character moments and closure. He notes that several returning characters and cameos assume viewers remember earlier films.",
            "Kim Newman"),
)
SPIDER_MAN_READINGS = (
    curated("Spider-Man: Homecoming", 2017, "BFI / Sight and Sound",
            "Spider-Man: Homecoming review – Peter Parker as a humble high-school hero",
            "https://www.bfi.org.uk/sight-and-sound/reviews/spider-man-homecoming-review-peter-parker-humble-high-school-hero",
            "Sight and Sound praises Jon Watts’s reboot for skipping a repeated origin story and grounding Tom Holland’s Peter Parker in a brisk high-school story with working-class stakes."),
    curated("Spider-Man: Far From Home", 2019, "BFI / Sight and Sound",
            "Spider-Man: Far from Home review – insider japery and daffy humour",
            "https://www.bfi.org.uk/sight-and-sound/reviews/spider-man-far-from-home-review-insider-japery-daffy-humour",
            "Sight and Sound describes the European school-trip sequel as a lighter post-Endgame follow-up in which Peter Parker tries to step out from Tony Stark’s shadow."),
    curated("Spider-Man: No Way Home", 2021, "BFI / Sight and Sound",
            "Spider-Man: No Way Home offers a satisfying trip through the multiverse",
            "https://www.bfi.org.uk/sight-and-sound/reviews/spider-man-no-way-home-is-satisfying-trip-through-multiverse",
            "Sight and Sound examines how No Way Home uses the multiverse to unite characters from earlier Spider-Man series while resetting Peter Parker’s personal life."),
)
READINGS = (
    MIDDLE_AGES + NORSE + ROMANIAN_POLITICAL_CINEMA + ROMANIAN_NEW_WAVE_READINGS + ITALIAN_NEOREALISM_READINGS
    + PACIFIC_WAR_READINGS + MARVEL_READINGS + SPIDER_MAN_READINGS
) + (
    curated("Jan Žižka", 1956, "Czech National Film Archive",
            "Jan Žižka", "https://nfa.cz/cs/25486-jan-zizka",
            "Vávra’s film follows the Hussite movement after the death of Wenceslaus IV, with Žižka cast as a leader of the popular revolt."),
    curated("Andrei Rublev", 1966, "Criterion", "Andrei Rublev: An Icon Emerges",
            "https://www.criterion.com/current/posts/43-andrei-rublev",
            "Hoberman reads Tarkovsky’s episodic portrait as an invented biography of the icon painter against the violence of medieval Russia.",
            "J. Hoberman"),
    curated("Andrei Rublev", 1966, "Senses of Cinema", "The Passion According to Andrei: Andrei Rublev",
            "https://www.sensesofcinema.com/2001/cteq/andrei_rublev/",
            "Dzenis traces the film’s delayed public release and explores how Rublev’s struggle can illuminate Tarkovsky’s own position as an artist.",
            "Anna Dzenis"),
)


def curated_candidates(constraints: Constraints, terms: list[str]) -> list[FilmHint]:
    query = search_key(" ".join([constraints.theme, constraints.story_period, constraints.region, *terms]))
    rows = ()
    if any(contains_term(query, term) for term in ("bohemia", "bohemian", "波希米亚", "波西米亚")):
        rows = MEDIEVAL_BOHEMIA
    elif any(contains_term(query, term) for term in ("middle ages", "medieval", "中世纪")):
        rows = MIDDLE_AGES
    if any(contains_term(query, term) for term in ("norse", "viking", "vikings", "北欧神话")):
        rows += NORSE
    if any(contains_term(query, term) for term in ("romania", "romanian", "securitate")) and any(
        contains_term(query, term) for term in ("surveillance", "revolution", "political memory", "communism")
    ):
        rows += ROMANIAN_POLITICAL_CINEMA
    if contains_term(query, "romanian new wave"):
        rows += ROMANIAN_NEW_WAVE_READINGS
    if any(contains_term(query, term) for term in ("italian neorealism", "italian neo realism", "italian neorealist")):
        rows += ITALIAN_NEOREALISM_READINGS
    marvel_query = search_key(f"{constraints.theme} {constraints.topic}")
    if any(contains_term(marvel_query, term) for term in ("spider man", "spiderman", "brand new day")):
        rows += SPIDER_MAN_READINGS + tuple(row for row in MARVEL_READINGS if row.title == "Avengers: Endgame")
    elif any(contains_term(marvel_query, term) for term in ("marvel cinematic universe", "mcu", "marvel", "avengers", "infinity saga")):
        rows += MARVEL_READINGS
    hints = {}
    for row in rows:
        if constraints.release_from and row.year < constraints.release_from:
            continue
        if constraints.release_to and row.year > constraints.release_to:
            continue
        hints.setdefault((row.title, row.year), FilmHint(title=row.title, year=row.year))
    return list(hints.values())


CORE_PUBLICATIONS = {
    "criterion.com": "Criterion",
    "bfi.org.uk": "BFI / Sight and Sound",
    "sensesofcinema.com": "Senses of Cinema",
}
EXTENDED_PUBLICATIONS = {
    "theguardian.com": "The Guardian",
    "bbc.com": "BBC Culture",
    "variety.com": "Variety",
    "filmcomment.com": "Film Comment",
    "rogerebert.com": "RogerEbert.com",
}
PUBLICATIONS = {**CORE_PUBLICATIONS, **EXTENDED_PUBLICATIONS}
SENSES_API = "https://www.sensesofcinema.com/wp-json/wp/v2"
FILM_COMMENT_API = "https://www.filmcomment.com/wp-json/wp/v2"


def normalize_reference_url(url: str) -> str:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if parsed.scheme == "http" and host in PUBLICATIONS:
        return url.replace("http://", "https://", 1)
    return url


def article_url(url: str, host: str) -> bool:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc.lower() not in {host, f"www.{host}"}:
        return False
    path = parsed.path.rstrip("/")
    if host == "criterion.com":
        return bool(re.fullmatch(r"/current/posts/[0-9]+-[a-z0-9-]+", path))
    if host == "bfi.org.uk":
        return bool(re.fullmatch(
            r"/(?:(?:sight-and-sound/)?(?:lists|features)|sight-and-sound/(?:reviews|interviews|news))/[a-z0-9-]+",
            path,
        ))
    if host == "sensesofcinema.com":
        return bool(re.fullmatch(r"/[0-9]{4}/[^/]+/[^/]+", path))
    if host == "theguardian.com":
        return bool(re.fullmatch(r"/film/[0-9]{4}/[a-z]{3}/[0-9]{2}/[a-z0-9-]+", path))
    if host == "bbc.com":
        return bool(re.fullmatch(r"/culture/article/[0-9a-z-]+", path))
    if host == "variety.com":
        return bool(re.fullmatch(r"/(?:[0-9]{4}/film/(?:reviews|features|news)/[a-z0-9-]+|review/[A-Za-z0-9-]+)", path))
    if host == "filmcomment.com":
        return bool(re.fullmatch(r"/(?:article|blog)?/?[a-z0-9-]{4,}", path))
    if host == "rogerebert.com":
        return bool(re.fullmatch(r"/reviews/[a-z0-9-]+", path))
    return False


@dataclass(frozen=True)
class EditorialMatch:
    reading: FurtherReading
    passage: str = ""

    def to_evidence(self, film: FilmProjection) -> EvidenceDraft:
        return EvidenceDraft(
            film_id=film.id, source_url=str(self.reading.url), source_title=self.reading.title,
            locator=self.reading.publication, body=self.reading.summary or self.passage,
            kind=EvidenceKind.EDITORIAL,
        )


def reading_excerpt(text: str, film: FilmProjection) -> str | None:
    terms = list(re.finditer(r"[^\W_]+", text))
    keys = [search_key(term[0]) for term in terms]
    aliases = [search_key(title).split() for title in (film.title, film.original_title) if search_key(title)]
    mention = next((
        index for index in range(len(terms))
        if any(keys[index:index + len(alias)] == alias for alias in aliases)
    ), 0)
    start = terms[mention - 4].start() if mention > 4 else 0
    words = text[start:].split()
    if not words:
        return None
    return ("…" if start else "") + " ".join(words[:20]) + ("…" if len(words) > 20 else "")


def film_passage(film: FilmProjection, headline: str, text: str) -> str:
    titles = {film.title, film.original_title} - {""}
    identities = {str(film.year), *film.director.split(" / ")} - {""}
    headline_key = search_key(headline)
    title_in_headline = any(contains_term(headline_key, title) for title in titles)
    dedicated = title_in_headline and any(
        contains_term(search_key(text), identity) for identity in identities
    )
    sentences = re.split(r"(?<!\bMr\.)(?<!\bMrs\.)(?<!\bDr\.)(?<=[.!?])\s+", text)
    for index, sentence in enumerate(sentences):
        normalized = search_key(sentence)
        if not any(contains_term(normalized, title) for title in titles) and not (index == 0 and title_in_headline):
            continue
        # Start at the film mention in long festival roundups and skip menus and introductions.
        excerpt = " ".join(sentences[index:index + 3])[:900].strip()
        if re.search(r"\b(?:prod co|cast|print source)\s*:", excerpt, re.I):
            continue
        if re.search(r"\b(?:at the end|closing scene|final shot|the film ends)\b", excerpt, re.I):
            continue
        context = search_key(f"{headline} {excerpt}")
        if len(excerpt.split()) >= 10 and (
            dedicated or any(contains_term(context, identity) for identity in identities)
        ):
            return excerpt
    return ""


class EditorialResearch:
    def __init__(self, http: httpx.AsyncClient, options: ToolsOptions, crawler: WebCrawler):
        self.http = http
        self.options = options
        self.searches = []
        if options.brave.enabled:
            self.searches.append(BraveSearchProvider(http, options.brave))
        if options.duckduckgo.enabled:
            self.searches.append(DuckDuckGoSearchProvider(
                options.duckduckgo, options.crawler.model_copy(update={"enabled": False}), crawler,
            ))

    async def find(
        self, film: FilmProjection, saved: Sequence[EvidenceRecord] = (), *, focus: str = "", live: bool = True,
    ) -> list[EditorialMatch]:
        matches = self.saved_matches(film, saved)
        covered = {urlsplit(url).hostname.removeprefix("www.") for url in matches}
        core_hosts = [host for host in CORE_PUBLICATIONS if host not in covered]
        if live and core_hosts:
            references = [normalize_reference_url(url) for url in await self._reference_urls(film)]
            extended_hosts = [
                host for host in EXTENDED_PUBLICATIONS
                if host not in covered and (
                    host == "filmcomment.com" or any(article_url(url, host) for url in references)
                )
            ]
            hosts = [*core_hosts, *extended_hosts]
            found = await asyncio.gather(*(
                self._find_at(host, film, focus, tuple(url for url in references if article_url(url, host)))
                for host in hosts
            ))
            matches.update((str(match.reading.url), match) for match in found if match)
        return list(matches.values())

    @staticmethod
    def saved_matches(film: FilmProjection, saved: Sequence[EvidenceRecord]) -> dict[str, EditorialMatch]:
        matches = {
            str(row.reading.url): EditorialMatch(row.reading)
            for row in READINGS if row.matches(film)
        }
        for item in saved:
            matches.setdefault(item.source_url, EditorialMatch(FurtherReading(
                publication=item.locator, title=item.source_title, url=item.source_url, origin="research",
                excerpt=reading_excerpt(item.body, film),
            ), item.body))
        return matches

    async def _reference_urls(self, film: FilmProjection) -> list[str]:
        if not self.options.wikipedia_enabled or not self.options.crawler.enabled:
            return []
        try:
            return await WikipediaSearchProvider(self.http).film_reference_urls(film)
        except (httpx.HTTPError, ValueError):
            logger.debug("Editorial reference lookup unavailable")
            return []

    async def _find_at(
        self, host: str, film: FilmProjection, focus: str, references: tuple[str, ...],
    ) -> EditorialMatch | None:
        checked = set()
        reference = None
        try:
            async with asyncio.timeout(8):
                async for rows in self._candidates_at(host, film, focus, references):
                    for row in rows:
                        if not article_url(row.url, host):
                            continue
                        title = row.title
                        passage = film_passage(film, title, row.snippet)
                        if not passage and self.options.crawler.enabled and row.url not in checked:
                            checked.add(row.url)
                            try:
                                page = await self._publisher_page(row.url, host)
                            except httpx.HTTPStatusError:
                                slug = search_key(urlsplit(row.url).path.split("/")[-1])
                                if host in {"criterion.com", "rogerebert.com"} and row.url in references and any(
                                    len(search_key(name)) >= 3 and contains_term(slug, name)
                                    for name in (film.title, film.original_title)
                                ):
                                    label = "Criterion essay" if host == "criterion.com" else "RogerEbert.com review"
                                    reference = EditorialMatch(FurtherReading(
                                        publication=PUBLICATIONS[host], title=f"{label} on {film.title}",
                                        url=row.url, origin="reference",
                                    ))
                            else:
                                if page:
                                    title, content = page
                                    passage = film_passage(film, title, content)
                        if passage:
                            return EditorialMatch(FurtherReading(
                                publication=PUBLICATIONS[host], title=title or row.title, url=row.url, origin="web",
                                excerpt=reading_excerpt(passage, film),
                            ), passage)
        except (TimeoutError, ValueError):
            logger.debug("Editorial search ended without a match publication=%s", PUBLICATIONS[host])
        return reference

    async def _publisher_page(self, url: str, host: str) -> tuple[str, str] | None:
        # For these three fixed hosts and validated article paths, never follow a publisher redirect.
        # General web fetches still use the crawler's public-address and redirect checks.
        if not article_url(url, host):
            return None
        try:
            async with self.http.stream(
                "GET", url, headers={"User-Agent": self.options.crawler.user_agent}, timeout=3,
                follow_redirects=False,
            ) as response:
                if response.status_code in {401, 403, 429}:
                    response.raise_for_status()
                if response.status_code != 200 or "text/html" not in response.headers.get("content-type", ""):
                    return None
                parser = VisibleHTMLParser()
                decoder = getincrementaldecoder(response.encoding or "utf-8")(errors="replace")
                remaining = self.options.crawler.response_byte_limit
                async for chunk in response.aiter_bytes():
                    parser.feed(decoder.decode(chunk[:remaining]))
                    remaining -= len(chunk)
                    if remaining <= 0:
                        break
                parser.close()
                return parser.title, parser.text
        except (httpx.RequestError, LookupError, ValueError):
            return None

    async def _candidates_at(
        self, host: str, film: FilmProjection, focus: str, references: tuple[str, ...],
    ) -> AsyncIterator[list[SearchResult]]:
        if host == "sensesofcinema.com" and self.options.crawler.enabled:
            yield await self._wp_candidates(film, SENSES_API, "sensesofcinema.com")
        if host == "filmcomment.com" and self.options.crawler.enabled:
            yield await self._wp_candidates(film, FILM_COMMENT_API, "filmcomment.com")
        if references:
            ordered = sorted(references, key=lambda url: not contains_term(
                search_key(urlsplit(url).path.split("/")[-1]), film.title,
            ))
            yield [SearchResult(url, "", "") for url in ordered[:3]]
        if host not in CORE_PUBLICATIONS:
            return
        titles = [film.title]
        if film.original_title and search_key(film.original_title) != search_key(film.title):
            titles.append(film.original_title)
        for title in titles:
            query = f'site:{host} "{title}" {film.director}'
            for search in self.searches:
                try:
                    rows = await search.search(query)
                except (httpx.HTTPError, ValueError):
                    continue
                yield [SearchResult(row.source_url, row.source_title, row.body) for row in rows]
        if focus and self.searches:
            query = f'site:{host} "{film.title}" {search_key(focus)[:100]}'
            try:
                rows = await self.searches[0].search(query)
            except (httpx.HTTPError, ValueError):
                return
            yield [SearchResult(row.source_url, row.source_title, row.body) for row in rows]

    async def _wp_candidates(self, film: FilmProjection, api_base: str, host: str) -> list[SearchResult]:
        queries = dict.fromkeys(filter(None, (film.original_title, film.title, film.director)))
        responses = await asyncio.gather(*(self._wp_search(api_base, query) for query in queries))
        candidates = {}
        for rows in responses:
            for row in rows:
                if not isinstance(row, dict) or type(row.get("id")) is not int:
                    continue
                title = row.get("title")
                if isinstance(title, str) and not contains_term(search_key(title), "world poll"):
                    candidates.setdefault(row["id"], row)

        def priority(row):
            heading = search_key(re.sub(r"<[^>]+>", "", unescape(row["title"])))
            exact = any(contains_term(heading, title) for title in (film.title, film.original_title))
            essay = any(section in str(row.get("url", "")) for section in ("/cteq/", "/feature-articles/", "/article/"))
            return not exact, not essay

        ordered = sorted(candidates.values(), key=priority)
        posts = await asyncio.gather(*(self._wp_post(api_base, host, row) for row in ordered[:4]))
        return [post for post in posts if post is not None]

    async def _wp_search(self, api_base: str, query: str) -> list[object]:
        try:
            response = await self.http.get(
                f"{api_base}/search", params={"search": query, "per_page": 5, "subtype": "post"},
                timeout=3, follow_redirects=False,
            )
            response.raise_for_status()
            payload = response.json()
            return payload[:5] if isinstance(payload, list) else []
        except (httpx.HTTPError, ValueError):
            logger.debug("WordPress editorial search unavailable api=%s", api_base)
            return []

    async def _wp_post(self, api_base: str, host: str, row: object) -> SearchResult | None:
        if not isinstance(row, dict):
            return None
        post_id, url, headline = row.get("id"), row.get("url"), row.get("title")
        if not (type(post_id) is int and post_id > 0 and isinstance(url, str)
                and isinstance(headline, str) and article_url(url, host)):
            return None
        title = unescape(re.sub(r"<[^>]+>", "", headline))
        try:
            response = await self.http.get(f"{api_base}/posts/{post_id}", params={"_fields": "content"},
                                           timeout=3, follow_redirects=False)
            response.raise_for_status()
            payload = response.json()
            content = payload.get("content") if isinstance(payload, dict) else None
            if isinstance(content, dict) and isinstance(content.get("rendered"), str):
                parser = VisibleHTMLParser()
                parser.feed(content["rendered"][:self.options.crawler.response_byte_limit])
                parser.close()
                return SearchResult(url, title, parser.text)
        except (httpx.HTTPError, ValueError):
            logger.debug("WordPress editorial post lookup unavailable api=%s", api_base)
        return SearchResult(url, title)


class WebFilmReadings:
    def __init__(self, options: Options[ToolsOptions]):
        self.options = options.value

    async def for_film(
        self, film: FilmProjection, saved: Sequence[EvidenceRecord] = (), *, live: bool = False,
    ) -> list[FurtherReading]:
        if not live:
            return [match.reading for match in EditorialResearch.saved_matches(film, saved).values()]
        async with httpx.AsyncClient(timeout=min(self.options.timeout_seconds, 6)) as http:
            crawler = WebCrawler(http, self.options.crawler)
            matches = await EditorialResearch(http, self.options, crawler).find(film, saved)
        return [match.reading for match in matches]
