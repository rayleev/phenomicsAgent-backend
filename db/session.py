from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from config.loader import get_database_url

DATABASE_URL = get_database_url()

engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncSession:
    """Yield an async DB session."""
    async with AsyncSessionLocal() as session:
        yield session
