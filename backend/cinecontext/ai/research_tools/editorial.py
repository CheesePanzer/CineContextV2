import asyncio
import re
from dataclasses import dataclass
from unicodedata import combining, normalize
from urllib.parse import urlsplit

import httpx
from alcedo import Options

from ...contracts import Constraints, FilmHint, FurtherReading
from ...settings import ToolsOptions
from ..types import EvidenceDraft, EvidenceKind, FilmProjection
from .brave import BraveSearchProvider
from .crawler import WebCrawler
from .duckduckgo import DuckDuckGoSearchProvider


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
        publication=publication, title=headline, url=url, summary=summary, author=author,
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
            "Newman finds that the sprawling ensemble still makes room for small character moments and gives long-running arcs a satisfying conclusion.",
            "Kim Newman"),
)
READINGS = MIDDLE_AGES + NORSE + ROMANIAN_POLITICAL_CINEMA + PACIFIC_WAR_READINGS + MARVEL_READINGS + (
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
    if any(contains_term(query, term) for term in ("marvel cinematic universe", "mcu", "marvel")):
        rows += MARVEL_READINGS
    hints = {}
    for row in rows:
        if constraints.release_from and row.year < constraints.release_from:
            continue
        if constraints.release_to and row.year > constraints.release_to:
            continue
        hints.setdefault((row.title, row.year), FilmHint(title=row.title, year=row.year))
    return list(hints.values())


def curated_evidence(film: FilmProjection) -> list[EvidenceDraft]:
    return [
        EvidenceDraft(
            film_id=film.id, source_url=str(row.reading.url),
            source_title=row.reading.title, locator=row.reading.publication,
            body=row.reading.summary or "", kind=EvidenceKind.FILM,
        )
        for row in READINGS if row.matches(film)
    ]


PUBLICATIONS = {
    "criterion.com": "Criterion",
    "bfi.org.uk": "BFI / Sight and Sound",
    "sensesofcinema.com": "Senses of Cinema",
}


def article_url(url: str, host: str) -> bool:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc.lower() not in {host, f"www.{host}"}:
        return False
    path = parsed.path.rstrip("/")
    if host == "criterion.com":
        return bool(re.fullmatch(r"/current/posts/[0-9]+-[a-z0-9-]+", path))
    if host == "bfi.org.uk":
        return bool(re.fullmatch(r"/sight-and-sound/(reviews|features)/[^/]+", path))
    return bool(re.fullmatch(r"/[0-9]{4}/[^/]+/[^/]+", path))


class WebFilmReadings:
    def __init__(self, options: Options[ToolsOptions]):
        self.options = options.value

    async def for_film(self, film: FilmProjection) -> list[FurtherReading]:
        readings = [row.reading for row in READINGS if row.matches(film)]
        covered = {"BFI / Sight and Sound" if row.publication == "BFI" else row.publication for row in readings}
        hosts = [host for host, publication in PUBLICATIONS.items() if publication not in covered]
        if not hosts:
            return readings
        # Find article links on demand. The search results do not become historical claims.
        async with httpx.AsyncClient(timeout=min(self.options.timeout_seconds, 6)) as http:
            search = (BraveSearchProvider(http, self.options.brave) if self.options.brave.enabled else
                      DuckDuckGoSearchProvider(self.options.duckduckgo,
                                               self.options.crawler.model_copy(update={"enabled": False}),
                                               WebCrawler(http, self.options.crawler)))

            async def find(host):
                try:
                    query = f'site:{host} "{film.title}" {film.year} {film.director}'
                    rows = await asyncio.wait_for(search.search(query), timeout=7)
                    for row in rows:
                        if not article_url(row.source_url, host):
                            continue
                        headline = search_key(row.source_title)
                        details = search_key(f"{row.source_title} {row.body}")
                        matches_title = any(contains_term(headline, title) for title in {film.title, film.original_title})
                        matches_film = contains_term(details, str(film.year)) or any(
                            contains_term(details, director) for director in film.director.split(" / ")
                        )
                        if matches_title and matches_film:
                            return FurtherReading(publication=PUBLICATIONS[host], title=row.source_title,
                                                  url=row.source_url)
                except (TimeoutError, httpx.HTTPError, ValueError):
                    pass
                return None

            found = await asyncio.gather(*(find(host) for host in hosts))
        return readings + [row for row in found if row is not None]
