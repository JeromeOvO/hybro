import os

import pinecone
from dotenv import load_dotenv

load_dotenv()


class PineconeDB:
    def __init__(self):
        self.index_name = os.getenv("PINECONE_INDEX_NAME")
        self.index = None

    def connect(self):
        # Initialize Pinecone for serverless
        pc = pinecone.Pinecone(api_key=os.getenv("PINECONE_API_KEY"))

        # Get the existing index
        self.index = pc.Index(self.index_name)

    def query(self, vector, top_k=5):
        """Query the vector database for similar vectors"""
        if not self.index:
            self.connect()

        results = self.index.query(vector=vector, top_k=top_k, include_metadata=True)

        return results

    def upsert(self, vectors):
        """Insert or update vectors in the database"""
        if not self.index:
            self.connect()

        return self.index.upsert(vectors=vectors)

    def delete(self, ids):
        """Delete vectors from the database by their IDs

        Args:
            ids: A single ID string or list of IDs to delete

        Returns:
            The deletion response from Pinecone
        """
        if not self.index:
            self.connect()

        return self.index.delete(ids=ids)


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
