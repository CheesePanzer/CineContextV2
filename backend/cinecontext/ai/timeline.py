import re
from uuid import UUID

from ..contracts import TimelineEvent, TimelineResearch
from .types import (
    EvidenceKind,
    EvidenceRecord,
    FilmProjection,
    RESEARCH_TIMELINE_AXES,
    ResearchTimelineAxis,
    TimelineAxis,
    TimelineViewEvent,
    ValidatedTimeline,
    ValidatedTimelineEdge,
)


CALENDAR_YEAR = re.compile(r"(?<![\d,])(?:[12]\d{3}|[12],\d{3})(?!\d)")


def calendar_years(text: str) -> set[int]:
    return {int(value.replace(",", "")) for value in CALENDAR_YEAR.findall(text)}


class TimelineBuilder:
    """Build timeline views from catalog releases and validated model research.

    RELEASE is synthesized from catalog metadata. STORY, MYTH, and HISTORY are
    model-researched axes whose events and ordering require evidence validation.
    The builder is stateless so one instance can safely serve multiple runs.
    """

    def build(
        self,
        films: list[FilmProjection],
        research: TimelineResearch,
        evidence: list[EvidenceRecord],
    ) -> ValidatedTimeline:
        sources = {item.id: item for item in evidence}
        events = self._validated_events(films, research, sources)
        evidence_ids = {item.id for item in evidence if item.kind != EvidenceKind.METADATA}
        views = {TimelineAxis.RELEASE: self._release_view(films)}
        valid_edges = []
        for axis in RESEARCH_TIMELINE_AXES:
            subset = {key: event for key, event in events.items() if event.axis == axis}
            if not subset:
                continue
            ordered = self._ordered_research_view(subset, research, evidence_ids)
            if ordered:
                views[TimelineAxis(axis.value)] = ordered
                valid_edges.extend(self._valid_edges(subset, ordered, research, evidence_ids))
        return ValidatedTimeline(views=views, valid_edges=valid_edges)

    def _validated_events(
        self,
        films: list[FilmProjection],
        research: TimelineResearch,
        sources: dict[UUID, EvidenceRecord],
    ) -> dict[str, TimelineEvent]:
        film_ids = {film.id for film in films}
        events = {}
        for event in research.events:
            if (event.id in events or not set(event.evidence_ids) <= sources.keys()
                    or any(film_id not in film_ids for film_id in event.film_ids)
                    or (event.axis == ResearchTimelineAxis.MYTH and (not event.version or event.sort_year is not None))
                    or (event.axis == ResearchTimelineAxis.HISTORY and event.sort_year is None)):
                continue
            references = [ref for ref in event.evidence_ids if sources[ref].kind != EvidenceKind.METADATA]
            if not references:
                continue
            cited_films = {sources[ref].film_id for ref in references}
            event = event.model_copy(update={"evidence_ids": references})
            if event.axis == ResearchTimelineAxis.STORY and len(event.film_ids) != 1:
                continue
            if not set(event.film_ids) <= cited_films:
                continue
            if event.axis == ResearchTimelineAxis.STORY:
                film_id = event.film_ids[0]
                source_years = {
                    year for ref in event.evidence_ids if sources[ref].film_id == film_id
                    for year in calendar_years(sources[ref].body)
                }
                named_years = calendar_years(event.period)
                if not named_years <= source_years:
                    continue
                if named_years and event.sort_year not in named_years:
                    event = event.model_copy(update={"sort_year": min(named_years)})
                elif not named_years and event.sort_year not in source_years:
                    event = event.model_copy(update={"sort_year": None})
            events[event.id] = event
        return events

    def _release_view(self, films: list[FilmProjection]) -> list[TimelineViewEvent]:
        return [
            TimelineViewEvent(
                id=str(film.id), label=film.title, period=film.releases[0].label,
                sort_year=film.year, film_ids=[film.id], evidence_ids=[], relation="Release",
            )
            for film in sorted(films, key=lambda item: (item.year, item.releases[0].label, item.title))
        ]

    def _ordered_research_view(
        self,
        events: dict[str, TimelineEvent],
        research: TimelineResearch,
        evidence_ids: set[UUID],
    ) -> list[TimelineViewEvent]:
        edges = {key: set() for key in events}
        for relation in research.order:
            left, right = relation.earlier, relation.later
            if (left in events and right in events and left != right
                    and events[left].version == events[right].version
                    and set(relation.evidence_ids) <= evidence_ids):
                edges[right].add(left)
        ordered = []
        order_group = 0
        while edges:
            layer = sorted(
                (key for key, incoming in edges.items() if not incoming),
                key=lambda key: (events[key].sort_year is None, events[key].sort_year or 0, key),
            )
            if not layer:
                break
            for key in layer:
                ordered.append(TimelineViewEvent(
                    **events[key].model_dump(), order_group=order_group,
                ))
                del edges[key]
            for incoming in edges.values():
                incoming.difference_update(layer)
            order_group += 1
        return ordered

    def _valid_edges(
        self,
        events: dict[str, TimelineEvent],
        ordered: list[TimelineViewEvent],
        research: TimelineResearch,
        evidence_ids: set[UUID],
    ) -> list[ValidatedTimelineEdge]:
        keys = {event.id for event in ordered}
        return [
            ValidatedTimelineEdge(edge.earlier, edge.later, edge.evidence_ids)
            for edge in research.order
            if edge.earlier in keys and edge.later in keys and edge.earlier != edge.later
            and set(edge.evidence_ids) <= evidence_ids
            and events[edge.earlier].version == events[edge.later].version
        ]
