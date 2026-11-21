import re

from ...contracts import Assessment, Guide, ParsedRequest, Plan, TimelineResearch
from ...ai.types import (
    DecisionStatus,
    EvidenceKind,
    ModelMode,
    ResearchTimelineAxis,
    TimelineAxis,
)
from ..demo_data import (
    ITALIAN_NEOREALISM,
    MARVEL,
    MEDIEVAL,
    MEMORY,
    NORSE,
    ROMANIAN_NEW_WAVE,
    SPIDER_MAN,
    SURVEILLANCE,
)

THEMES = {
    MEDIEVAL: ("kingdom come: deliverance ii", "medieval", "middle ages"),
    ROMANIAN_NEW_WAVE: ("romanian new wave",),
    ITALIAN_NEOREALISM: ("italian neorealism", "italian neo realism", "italian neo-realism"),
    SPIDER_MAN: ("spider-man: brand new day", "brand new day", "spider-man", "spiderman"),
    MARVEL: ("marvel cinematic universe", "marvel films", "mcu"),
    NORSE: ("北欧神话", "诸神黄昏", "norse", "ragnarök", "ragnarok", "valhalla"),
    SURVEILLANCE: ("监控", "监听", "surveillance", "stasi"),
    MEMORY: ("政治记忆", "罗马尼亚革命", "romanian revolution", "齐奥塞斯库", "political memory", "ceausescu", "ceaus"),
}
RESEARCH_TITLES = {
    MEDIEVAL: "Medieval European History on Film",
    ROMANIAN_NEW_WAVE: "Films of the Romanian New Wave",
    ITALIAN_NEOREALISM: "Italian Neorealism on Film",
    SPIDER_MAN: "Spider-Man: Brand New Day Prerequisites",
    MARVEL: "Building the Marvel Cinematic Universe",
    NORSE: "Norse Mythology on Film",
    SURVEILLANCE: "State Surveillance on Film",
    MEMORY: "Romanian Political Memory on Film",
}
REGIONS = {
    "欧洲": {"DK", "GB", "SE", "NO", "DE", "RO", "BE"},
    "北欧": {"DK", "SE", "NO"},
    "德国": {"DE"}, "罗马尼亚": {"RO"}, "挪威": {"NO"}, "丹麦": {"DK"},
    "美国": {"US"}, "中国": {"CN"}, "英国": {"GB"}, "法国": {"FR"},
    "日本": {"JP"}, "韩国": {"KR"}, "意大利": {"IT"},
}


