from __future__ import annotations

import asyncio

from database.session import DatabaseSettings, create_engine, initialize_schema


async def main() -> None:
    settings = DatabaseSettings.from_env()
    if not settings.is_configured:
        raise SystemExit("DATABASE_URL is required to initialize the local database")

    engine = create_engine(settings)
    try:
        await initialize_schema(engine)
    finally:
        await engine.dispose()

    print("local_database_schema_ready")


if __name__ == "__main__":
    asyncio.run(main())
