from motor.motor_asyncio import AsyncIOMotorClient
from config import settings

class MongoDB:
    client: AsyncIOMotorClient = None
    
    async def connect_to_database(self):
        """Connect to MongoDB"""
        self.client = AsyncIOMotorClient(settings.MONGODB_URL)
        
    async def close_database_connection(self):
        """Close MongoDB connection"""
        if self.client:
            self.client.close()
    
    @property
    def db(self):
        """Get database instance"""
        return self.client[settings.MONGODB_DB_NAME]
    
    @property
    def agents_collection(self):
        """Get agents collection"""
        return self.db.agents
    
    @property
    def tasks_collection(self):
        """Get tasks collection"""
        return self.db.tasks

mongodb = MongoDB() 