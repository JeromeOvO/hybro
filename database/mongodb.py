import os
from typing import Any

from a2a.types import AgentCard
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

from models.agent import Agent
from models.memory import ChatContext, RoomMemory
from models.task import BaseTask, MetaTask, TaskSession
from models.room import Room, RoomUserMessage, RoomAgentMessage

load_dotenv()


class MongoDB:
    client: AsyncIOMotorClient | None = None

    def __init__(self):
        self.client = None

    async def connect(self):
        try:
            self.client = AsyncIOMotorClient(os.getenv("MONGODB_URL"))
            # Verify connection works
            await self.client.admin.command("ping")
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
            raise ConnectionError(
                "MongoDB client is not connected. Please call connect() first."
            )
        db_name = os.getenv("MONGODB_DB_NAME")
        if not db_name:
            raise ValueError("MONGODB_DB_NAME environment variable is not set")
        return self.client[db_name]

    @property
    def agents_collection(self):
        """Get agents collection"""
        if not self.client:
            raise ConnectionError(
                "MongoDB client is not connected. Please call connect() first."
            )
        return self.db.agents

    @property
    def base_tasks_collection(self):
        """Get base tasks collection"""
        if not self.client:
            raise ConnectionError(
                "MongoDB client is not connected. Please call connect() first."
            )
        return self.db.base_tasks

    @property
    def meta_tasks_collection(self):
        """Get meta tasks collection"""
        if not self.client:
            raise ConnectionError(
                "MongoDB client is not connected. Please call connect() first."
            )
        return self.db.meta_tasks

    @property
    def task_sessions_collection(self):
        """Get task sessions collection"""
        if not self.client:
            raise ConnectionError(
                "MongoDB client is not connected. Please call connect() first."
            )
        return self.db.task_sessions

    @property
    def chat_contexts_collection(self):
        """Get chat contexts collection"""
        if not self.client:
            raise ConnectionError(
                "MongoDB client is not connected. Please call connect() first."
            )
        return self.db.chat_contexts


    @property
    def rooms_collection(self):
        """Get rooms collection"""
        if not self.client:
            raise ConnectionError(
                "MongoDB client is not connected. Please call connect() first."
            )
        return self.db.rooms

    @property
    def room_user_messages_collection(self):
        """Get room user messages collection"""
        if not self.client:
            raise ConnectionError(
                "MongoDB client is not connected. Please call connect() first."
            )
        return self.db.room_user_messages

    @property
    def room_agent_messages_collection(self):
        """Get room agent messages collection"""
        if not self.client:
            raise ConnectionError(
                "MongoDB client is not connected. Please call connect() first."
            )
        return self.db.room_agent_messages

    @property
    def room_memories_collection(self):
        """Get room memories collection"""
        if not self.client:
            raise ConnectionError(
                "MongoDB client is not connected. Please call connect() first."
            )
        return self.db.room_memories

    # agent management
    async def add_agent(self, agent: Agent) -> str:
        """
        Add an agent to the database

        Args:
            agent: agent in Agent model

        Returns:
            str: inserted_id
        """
        result = await self.agents_collection.insert_one(agent.model_dump(mode="json"))
        return str(result.inserted_id)

    async def delete_agent_by_agent_id(self, agent_id: str) -> bool:
        """
        Delete an agent

        Args:
            agent_id: ID of the agent to delete

        Returns:
            bool: True if deletion was successful
        """
        result = await self.agents_collection.delete_one({"agent_id": agent_id})
        return result.deleted_count > 0

    async def get_agent_by_agent_id(self, agent_id: str) -> Agent | None:
        """
        Get an agent by AgentID

        Args:
            agent_id: AgentID of the agent to retrieve

        Returns:
            Agent: Agent document or None if not found
        """
        agent = await self.agents_collection.find_one({"agent_id": agent_id})

        return Agent(**agent) if agent else None

    async def get_all_agents(self) -> list[Agent]:
        """
        Get all agents
        """
        results = self.agents_collection.find()
        agents = []
        async for agent in results:
            agents.append(Agent(**agent))
        return agents

    async def get_agents_with_conditions(
        self, query: dict[str, Any] | None = None, limit: int = 0
    ) -> list[Agent]:
        """
        Get multiple agents matching a query

        Args:
            query: Query filter
            limit: Maximum number of results (0 for no limit)

        Returns:
            List[Agent]: List of agent documents
        """
        if query is None:
            query = {}

        results = self.agents_collection.find(query)
        if limit > 0:
            results = results.limit(limit)

        agents = []
        async for agent in results:
            agents.append(Agent(**agent))

        return agents

    async def update_agent_agent_card_by_agent_id(
        self, agent_id: str, agent_card: AgentCard
    ) -> bool:
        """
        Update an agent

        Args:
            agent_id: ID of the agent to update
            agent_card: New agent card to update

        Returns:
            bool: True if update was successful
        """
        result = await self.agents_collection.update_one(
            {"agent_id": agent_id},
            {"$set": agent_card.model_dump(exclude_unset=True, mode="json")},
        )
        return result.modified_count > 0

    async def update_agent_by_agent_id(self, agent_id: str, agent: Agent) -> bool:
        """
        Update an agent
        """
        if not agent:
            raise ValueError("Agent is required")

        result = await self.agents_collection.update_one(
            {"agent_id": agent_id},
            {"$set": agent.model_dump(exclude_unset=True, mode="json")},
        )

        return result.modified_count > 0

    # task management
    async def add_base_task(self, base_task: BaseTask) -> str:
        """
        Add a base task to the database
        """
        result = await self.base_tasks_collection.insert_one(
            base_task.model_dump(mode="json")
        )
        return str(result.inserted_id)

    async def add_meta_task(self, meta_task: MetaTask) -> str:
        """
        Add a meta task to the database
        """
        result = await self.meta_tasks_collection.insert_one(
            meta_task.model_dump(mode="json")
        )
        return str(result.inserted_id)

    async def add_task_session(self, task_session: TaskSession) -> str:
        """
        Add a task session to the database
        """
        result = await self.task_sessions_collection.insert_one(
            task_session.model_dump(mode="json")
        )
        return str(result.inserted_id)

    async def delete_base_task_by_task_id(self, task_id: str) -> bool:
        """
        Delete a base task by task_id
        """
        result = await self.base_tasks_collection.delete_one({"task_id": task_id})
        return result.deleted_count > 0

    async def delete_meta_task_by_task_id(self, task_id: str) -> bool:
        """
        Delete a meta task by task_id
        """
        result = await self.meta_tasks_collection.delete_one({"task_id": task_id})
        return result.deleted_count > 0

    async def delete_task_session_by_session_id(self, session_id: str) -> bool:
        """
        Delete a task session by session_id
        """
        result = await self.task_sessions_collection.delete_one(
            {"session_id": session_id}
        )
        return result.deleted_count > 0

    async def get_base_task_by_task_id(self, task_id: str) -> BaseTask | None:
        """
        Get a base task by task_id
        """
        result = await self.base_tasks_collection.find_one({"task_id": task_id})
        return BaseTask(**result) if result else None

    async def get_meta_task_by_task_id(self, task_id: str) -> MetaTask | None:
        """
        Get a meta task by task_id
        """
        result = await self.meta_tasks_collection.find_one({"task_id": task_id})
        return MetaTask(**result) if result else None

    async def get_task_session_by_session_id(
        self, session_id: str
    ) -> TaskSession | None:
        """
        Get a task session by session_id
        """
        result = await self.task_sessions_collection.find_one(
            {"session_id": session_id}
        )
        return TaskSession(**result) if result else None

    async def get_task_sessions_by_user_name(self, user_name: str) -> list[TaskSession]:
        """
        Get all task sessions by user_name
        """
        results = self.task_sessions_collection.find({"user_name": user_name})
        task_sessions = []
        async for task_session in results:
            task_sessions.append(TaskSession(**task_session))
        return task_sessions

    async def get_all_task_sessions(self) -> list[TaskSession]:
        """
        Get all task sessions
        """
        results = self.task_sessions_collection.find()
        task_sessions = []
        async for task_session in results:
            task_sessions.append(TaskSession(**task_session))
        return task_sessions

    async def get_base_tasks_by_session_id(self, session_id: str) -> list[BaseTask]:
        """
        Get all base tasks by session_id
        """
        results = self.base_tasks_collection.find({"session_id": session_id})
        base_tasks = []
        async for base_task in results:
            base_tasks.append(BaseTask(**base_task))
        return base_tasks

    async def get_meta_tasks_by_parent_task_id(
        self, parent_task_id: str
    ) -> list[MetaTask]:
        """
        Get all meta tasks by parent_task_id
        """
        results = self.meta_tasks_collection.find({"parent_task_id": parent_task_id})
        meta_tasks = []
        async for meta_task in results:
            meta_tasks.append(MetaTask(**meta_task))
        return meta_tasks

    async def update_base_task_by_task_id(
        self, task_id: str, base_task: BaseTask
    ) -> bool:
        """
        Update a base task by task_id
        """
        result = await self.base_tasks_collection.update_one(
            {"task_id": task_id},
            {"$set": base_task.model_dump(exclude_unset=True, mode="json")},
        )
        return result.modified_count > 0

    async def update_meta_task_by_task_id(
        self, task_id: str, meta_task: MetaTask
    ) -> bool:
        """
        Update a meta task by task_id
        """
        result = await self.meta_tasks_collection.update_one(
            {"task_id": task_id},
            {"$set": meta_task.model_dump(exclude_unset=True, mode="json")},
        )
        return result.modified_count > 0

    async def update_task_session_by_session_id(
        self, session_id: str, task_session: TaskSession
    ) -> bool:
        """
        Update a task session by session_id
        """
        result = await self.task_sessions_collection.update_one(
            {"session_id": session_id},
            {"$set": task_session.model_dump(exclude_unset=True, mode="json")},
        )
        return result.modified_count > 0

    # chat context management
    async def add_chat_context(self, chat_context: ChatContext) -> str:
        """
        Add a chat context to the database
        """
        result = await self.chat_contexts_collection.insert_one(
            chat_context.model_dump(mode="json")
        )
        return str(result.inserted_id)

    async def get_chat_context_by_session_id(self, session_id: str) -> ChatContext | None:
        """
        Get a chat context by session_id
        """
        result = await self.chat_contexts_collection.find_one({"session_id": session_id})
        return ChatContext(**result) if result else None
    
    async def update_chat_context_by_session_id(self, session_id: str, chat_context: ChatContext) -> bool:
        """
        Update a chat context by session_id
        """
        result = await self.chat_contexts_collection.update_one(
            {"session_id": session_id},
            {"$set": chat_context.model_dump(exclude_unset=True, mode="json")},
        )
        return result.modified_count >= 0
    
    async def delete_chat_context_by_session_id(self, session_id: str) -> bool:
        """
        Delete a chat context by session_id
        """
        result = await self.chat_contexts_collection.delete_one({"session_id": session_id})
        return result.deleted_count > 0

    # room management
    async def add_room(self, room: Room) -> str:
        """
        Add a room to the database
        """
        result = await self.rooms_collection.insert_one(room.model_dump(mode="json"))
        return str(result.inserted_id)

    async def get_room_by_room_id(self, room_id: str) -> Room | None:
        """
        Get a room by room_id
        """
        result = await self.rooms_collection.find_one({"room_id": room_id})
        return Room(**result) if result else None

    async def get_rooms_by_room_owner_id(self, room_owner_id: str) -> list[Room]:
        """
        Get rooms by room_owner_id
        """
        results = self.rooms_collection.find({"room_owner_id": room_owner_id})
        rooms = []
        async for room in results:
            rooms.append(Room(**room))
        return rooms

    async def update_room_by_room_id(self, room_id: str, room: Room) -> bool:
        """
        Update a room by room_id
        """
        result = await self.rooms_collection.update_one({"room_id": room_id}, {"$set": room.model_dump(exclude_unset=True, mode="json")})
        return result.modified_count > 0
    
    async def delete_room_by_room_id(self, room_id: str) -> bool:
        """
        Delete a room by room_id
        """
        result = await self.rooms_collection.delete_one({"room_id": room_id})
        return result.deleted_count > 0
    
    # room user message management
    async def add_room_user_message(self, room_user_message: RoomUserMessage) -> str:
        """
        Add a room user message to the database
        """
        result = await self.room_user_messages_collection.insert_one(room_user_message.model_dump(mode="json"))
        return str(result.inserted_id)
    
    async def get_room_user_messages_by_room_id(self, room_id: str) -> list[RoomUserMessage]:
        """
        Get room user messages by room_id
        """
        results = self.room_user_messages_collection.find({"room_id": room_id})
        room_user_messages = []
        async for room_user_message in results:
            room_user_messages.append(RoomUserMessage(**room_user_message))
        return room_user_messages
    
    async def get_room_user_message_by_message_id(self, message_id: str) -> RoomUserMessage | None:
        """
        Get a room user message by message_id
        """
        result = await self.room_user_messages_collection.find_one({"message_id": message_id})
        return RoomUserMessage(**result) if result else None
    
    async def update_room_user_message_by_message_id(self, message_id: str, room_user_message: RoomUserMessage) -> bool:
        """
        Update a room user message by message_id
        """
        result = await self.room_user_messages_collection.update_one({"message_id": message_id}, {"$set": room_user_message.model_dump(exclude_unset=True, mode="json")})
        return result.modified_count > 0
    
    async def delete_room_user_message_by_message_id(self, message_id: str) -> bool:
        """
        Delete a room user message by message_id
        """
        result = await self.room_user_messages_collection.delete_one({"message_id": message_id})
        return result.deleted_count > 0
    
    # room agent message management
    async def add_room_agent_message(self, room_agent_message: RoomAgentMessage) -> str:
        """
        Add a room agent message to the database
        """
        result = await self.room_agent_messages_collection.insert_one(room_agent_message.model_dump(mode="json"))
        return str(result.inserted_id)
    
    async def get_room_agent_messages_by_room_id(self, room_id: str) -> list[RoomAgentMessage]:
        """
        Get room agent messages by room_id
        """
        results = self.room_agent_messages_collection.find({"room_id": room_id})
        room_agent_messages = []
        async for room_agent_message in results:
            room_agent_messages.append(RoomAgentMessage(**room_agent_message))
        return room_agent_messages
    
    async def get_room_agent_message_by_message_id(self, message_id: str) -> RoomAgentMessage | None:
        """
        Get a room agent message by message_id
        """
        result = await self.room_agent_messages_collection.find_one({"message_id": message_id})
        return RoomAgentMessage(**result) if result else None

    async def get_room_agent_messages_by_related_message_id(self, related_message_id: str) -> list[RoomAgentMessage]:
        """
        Get room agent messages by related_message_id
        """
        results = self.room_agent_messages_collection.find({"related_message_id": related_message_id})
        room_agent_messages = []
        async for room_agent_message in results:
            room_agent_messages.append(RoomAgentMessage(**room_agent_message))
        return room_agent_messages
    
    async def update_room_agent_message_by_message_id(self, message_id: str, room_agent_message: RoomAgentMessage) -> bool:
        """
        Update a room agent message by message_id
        """
        result = await self.room_agent_messages_collection.update_one({"message_id": message_id}, {"$set": room_agent_message.model_dump(exclude_unset=True, mode="json")})
        return result.modified_count > 0
    
    async def delete_room_agent_message_by_message_id(self, message_id: str) -> bool:
        """
        Delete a room agent message by message_id
        """
        result = await self.room_agent_messages_collection.delete_one({"message_id": message_id})
        return result.deleted_count > 0

    # room memory management
    async def add_room_memory(self, room_memory: RoomMemory) -> str:
        """
        Add a room memory to the database
        """
        result = await self.room_memories_collection.insert_one(room_memory.model_dump(mode="json"))
        return str(result.inserted_id)

    async def get_room_memory_by_memory_id(self, memory_id: str) -> RoomMemory | None:
        """
        Get a room memory by memory_id
        """
        result = await self.room_memories_collection.find_one({"memory_id": memory_id})
        return RoomMemory(**result) if result else None

    async def get_room_memory_by_room_id(self, room_id: str) -> RoomMemory | None:
        """
        Get a room memory by room_id
        """
        result = await self.room_memories_collection.find_one({"room_id": room_id})
        return RoomMemory(**result) if result else None

    async def update_room_memory_by_memory_id(self, memory_id: str, room_memory: RoomMemory) -> bool:
        """
        Update a room memory by memory_id
        """
        result = await self.room_memories_collection.update_one({"memory_id": memory_id}, {"$set": room_memory.model_dump(exclude_unset=True, mode="json")})
        return result.modified_count > 0

    async def delete_room_memory_by_memory_id(self, memory_id: str) -> bool:
        """
        Delete a room memory by memory_id
        """
        result = await self.room_memories_collection.delete_one({"memory_id": memory_id})
        return result.deleted_count > 0

    async def update_room_memory_by_room_id(self, room_id: str, room_memory: RoomMemory) -> bool:
        """
        Update a room memory by room_id
        """
        result = await self.room_memories_collection.update_one({"room_id": room_id}, {"$set": room_memory.model_dump(exclude_unset=True, mode="json")})
        return result.modified_count > 0
    
    async def delete_room_memory_by_room_id(self, room_id: str) -> bool:
        """
        Delete a room memory by room_id
        """
        result = await self.room_memories_collection.delete_one({"room_id": room_id})
        return result.deleted_count > 0

mongodb = MongoDB()
