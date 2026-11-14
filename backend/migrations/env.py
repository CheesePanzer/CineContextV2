import asyncio
import os

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from cinecontext.domain import Entity


def migrate(connection):
    context.configure(connection=connection, target_metadata=Entity.metadata)
    with context.begin_transaction():
        context.run_migrations()


async def main():
    engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        async with engine.connect() as connection:
            await connection.run_sync(migrate)
    finally:
        await engine.dispose()


asyncio.run(main())
