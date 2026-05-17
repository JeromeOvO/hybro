import pinecone

from common.config import get_pinecone_api_key, get_pinecone_index_name


class PineconeDB:
    def __init__(self):
        self.index_name = get_pinecone_index_name()
        self.index = None
        self._pc: pinecone.Pinecone | None = None
        self._api_key: str | None = None
        self._indexes: dict[str, object] = {}

    def _get_client(self) -> pinecone.Pinecone:
        api_key = get_pinecone_api_key()
        if self._pc is None:
            self._pc = pinecone.Pinecone(api_key=api_key)
            self._api_key = api_key
            self._indexes.clear()
            self.index = None
        elif self._api_key is None:
            self._api_key = api_key
        elif self._api_key != api_key:
            self._pc = pinecone.Pinecone(api_key=api_key)
            self._api_key = api_key
            self._indexes.clear()
            self.index = None
        return self._pc

    def connect(self):
        self.index_name = get_pinecone_index_name()
        pc = self._get_client()
        self.index = pc.Index(self.index_name)

    def _ensure_default_index(self):
        if self.index is None or self.index_name != get_pinecone_index_name():
            self.connect()
        return self.index

    def get_index(self, index_name: str):
        """Get a Pinecone index by name, with lazy connection caching.

        Supports multiple indexes (e.g. 'agentmatch' for discovery,
        'room-memory' for memory search) without duplicating client init.
        """
        if index_name in self._indexes:
            return self._indexes[index_name]

        pc = self._get_client()
        idx = pc.Index(index_name)
        self._indexes[index_name] = idx
        return idx

    def query(self, vector, top_k=5, filter=None):
        """Query the vector database for similar vectors with optional metadata filter"""
        index = self._ensure_default_index()

        results = index.query(
            vector=vector,
            top_k=top_k,
            include_metadata=True,
            filter=filter,
        )

        return results

    def upsert(self, vectors):
        """Insert or update vectors in the database"""
        index = self._ensure_default_index()

        return index.upsert(vectors=vectors)

    def delete(self, ids):
        """Delete vectors from the database by their IDs

        Args:
            ids: A single ID string or list of IDs to delete

        Returns:
            The deletion response from Pinecone
        """
        index = self._ensure_default_index()

        return index.delete(ids=ids)


pinecone_db = PineconeDB()


def test_pinecone_connection():
    """Test if Pinecone connection is successful"""
    try:
        pinecone_db.connect()
        # Check if we can access index info to verify connection
        index_stats = pinecone_db.index.describe_index_stats()
        print(f"Connection successful! Index info: {index_stats}")
        return True
    except Exception as e:
        print(f"Connection failed: {str(e)}")
        return False


if __name__ == "__main__":
    test_pinecone_connection()
