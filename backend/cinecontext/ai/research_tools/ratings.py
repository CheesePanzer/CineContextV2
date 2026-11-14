from datetime import datetime, timezone
from uuid import UUID

from ...application.ports import CurationPersistence
from ...contracts import Rating


class RatingStore:
    def __init__(self, persistence: CurationPersistence):
        self.persistence = persistence

    async def save(self, film_id: UUID, ratings: list[Rating], provider: str) -> None:
        if not ratings:
            return
        rows = [
            {
                "origin": rating.origin,
                "metric": rating.metric,
                "payload": {
                    **rating.model_dump(mode="json"),
                    "provider": provider,
                    "status": "available",
                    "as_of": datetime.now(timezone.utc).isoformat(),
                },
            }
            for rating in ratings
        ]
        await self.persistence.save_ratings(film_id, rows)
