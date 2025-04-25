from pinecone import Pinecone
from config import settings
import os

class PineconeDB:
    def __init__(self):
        self.index_name = settings.PINECONE_INDEX_NAME
        self.pc = None
        self.index = None
        
    def connect(self):
        # Initialize Pinecone with the new SDK approach
        self.pc = Pinecone(
            api_key=settings.PINECONE_API_KEY
        )
        
        # Check if index exists, if not create it
        if self.index_name not in [index.name for index in self.pc.list_indexes()]:
            self.pc.create_index(
                name=self.index_name,
                dimension=1536,  # Using OpenAI's embedding dimension
                metric="cosine"
            )
        
        self.index = self.pc.Index(self.index_name)
        
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

pinecone_db = PineconeDB() 