from motor.motor_asyncio import AsyncIOMotorClient
from config import settings
import json
from typing import Any, Dict, List
from bson import ObjectId
from datetime import datetime

class JSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, ObjectId):
            return str(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)

def serialize_mongodb_doc(doc):
    """Convert MongoDB document to JSON-serializable dict"""
    if doc is None:
        return None
    
    # Convert ObjectId to string
    if "_id" in doc:
        doc["_id"] = str(doc["_id"])
    
    # Recursively process embedded documents
    for key, value in doc.items():
        if isinstance(value, ObjectId):
            doc[key] = str(value)
        elif isinstance(value, datetime):
            doc[key] = value.isoformat()
        elif isinstance(value, dict):
            doc[key] = serialize_mongodb_doc(value)
        elif isinstance(value, list):
            doc[key] = [serialize_mongodb_doc(item) if isinstance(item, dict) else 
                        str(item) if isinstance(item, ObjectId) else 
                        item.isoformat() if isinstance(item, datetime) else 
                        item for item in value]
    
    return doc

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
    
    @property
    def protocols_collection(self):
        """Get protocols collection for agent-to-agent communication"""
        return self.db.protocols
    
    async def save_protocol_task(self, task_id: str, protocol_task: Any) -> Any:
        """Save a protocol task to the database"""
        # Convert to dict for storage
        task_dict = protocol_task.model_dump()
        
        # Convert datetime objects to strings for MongoDB storage
        if "status" in task_dict and "timestamp" in task_dict["status"]:
            if hasattr(task_dict["status"]["timestamp"], "isoformat"):
                task_dict["status"]["timestamp"] = task_dict["status"]["timestamp"].isoformat()
        
        # Store in protocols collection
        await self.protocols_collection.update_one(
            {"id": protocol_task.id},
            {"$set": task_dict},
            upsert=True
        )
        
        # Also link to the task
        await self.tasks_collection.update_one(
            {"task_id": task_id},
            {"$addToSet": {"protocol_tasks": protocol_task.id}}
        )
        
        return protocol_task
    
    async def get_protocol_tasks(self, task_id: str) -> List[Dict[str, Any]]:
        """Get all protocol tasks for a main task"""
        task_data = await self.tasks_collection.find_one({"task_id": task_id})
        if not task_data or "protocol_tasks" not in task_data:
            return []
            
        protocol_tasks = []
        for pt_id in task_data["protocol_tasks"]:
            pt_data = await self.protocols_collection.find_one({"id": pt_id})
            if pt_data:
                protocol_tasks.append(serialize_mongodb_doc(pt_data))
                
        return protocol_tasks

mongodb = MongoDB() 