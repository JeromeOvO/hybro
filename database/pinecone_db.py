import pinecone
from config import settings


class PineconeDB:
    def __init__(self):
        self.index_name = settings.PINECONE_INDEX_NAME
        self.index = None
        
    def connect(self):
        # Initialize Pinecone (Serverless style)
        pinecone.init(
            api_key=settings.PINECONE_API_KEY,
            host=settings.PINECONE_HOST  # 必须是你的 index host，例如 bromatch-test-xxxx.pinecone.io
        )
        
        # 直接连接已有的 Serverless Index
        self.index = pinecone.Index(self.index_name)
        
    def query(self, vector, top_k=5):
        """Query the vector database for similar vectors"""
        if not self.index:
            self.connect()
        
        results = self.index.query(
            vector=vector,
            top_k=top_k,
            include_metadata=True
        )
        
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