class MockModel:
    mode = ModelMode.MOCK

    def set_run_id(self, run_id) -> None:
        pass

    async def close(self):
        pass

    async def parse(self, prompt):
        lower = prompt.casefold()
        theme = next((key for key, names in THEMES.items() if any(name in lower for name in names)), "")
        count_match = re.search(r"([1-5一二两三四五])\s*(?:部|个)|\b([1-5]|one|two|three|four|five)\s+(?:films?|movies?)\b", lower)
        count = 3
        if count_match:
            value = count_match.group(1) or count_match.group(2)
            counts = dict(zip(("one", "two", "three", "four", "five", *"一二两三四五"), (1, 2, 3, 4, 5, 1, 2, 2, 3, 4, 5)))
            count = int(value) if value.isdecimal() else counts[value]
        region = next((name for name in REGIONS if name in prompt.replace("北欧神话", "")), "")
        if not region and theme not in {MEDIEVAL, ROMANIAN_NEW_WAVE, ITALIAN_NEOREALISM, SPIDER_MAN, MARVEL}:
            region = next((label for keyword, label in (("europe", "欧洲"), ("romanian", "罗马尼亚"),
                          ("german", "德国"), ("american", "美国")) if keyword in lower), "")
        year_range = re.search(r"((?:18|19|20)\d\d)\s*(?:年\s*)?(?:至|到|[-–—]|to)\s*((?:18|19|20)\d\d)", lower)
        decade = re.search(r"((?:18|19|20)\d0)\s*(?:年代|s\b)", lower)
        explicit_year = re.search(r"((?:18|19|20)\d\d)\s*年?\s*上映|released in ((?:18|19|20)\d\d)\b", lower)
        start, end = map(int, year_range.groups()) if year_range else (
            (int(decade[1]), int(decade[1]) + 9) if decade else (
                (int(explicit_year[1] or explicit_year[2]), int(explicit_year[1] or explicit_year[2])) if explicit_year else (None, None)))
        story_period = ""
        clarification = ""
        if "上映" not in lower and "released" not in lower and (start or end):
            story_period = str(start) if start == end else f"{start}–{end}"
            if "故事" in lower or "set in" in lower:
                start, end = None, None
            else:
                story_period = ""
                clarification = "The dates were interpreted as a release range. You can edit the range or specify a story period."
        axis = TimelineAxis.MYTH if theme == NORSE else TimelineAxis.HISTORY if theme == MEMORY else TimelineAxis.RELEASE
        if "故事时间线" in prompt or "story timeline" in lower:
            axis = TimelineAxis.STORY
        elif "上映时间线" in prompt or "release timeline" in lower:
            axis = TimelineAxis.RELEASE
        if not theme:
            clarification = "Try one of the demo topics, such as Italian neorealism or Spider-Man: Brand New Day."
        return ParsedRequest.model_validate({
            "constraints": {"topic": prompt, "theme": theme, "research_title": RESEARCH_TITLES.get(theme, ""),
                            "count": count, "region": region,
                            "release_from": start, "release_to": end, "story_period": story_period, "axis": axis,
                            "setting_match": "nearby" if theme == MEDIEVAL and not story_period else "strict"},
            "clarification": clarification,
        })

    async def resolve_reference(self, prompt, sources):
        return None

    async def plan(self, constraints):
        return Plan(query=constraints.theme or constraints.topic[:250])

    async def assess(self, constraints, films, evidence):
        decisions = []
        for film in films:
            sources = [item for item in evidence if item.film_id == film.id and item.kind != EvidenceKind.METADATA]
            metadata = [item.id for item in evidence if item.film_id == film.id and item.kind == EvidenceKind.METADATA]
            allowed = REGIONS.get(constraints.region)
            reason = ""
            status = DecisionStatus.ACCEPTED
            if constraints.region and not allowed:
                status, reason = DecisionStatus.NEEDS_EVIDENCE, "The demo does not cover this production region."
            elif allowed and not allowed.intersection(film.countries):
                status, reason = DecisionStatus.REJECTED, "The production countries fall outside the requested region."
            elif (constraints.release_from and film.year < constraints.release_from) or (
                constraints.release_to and film.year > constraints.release_to
            ):
                status, reason = DecisionStatus.REJECTED, f"The film was released in {film.year}, outside the requested range."
            elif "秘密警察" in constraints.topic and film.slug == "police-2009":
                status, reason = DecisionStatus.REJECTED, "The sources describe routine police surveillance."
            elif constraints.movement:
                status, reason = DecisionStatus.NEEDS_EVIDENCE, "The sources do not verify a connection to the requested film movement."
            elif constraints.story_period:
                wanted = [int(y) for y in re.findall(r"(?:18|19|20)\d\d", constraints.story_period)]
                known = [int(y) for y in re.findall(r"(?:18|19|20)\d\d", film.story)]
                if not wanted or not known:
                    status, reason = DecisionStatus.NEEDS_EVIDENCE, "The sources do not establish a story date."
                elif max(wanted) < min(known) or min(wanted) > max(known):
                    status, reason = DecisionStatus.REJECTED, "The documented story period falls outside the requested range."
            if status == DecisionStatus.ACCEPTED and not sources:
                status, reason = DecisionStatus.NEEDS_EVIDENCE, "No sources directly connect the film to the topic."
            decisions.append({
                "film_id": film.id, "status": status, "reason": reason or film.reason,
                "evidence_ids": [item.id for item in sources[:1]] + metadata[:1],
                "next_query": f"{constraints.theme} OR {film.original_title}" if status == DecisionStatus.NEEDS_EVIDENCE else "",
            })
        return Assessment.model_validate({"decisions": decisions})

    async def timeline(self, constraints, films, evidence):
        by_slug = {film.slug: film.id for film in films}
        by_locator = {item.locator: item.id for item in evidence}
        events = []
        edges = []
        for film in films:
            year = re.search(r"(?<!\d)[12]\d{3}(?!\d)", film.story)
            if year and film.slug in by_locator:
                events.append({"id": film.slug, "axis": ResearchTimelineAxis.STORY, "label": film.title,
                               "period": film.story, "sort_year": int(year[0]),
                               "film_ids": [film.id], "relation": "Story or archival setting",
                               "evidence_ids": [by_locator[film.slug]]})
        if "myth" in by_locator:
            phases = [
                ("creation", "Creation", []),
                ("gods", "The world of gods and humans", ["valhalla-1986", "thor-2011"]),
                ("ragnarok", "Ragnarök", ["ragnarok-2013"]),
                ("renewal", "The world renewed", []),
            ]
            for index, (key, label, slugs) in enumerate(phases):
                attached = [slug for slug in slugs if slug in by_slug and slug in by_locator]
                events.append({"id": key, "axis": ResearchTimelineAxis.MYTH, "label": label, "period": f"Stage {index + 1}",
                               "version": "Völuspá · National Museum of Denmark",
                               "film_ids": [by_slug[slug] for slug in attached],
                               "relation": "Motif or adaptation",
                               "evidence_ids": [by_locator["myth"], *[by_locator[slug] for slug in attached]]})
                if index:
                    edges.append({"earlier": phases[index - 1][0], "later": key, "evidence_ids": [by_locator["myth"]]})
        history = [
            ("leadership", "Ceaușescu comes to power", 1965, "Period covered by archival footage", ("autobiography-2010",)),
            ("decree-770", "Romania issues Decree 770", 1966, "Legal context for the story", ("four-months-2007",)),
            ("revolution", "Romanian Revolution", 1989, "Retrospective or archival depiction", ("east-2006", "autobiography-2010")),
        ]
        for key, label, year, relation, slugs in history:
            if key in by_locator:
                linked = [slug for slug in slugs if slug in by_slug and slug in by_locator]
                events.append({"id": key, "axis": ResearchTimelineAxis.HISTORY, "label": label, "period": f"{year}",
                               "sort_year": year, "film_ids": [by_slug[slug] for slug in linked], "relation": relation,
                               "evidence_ids": [by_locator[key], *[by_locator[slug] for slug in linked]]})
        return TimelineResearch.model_validate({"events": events, "order": edges})

    async def generate(self, constraints, films, evidence, correction=""):
        common = [{"title": item.source_title.split(" · ")[-1], "text": item.body, "evidence_ids": [item.id]}
                  for item in evidence if item.kind == EvidenceKind.CONTEXT]
        guides = []
        if constraints.theme in {MARVEL, SPIDER_MAN}:
            films = sorted(films, key=lambda film: (film.year, film.slug))
        for film in films:
            source = next(item for item in evidence if item.film_id == film.id and item.kind == EvidenceKind.FILM)
            review = next((item for item in evidence if item.film_id == film.id and item.kind == EvidenceKind.EDITORIAL), None)
            reason = {"title": "Why it fits", "text": film.reason, "evidence_ids": [(review or source).id]}
            primer = {"title": "Before you watch", "text": source.body, "evidence_ids": [source.id]}
            guides.append({"film_id": film.id, "reason": reason, "primer": [primer], "background": []})
        return Guide.model_validate({
            "title": RESEARCH_TITLES.get(constraints.theme, constraints.topic),
            "introduction": "Compare how the films approach the same theme through their settings, sources, and adaptations.",
            "common_primer": common[:5], "film_guides": guides,
            "viewing_order": [film.id for film in films],
            "discussion_questions": ["Whose perspectives shape the way each film presents the theme?", "What does each film preserve or change from its source material?"],
        })


class MockModelProvider:
    mode = ModelMode.MOCK

    async def readiness(self) -> None:
        return None
    model_id = None

    def create(self) -> MockModel:
        return MockModel()
