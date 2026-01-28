import os
from contextlib import asynccontextmanager
from typing import Any

from a2a.types import AgentCard
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from models.agent import Agent
from models.agent_group import AgentGroup
from models.api_key import APIKey
from models.memory import ChatContext, RoomMemory
from models.room import Room, RoomAgentMessage, RoomUserMessage
from models.task import BaseTask, MetaTask, TaskSession

load_dotenv()


class MongoDB:
    client: AsyncIOMotorClient | None = None

    def __init__(self):
        self.client = None

    async def connect(self):
        try:
            self.client = AsyncIOMotorClient(
                os.getenv("MONGODB_URL"),
                maxPoolSize=50,  # Maximum number of connections in the pool
                minPoolSize=10,  # Minimum number of connections to maintain
                maxIdleTimeMS=30000,  # Close connections idle for 30 seconds
                serverSelectionTimeoutMS=5000,  # Timeout for server selection
                connectTimeoutMS=5000,  # Timeout for initial connection
                socketTimeoutMS=30000,  # Timeout for socket operations
            )
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

    @property
    def agent_groups_collection(self):
        """Get agent groups collection"""
        if not self.client:
            raise ConnectionError(
                "MongoDB client is not connected. Please call connect() first."
            )
        return self.db.agent_groups

    @property
    def cancelled_messages_collection(self):
        """Get cancelled messages collection"""
        if not self.client:
            raise ConnectionError(
                "MongoDB client is not connected. Please call connect() first."
            )
        return self.db.cancelled_messages

    @property
    def api_keys_collection(self):
        """Get API keys collection"""
        if not self.client:
            raise ConnectionError(
                "MongoDB client is not connected. Please call connect() first."
            )
        return self.db.api_keys

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

    async def get_agents_by_provider_id(self, provider_id: str) -> list[Agent]:
        """
        Get all agents belong to a ProviderID

        Args:
            provider_id: ProviderID of the agent to retrieve

        Returns:
            Agent: Agents document or None if not found
        """
        cursor = self.agents_collection.find({"provider_id": provider_id})
        agents = await cursor.to_list(length=None)
        return [Agent(**agent) for agent in agents]

    async def get_all_agents(self) -> list[Agent]:
        """
        Get all agents
        """
        cursor = self.agents_collection.find()
        results = await cursor.to_list(length=None)
        return [Agent(**agent) for agent in results]

    async def get_all_agents_by_user_id(self, user_id: str) -> list[Agent]:
        """
        Get all agents by user ID
        """
        cursor = self.agents_collection.find({"provider_id": user_id})
        results = await cursor.to_list(length=None)
        return [Agent(**agent) for agent in results]

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

        cursor = self.agents_collection.find(query)
        if limit > 0:
            cursor = cursor.limit(limit)

        results = await cursor.to_list(length=None)
        return [Agent(**agent) for agent in results]

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
        cursor = self.task_sessions_collection.find({"user_name": user_name})
        results = await cursor.to_list(length=None)
        return [TaskSession(**task_session) for task_session in results]

    async def get_all_task_sessions(self) -> list[TaskSession]:
        """
        Get all task sessions
        """
        cursor = self.task_sessions_collection.find()
        results = await cursor.to_list(length=None)
        return [TaskSession(**task_session) for task_session in results]

    async def get_base_tasks_by_session_id(self, session_id: str) -> list[BaseTask]:
        """
        Get all base tasks by session_id
        """
        cursor = self.base_tasks_collection.find({"session_id": session_id})
        results = await cursor.to_list(length=None)
        return [BaseTask(**base_task) for base_task in results]

    async def get_meta_tasks_by_parent_task_id(
        self, parent_task_id: str
    ) -> list[MetaTask]:
        """
        Get all meta tasks by parent_task_id
        """
        cursor = self.meta_tasks_collection.find({"parent_task_id": parent_task_id})
        results = await cursor.to_list(length=None)
        return [MetaTask(**meta_task) for meta_task in results]

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

    async def get_chat_context_by_session_id(
        self, session_id: str
    ) -> ChatContext | None:
        """
        Get a chat context by session_id
        """
        result = await self.chat_contexts_collection.find_one(
            {"session_id": session_id}
        )
        return ChatContext(**result) if result else None

    async def update_chat_context_by_session_id(
        self, session_id: str, chat_context: ChatContext
    ) -> bool:
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
        result = await self.chat_contexts_collection.delete_one(
            {"session_id": session_id}
        )
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
        cursor = self.rooms_collection.find({"room_owner_id": room_owner_id})
        results = await cursor.to_list(length=None)
        return [Room(**room) for room in results]

    async def update_room_by_room_id(self, room_id: str, room: Room) -> bool:
        """
        Update a room by room_id
        """
        result = await self.rooms_collection.update_one(
            {"room_id": room_id},
            {"$set": room.model_dump(exclude_unset=True, mode="json")},
        )
        return result.modified_count >= 0

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
        result = await self.room_user_messages_collection.insert_one(
            room_user_message.model_dump(mode="json")
        )
        return str(result.inserted_id)

    async def get_room_user_messages_by_room_id(
        self, room_id: str
    ) -> list[RoomUserMessage]:
        """
        Get room user messages by room_id
        """
        cursor = self.room_user_messages_collection.find({"room_id": room_id})
        results = await cursor.to_list(length=None)
        return [RoomUserMessage(**room_user_message) for room_user_message in results]

    async def get_room_user_message_by_message_id(
        self, message_id: str
    ) -> RoomUserMessage | None:
        """
        Get a room user message by message_id
        """
        result = await self.room_user_messages_collection.find_one(
            {"message_id": message_id}
        )
        return RoomUserMessage(**result) if result else None

    async def update_room_user_message_by_message_id(
        self, message_id: str, room_user_message: RoomUserMessage
    ) -> bool:
        """
        Update a room user message by message_id
        """
        result = await self.room_user_messages_collection.update_one(
            {"message_id": message_id},
            {"$set": room_user_message.model_dump(exclude_unset=True, mode="json")},
        )
        return result.modified_count > 0

    async def delete_room_user_message_by_message_id(self, message_id: str) -> bool:
        """
        Delete a room user message by message_id
        """
        result = await self.room_user_messages_collection.delete_one(
            {"message_id": message_id}
        )
        return result.deleted_count > 0

    # room agent message management
    async def add_room_agent_message(self, room_agent_message: RoomAgentMessage) -> str:
        """
        Add a room agent message to the database
        """
        result = await self.room_agent_messages_collection.insert_one(
            room_agent_message.model_dump(mode="json")
        )
        return str(result.inserted_id)

    async def get_room_agent_messages_by_room_id(
        self, room_id: str
    ) -> list[RoomAgentMessage]:
        """
        Get room agent messages by room_id
        """
        cursor = self.room_agent_messages_collection.find({"room_id": room_id})
        results = await cursor.to_list(length=None)
        return [
            RoomAgentMessage(**room_agent_message) for room_agent_message in results
        ]

    async def get_room_agent_message_by_message_id(
        self, message_id: str
    ) -> RoomAgentMessage | None:
        """
        Get a room agent message by message_id
        """
        result = await self.room_agent_messages_collection.find_one(
            {"message_id": message_id}
        )
        return RoomAgentMessage(**result) if result else None

    async def get_room_agent_messages_by_related_message_id(
        self, related_message_id: str
    ) -> list[RoomAgentMessage]:
        """
        Get room agent messages by related_message_id
        """
        cursor = self.room_agent_messages_collection.find(
            {"related_message_id": related_message_id}
        )
        results = await cursor.to_list(length=None)
        return [
            RoomAgentMessage(**room_agent_message) for room_agent_message in results
        ]

    async def update_room_agent_message_by_message_id(
        self, message_id: str, room_agent_message: RoomAgentMessage
    ) -> bool:
        """
        Update a room agent message by message_id
        """
        result = await self.room_agent_messages_collection.update_one(
            {"message_id": message_id},
            {"$set": room_agent_message.model_dump(exclude_unset=True, mode="json")},
        )
        return result.modified_count > 0

    async def delete_room_agent_message_by_message_id(self, message_id: str) -> bool:
        """
        Delete a room agent message by message_id
        """
        result = await self.room_agent_messages_collection.delete_one(
            {"message_id": message_id}
        )
        return result.deleted_count > 0

    # room memory management
    async def add_room_memory(self, room_memory: RoomMemory) -> str:
        """
        Add a room memory to the database
        """
        result = await self.room_memories_collection.insert_one(
            room_memory.model_dump(mode="json")
        )
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

    async def update_room_memory_by_memory_id(
        self, memory_id: str, room_memory: RoomMemory
    ) -> bool:
        """
        Update a room memory by memory_id
        """
        result = await self.room_memories_collection.update_one(
            {"memory_id": memory_id},
            {"$set": room_memory.model_dump(exclude_unset=True, mode="json")},
        )
        return result.modified_count > 0

    async def delete_room_memory_by_memory_id(self, memory_id: str) -> bool:
        """
        Delete a room memory by memory_id
        """
        result = await self.room_memories_collection.delete_one(
            {"memory_id": memory_id}
        )
        return result.deleted_count > 0

    async def update_room_memory_by_room_id(
        self, room_id: str, room_memory: RoomMemory
    ) -> bool:
        """
        Update a room memory by room_id
        """
        result = await self.room_memories_collection.update_one(
            {"room_id": room_id},
            {"$set": room_memory.model_dump(exclude_unset=True, mode="json")},
        )
        return result.modified_count > 0

    async def delete_room_memory_by_room_id(self, room_id: str) -> bool:
        """
        Delete a room memory by room_id
        """
        result = await self.room_memories_collection.delete_one({"room_id": room_id})
        return result.deleted_count > 0

    # Agent Group management
    async def add_agent_group(self, agent_group: AgentGroup) -> str:
        """
        Add an agent group to the database
        """
        result = await self.agent_groups_collection.insert_one(
            agent_group.model_dump(mode="json")
        )
        return str(result.inserted_id)

    async def get_agent_groups_by_owner(self, owner_id: str) -> list[AgentGroup]:
        """
        Get all agent groups owned by a user
        """
        cursor = self.agent_groups_collection.find({"owner_id": owner_id})
        groups = []
        async for doc in cursor:
            groups.append(AgentGroup(**doc))
        return groups

    async def get_agent_group_by_id(self, group_id: str) -> AgentGroup | None:
        """
        Get an agent group by its ID
        """
        doc = await self.agent_groups_collection.find_one({"group_id": group_id})
        if doc:
            return AgentGroup(**doc)
        return None

    async def update_agent_group(self, group_id: str, updates: dict) -> bool:
        """
        Update an agent group by its ID
        """
        # Add updated_at timestamp
        from common.utils.time import utcnow

        updates["updated_at"] = utcnow()

        result = await self.agent_groups_collection.update_one(
            {"group_id": group_id}, {"$set": updates}
        )
        return result.modified_count > 0

    async def delete_agent_group(self, group_id: str) -> bool:
        """
        Delete an agent group by its ID
        """
        result = await self.agent_groups_collection.delete_one({"group_id": group_id})
        return result.deleted_count > 0

    # ============== Message Cancellation Methods ==============

    async def cancel_message(self, message_id: str, user_id: str) -> bool:
        """
        Mark a message as cancelled in the database.

        Args:
            message_id: The message ID to cancel
            user_id: The user who cancelled the message

        Returns:
            bool: True if cancellation was recorded successfully
        """
        from common.utils.time import utcnow

        doc = {
            "message_id": message_id,
            "user_id": user_id,
            "cancelled_at": utcnow(),
        }

        try:
            # Only insert if not exist (upsert with $setOnInsert)
            await self.cancelled_messages_collection.update_one(
                {"message_id": message_id},
                {"$setOnInsert": doc},
                upsert=True
            )
            return True
        except Exception as e:
            print(f"Error cancelling message: {e}")
            return False

    async def is_message_cancelled(self, message_id: str) -> bool:
        """
        Check if a message has been cancelled.

        Args:
            message_id: The message ID to check

        Returns:
            bool: True if message is cancelled
        """
        doc = await self.cancelled_messages_collection.find_one(
            {"message_id": message_id}
        )
        return doc is not None

    async def clear_message_cancellation(self, message_id: str) -> bool:
        """
        Remove cancellation record for a message.
        Should be called after workflow completes to clean up.

        Args:
            message_id: The message ID to clear

        Returns:
            bool: True if deletion was successful
        """
        result = await self.cancelled_messages_collection.delete_one(
            {"message_id": message_id}
        )
        return result.deleted_count > 0

    # ============== API Key Management ==============

    async def add_api_key(self, api_key: APIKey) -> str:
        """
        Add an API key to the database.

        Args:
            api_key: APIKey model instance

        Returns:
            str: inserted_id
        """
        result = await self.api_keys_collection.insert_one(
            api_key.model_dump(mode="json")
        )
        return str(result.inserted_id)

    async def get_api_key_by_hash(self, key_hash: str) -> APIKey | None:
        """
        Get an API key by its hash.

        Args:
            key_hash: SHA-256 hash of the API key

        Returns:
            APIKey or None if not found
        """
        result = await self.api_keys_collection.find_one({"key_hash": key_hash})
        return APIKey(**result) if result else None

    async def get_api_key_by_id(self, key_id: str) -> APIKey | None:
        """
        Get an API key by its ID.

        Args:
            key_id: The key ID

        Returns:
            APIKey or None if not found
        """
        result = await self.api_keys_collection.find_one({"key_id": key_id})
        return APIKey(**result) if result else None

    async def get_api_keys_by_user(self, user_id: str) -> list[APIKey]:
        """
        Get all API keys for a user.

        Args:
            user_id: The user ID

        Returns:
            List of APIKey instances
        """
        cursor = self.api_keys_collection.find({"user_id": user_id})
        results = await cursor.to_list(length=None)
        return [APIKey(**key) for key in results]

    async def update_api_key_usage(self, key_hash: str) -> bool:
        """
        Update the usage statistics for an API key.
        Increments usage_count and sets last_used_at.

        Args:
            key_hash: SHA-256 hash of the API key

        Returns:
            bool: True if update was successful
        """
        from common.utils.time import utcnow

        result = await self.api_keys_collection.update_one(
            {"key_hash": key_hash},
            {
                "$set": {"last_used_at": utcnow()},
                "$inc": {"usage_count": 1},
            },
        )
        return result.modified_count > 0

    async def deactivate_api_key(self, key_id: str) -> bool:
        """
        Deactivate an API key.

        Args:
            key_id: The key ID

        Returns:
            bool: True if deactivation was successful
        """
        result = await self.api_keys_collection.update_one(
            {"key_id": key_id},
            {"$set": {"is_active": False}},
        )
        return result.modified_count > 0

    async def delete_api_key(self, key_id: str) -> bool:
        """
        Delete an API key.

        Args:
            key_id: The key ID

        Returns:
            bool: True if deletion was successful
        """
        result = await self.api_keys_collection.delete_one({"key_id": key_id})
        return result.deleted_count > 0
    

mongodb = MongoDB()


async def get_db() -> AsyncIOMotorDatabase:
    """Return module-level database"""
    return mongodb.db


# Create context manager for mongo connection
@asynccontextmanager
async def mongodb_connection(transaction: bool = False):
    """Async context manager for MongoDB connection

    Example usage:
        async with mongodb_connection() as db:
            # use db
    """
    # Startup: connect to MongoDB
    if not mongodb.client:
        await mongodb.connect()

    try:
        if transaction:
            async with await mongodb.client.start_session() as session:
                async with session.start_transaction():
                    yield mongodb.db
        else:
            yield mongodb.db
    finally:
        pass
