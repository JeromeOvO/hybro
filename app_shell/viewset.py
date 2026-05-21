from app_shell.bound import (
    ViewSetDatabaseProvider,
    ViewSetOperation,
    ViewSetRepository,
    ViewSetRepositoryFactory,
    ViewSetResult,
)


class AppShellViewSetRepositoryProvider:
    def __init__(
        self,
        *,
        db_provider: ViewSetDatabaseProvider,
        create_repository: ViewSetRepositoryFactory,
    ) -> None:
        self._db_provider = db_provider
        self._create_repository = create_repository

    def get_repository(
        self, *, collection_name: str, pk_field: str = "_id"
    ) -> ViewSetRepository:
        return self._create_repository(
            collection_name=collection_name,
            db=self._db_provider(),
            pinecone=None,
            pk_field=pk_field,
        )

    async def run_in_transaction(
        self, operation: ViewSetOperation
    ) -> ViewSetResult:
        db = self._db_provider()
        async with await db.client.start_session() as session:
            async with session.start_transaction():
                return await operation()


__all__ = ["AppShellViewSetRepositoryProvider"]
