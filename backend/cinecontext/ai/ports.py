from typing import Protocol

from ..contracts import Assessment, Constraints, Guide, ParsedRequest, Plan, TimelineResearch
from .types import EvidenceRecord, FilmProjection, ModelMode


class ApplicationModel(Protocol):
    mode: ModelMode

    def set_run_id(self, run_id) -> None: ...

    async def close(self) -> None: ...
    async def parse(self, prompt: str) -> ParsedRequest: ...
    async def plan(self, constraints: Constraints) -> Plan: ...
    async def assess(
        self, constraints: Constraints, films: list[FilmProjection], evidence: list[EvidenceRecord]
    ) -> Assessment: ...
    async def timeline(
        self, constraints: Constraints, films: list[FilmProjection], evidence: list[EvidenceRecord]
    ) -> TimelineResearch: ...
    async def generate(
        self, constraints: Constraints, films: list[FilmProjection],
        evidence: list[EvidenceRecord], correction: str = ""
    ) -> Guide: ...


class ApplicationModelProvider(Protocol):
    mode: ModelMode

    def create(self) -> ApplicationModel: ...
