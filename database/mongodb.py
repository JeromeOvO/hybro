from motor.motor_asyncio import AsyncIOMotorClient
from config import settings
import json
from typing import Any, Dict, List
from bson import ObjectId
from datetime import datetime
from models.agent import Agent
from models.task import RootTask, ChildTask
import uuid

class MongoDB:
    client: AsyncIOMotorClient = None
    
    def __init__(self):
        self.client = None
    
    async def connect(self):
        try:
            self.client = AsyncIOMotorClient(settings.MONGODB_URL)
            # Verify connection works
            await self.client.admin.command('ping')
            print("Connected to MongoDB successfully")
        except Exception as e:
            print(f"MongoDB connection error: {e}")
            self.client = None
    
    async def close_database_connection(self):
        """Close MongoDB connection"""
        if self.client:
            self.client.close()
    
    @property
    def db(self):
        """Get database instance"""
        if not self.client:
            raise ConnectionError("MongoDB client is not connected. Please call connect() first.")
        return self.client[settings.MONGODB_DB_NAME]
    
    @property
    def agents_collection(self):
        """Get agents collection"""
        if not self.client:
            raise ConnectionError("MongoDB client is not connected. Please call connect() first.")
        return self.db.agents
    
    @property
    def tasks_collection(self):
        """Get tasks collection"""
        if not self.client:
            raise ConnectionError("MongoDB client is not connected. Please call connect() first.")
        return self.db.tasks

    @property
    def child_tasks_collection(self):
        """Get child tasks collection"""
        if not self.client:
            raise ConnectionError("MongoDB client is not connected. Please call connect() first.")
        return self.db.child_tasks

    def serialize_mongodb_doc(self, doc):
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
                doc[key] = self.serialize_mongodb_doc(value)
            elif isinstance(value, list):
                doc[key] = [self.serialize_mongodb_doc(item) if isinstance(item, dict) else 
                            str(item) if isinstance(item, ObjectId) else 
                            item.isoformat() if isinstance(item, datetime) else 
                            item for item in value]
        
        return doc

    async def add_agent(self, agent: Agent) -> str:
        """
        Add an agent to the database
        
        Args:
            agent: agent in Agent model
            
        Returns:
            str: ID of the added agent
        """
        # 将Pydantic模型转换为dict
        agent_dict = agent.dict() if hasattr(agent, 'dict') else agent.model_dump()
        
        # Insert into collection
        result = await self.agents_collection.insert_one(agent_dict)
        return str(result.inserted_id)
    
    async def get_agent(self, agent_id: str) -> Dict[str, Any]:
        """
        Get an agent by AgentID
        
        Args:
            agent_id: AgentID of the agent to retrieve
            
        Returns:
            Dict: Agent document or None if not found
        """
        # 考虑到ObjectId的问题，这里可能需要检查输入
        try:
            agent = await self.agents_collection.find_one({"agent_id": agent_id})
            # 如果找不到，尝试将agent_id作为ObjectId查询
            if not agent and len(agent_id) == 24:
                try:
                    agent = await self.agents_collection.find_one({"agent_id": ObjectId(agent_id)})
                except:
                    pass
        except Exception:
            # 如果出错，可能是格式问题，尝试直接查询
            agent = await self.agents_collection.find_one({"agent_id": agent_id})
        
        return self.serialize_mongodb_doc(agent) if agent else None
    
    async def get_agents(self, query: Dict[str, Any] = None, limit: int = 0) -> List[Dict[str, Any]]:
        """
        Get multiple agents matching a query
        
        Args:
            query: Query filter
            limit: Maximum number of results (0 for no limit)
            
        Returns:
            List[Dict]: List of agent documents
        """
        if query is None:
            query = {}
            
        cursor = self.agents_collection.find(query)
        if limit > 0:
            cursor = cursor.limit(limit)
            
        agents = []
        async for agent in cursor:
            agents.append(self.serialize_mongodb_doc(agent))
            
        return agents
    
    async def update_agent(self, agent_id: str, update_data: Dict[str, Any]) -> bool:
        """
        Update an agent
        
        Args:
            agent_id: ID of the agent to update
            update_data: New data to update
            
        Returns:
            bool: True if update was successful
        """
        # If update_data is a Pydantic model, convert to dict
        if hasattr(update_data, 'dict'):
            update_data = update_data.dict(exclude_unset=True)
        elif hasattr(update_data, 'model_dump'):
            update_data = update_data.model_dump(exclude_unset=True)
        
        result = await self.agents_collection.update_one(
            {"agent_id": ObjectId(agent_id)},
            {"$set": update_data}
        )
        return result.modified_count > 0
    
    async def delete_agent(self, agent_id: str) -> bool:
        """
        Delete an agent
        
        Args:
            agent_id: ID of the agent to delete
            
        Returns:
            bool: True if deletion was successful
        """
        result = await self.agents_collection.delete_one({"agent_id": ObjectId(agent_id)})
        return result.deleted_count > 0
    
    async def add_task(self, task: RootTask) -> str:
        """
        Add a task to MongoDB
        
        Args:
            task: The task to add
            
        Returns:
            str: The ID of the added task
        """
        # 为任务生成ID（如果尚未设置）
        if not task.task_id:
            task.task_id = str(uuid.uuid4())
        
        # 将Pydantic模型转换为dict
        task_dict = task.dict() if hasattr(task, 'dict') else task.model_dump()
        
        # 插入字典而不是对象
        result = await self.tasks_collection.insert_one(task_dict)
        
        # 返回任务ID
        return task.task_id

    async def get_task(self, task_id: str) -> Dict[str, Any]:
        """
        Get a task by ID
        
        Args:
            task_id: ID of the task to retrieve
            
        Returns:
            Dict: Task document or None if not found
        """
        task = await self.tasks_collection.find_one({"task_id": task_id})
        if not task:
            return None
        
        # 转换为可序列化的字典
        task_dict = self.serialize_mongodb_doc(task)
        
        # 补充subtasks中缺少的字段
        if "subtasks" in task_dict and task_dict["subtasks"]:
            for i, subtask_ref in enumerate(task_dict["subtasks"]):
                # 如果subtask只包含最小引用信息，补充必要字段
                if "task" not in subtask_ref:
                    # 创建一个基本Task对象字典，使用有效的枚举值
                    subtask_ref["task"] = {
                        "id": subtask_ref.get("task_id", ""),
                        "sessionId": task_dict.get("task_id", ""),
                        "status": {
                            "state": "submitted",  # 使用有效的枚举值替换 'pending'
                            "timestamp": datetime.now().isoformat()
                        },
                        "artifacts": [],
                        "history": [],
                        "metadata": {}
                    }
                
                # 确保parent_id字段存在
                if "parent_id" not in subtask_ref:
                    subtask_ref["parent_id"] = task_dict.get("task_id", "")
                
                # 确保其他必要字段存在
                if "priority" not in subtask_ref:
                    subtask_ref["priority"] = 1
                
                if "dependencies" not in subtask_ref:
                    subtask_ref["dependencies"] = []
        
        return RootTask.parse_obj(task_dict)

    async def get_tasks(self, query: Dict[str, Any] = None, limit: int = 0) -> List['RootTask']:
        """
        Get multiple tasks matching a query
        
        Args:
            query: Query filter
            limit: Maximum number of results (0 for no limit)
            
        Returns:
            List[RootTask]: List of task objects
        """
        
        if query is None:
            query = {}
            
        cursor = self.tasks_collection.find(query)
        if limit > 0:
            cursor = cursor.limit(limit)
            
        tasks = []
        async for task_dict in cursor:
            task_dict = self.serialize_mongodb_doc(task_dict)
            
            # 补充subtasks中缺少的字段，与get_task方法类似
            if "subtasks" in task_dict and task_dict["subtasks"]:
                for i, subtask_ref in enumerate(task_dict["subtasks"]):
                    if "task" not in subtask_ref:
                        subtask_ref["task"] = {
                            "id": subtask_ref.get("task_id", ""),
                            "sessionId": task_dict.get("task_id", ""),
                            "status": {
                                "state": "submitted",
                                "timestamp": datetime.now().isoformat()
                            },
                            "artifacts": [],
                            "history": [],
                            "metadata": {}
                        }
                    
                    if "parent_id" not in subtask_ref:
                        subtask_ref["parent_id"] = task_dict.get("task_id", "")
                    
                    if "priority" not in subtask_ref:
                        subtask_ref["priority"] = 1
                    
                    if "dependencies" not in subtask_ref:
                        subtask_ref["dependencies"] = []
            
            tasks.append(RootTask.parse_obj(task_dict))
            
        return tasks

    async def update_task(self, task_id: str, update_data: Dict[str, Any]) -> bool:
        """
        Update a task
        
        Args:
            task_id: ID of the task to update
            update_data: New data to update
            
        Returns:
            bool: True if update was successful
        """
        # If update_data is a Pydantic model, convert to dict
        if hasattr(update_data, 'dict'):
            update_data = update_data.dict(exclude_unset=True)
        elif hasattr(update_data, 'model_dump'):
            update_data = update_data.model_dump(exclude_unset=True)
        
        result = await self.tasks_collection.update_one(
            {"task_id": task_id},
            {"$set": update_data}
        )
        return result.modified_count > 0

    async def delete_task(self, task_id: str) -> bool:
        """
        Delete a task
        
        Args:
            task_id: ID of the task to delete
            
        Returns:
            bool: True if deletion was successful
        """
        result = await self.tasks_collection.delete_one({"task_id": task_id})
        return result.deleted_count > 0

    async def add_child_task(self, child_task: ChildTask) -> str:
        """
        Add a child task to the database
        
        Args:
            child_task: ChildTask object to add
            
        Returns:
            str: ID of the added child task
        """
        # Generate ID if not set
        if not child_task.task_id:
            child_task.task_id = str(uuid.uuid4())
        
        # Convert the Pydantic model to dict for MongoDB storage
        child_task_dict = child_task.dict() if hasattr(child_task, 'dict') else child_task.model_dump()
        
        # Insert the dictionary
        result = await self.child_tasks_collection.insert_one(child_task_dict)
        return child_task.task_id

    async def get_child_task(self, child_task_id: str) -> Dict[str, Any]:
        """
        Get a child task by ID
        
        Args:
            child_task_id: ID of the child task to retrieve
            
        Returns:
            Dict: Child task document or None if not found
        """
        child_task = await self.child_tasks_collection.find_one({"task_id": child_task_id})
        return self.serialize_mongodb_doc(child_task) if child_task else None

    async def get_child_tasks_by_parent(self, root_task_id: str) -> List[Dict[str, Any]]:
        """
        Get all child tasks for a parent task
        
        Args:
            root_task_id: ID of the parent task
            
        Returns:
            List[Dict]: List of child task documents
        """
        cursor = self.child_tasks_collection.find({"parent_id": root_task_id})
        child_tasks = []
        async for task in cursor:
            child_tasks.append(self.serialize_mongodb_doc(task))
        return child_tasks

    async def update_child_task(self, child_task_id: str, update_data: Dict[str, Any]) -> bool:
        """
        Update a child task
        
        Args:
            child_task_id: ID of the child task to update
            update_data: New data to update
            
        Returns:
            bool: True if update was successful
        """
        # If update_data is a Pydantic model, convert to dict
        if hasattr(update_data, 'dict'):
            update_data = update_data.dict(exclude_unset=True)
        elif hasattr(update_data, 'model_dump'):
            update_data = update_data.model_dump(exclude_unset=True)
        
        result = await self.child_tasks_collection.update_one(
            {"task_id": child_task_id},
            {"$set": update_data}
        )
        return result.modified_count > 0

    async def delete_child_task(self, child_task_id: str) -> bool:
        """
        Delete a child task
        
        Args:
            child_task_id: ID of the child task to delete
            
        Returns:
            bool: True if deletion was successful
        """
        result = await self.child_tasks_collection.delete_one({"task_id": child_task_id})
        return result.deleted_count > 0

mongodb = MongoDB() 