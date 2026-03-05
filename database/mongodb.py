import logging
import os
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

from a2a.types import AgentCard, Task, TaskState
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from common.utils.time import utcnow
from models.agent import Agent
from models.agent_group import AgentGroup
from models.api_key import APIKey
from models.memory import ChatContext, RoomMemory
from models.room import Room, RoomAgentMessage, RoomUserMessage
from models.supervisor_v2 import TrajectoryStatus
from models.task import BaseTask, MetaTask, TaskSession

logger = logging.getLogger(__name__)

load_dotenv()


def _ensure_task_validation(msg: RoomAgentMessage) -> RoomAgentMessage:
    """
    Ensure the Task object in message_content is properly validated as a Pydantic model.

    When retrieving from MongoDB, nested RootModel objects (like Part) may not be
    properly reconstructed. This function explicitly validates the Task to ensure
    all nested models are properly instantiated.
    """
    if (
        msg.message_content
        and msg.message_content.message_task
        and isinstance(msg.message_content.message_task, dict)
    ):
        msg.message_content.message_task = Task.model_validate(
            msg.message_content.message_task
        )
    return msg


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

    @property
    def agent_requests_collection(self):
        """Get agent requests collection for rate limiting"""
        if not self.client:
            raise ConnectionError(
                "MongoDB client is not connected. Please call connect() first."
            )
        return self.db.agent_requests

    @property
    def discovery_api_requests_collection(self):
        """Get discovery API requests collection for rate limiting"""
        if not self.client:
            raise ConnectionError(
                "MongoDB client is not connected. Please call connect() first."
            )
        return self.db.discovery_api_requests
      
    @property
    def a2a_tasks_collection(self):
        """Get A2A tasks collection for long-running task tracking"""
        if not self.client:
            raise ConnectionError(
                "MongoDB client is not connected. Please call connect() first."
            )
        return self.db.a2a_tasks

    @property
    def conversation_content_collection(self):
        """
        Get conversation_content collection for lossless compaction storage.

        This collection stores full content for compacted conversation turns.
        See CONTEXT_MEMORY_SYSTEM_DESIGN.md §6.6 for schema.
        """
        if not self.client:
            raise ConnectionError(
                "MongoDB client is not connected. Please call connect() first."
            )
        return self.db.conversation_content

    @property
    def user_memories_collection(self):
        """Get user_memories collection for cross-room user preferences."""
        if not self.client:
            raise ConnectionError(
                "MongoDB client is not connected. Please call connect() first."
            )
        return self.db.user_memories

    @property
    def agent_memories_collection(self):
        """Get agent_memories collection for agent performance history."""
        if not self.client:
            raise ConnectionError(
                "MongoDB client is not connected. Please call connect() first."
            )
        return self.db.agent_memories

    @property
    def file_uploads_collection(self):
        """Get file_uploads collection for multimodal file metadata."""
        if not self.client:
            raise ConnectionError(
                "MongoDB client is not connected. Please call connect() first."
            )
        return self.db.file_uploads

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

    async def update_room_processing_status(
        self, room_id: str, processing_message_id: str | None
    ) -> bool:
        """
        Update the processing_message_id field on a room.
        Used to track which user message is currently being processed.
        Set to message_id when processing starts, None when complete/cancelled/failed.
        """
        result = await self.rooms_collection.update_one(
            {"room_id": room_id},
            {"$set": {"processing_message_id": processing_message_id}},
        )
        return result.modified_count >= 0

    async def delete_room_by_room_id(self, room_id: str) -> bool:
        """
        Delete a room by room_id
        """
        result = await self.rooms_collection.delete_one({"room_id": room_id})
        return result.deleted_count > 0

    # room user message management
    @staticmethod
    def _strip_file_urls(doc: dict) -> None:
        """Remove file_url from serialized attachments to prevent persistence."""
        target = doc.get("$set", doc)
        content = target.get("message_content")
        if not content:
            return
        for att in content.get("attachments") or []:
            att.pop("file_url", None)

    async def add_room_user_message(self, room_user_message: RoomUserMessage) -> str:
        """
        Add a room user message to the database
        """
        doc = room_user_message.model_dump(mode="json")
        self._strip_file_urls(doc)
        result = await self.room_user_messages_collection.insert_one(doc)
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
        update_doc = {
            "$set": room_user_message.model_dump(exclude_unset=True, mode="json")
        }
        self._strip_file_urls(update_doc)
        result = await self.room_user_messages_collection.update_one(
            {"message_id": message_id},
            update_doc,
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
            _ensure_task_validation(RoomAgentMessage(**room_agent_message))
            for room_agent_message in results
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
        if not result:
            return None
        return _ensure_task_validation(RoomAgentMessage(**result))

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
            _ensure_task_validation(RoomAgentMessage(**room_agent_message))
            for room_agent_message in results
        ]

    async def cancel_descendants(self, message_id: str) -> int:
        """Cancel all agent messages downstream in the related_message_id chain.

        Walks the chain iteratively via BFS: at each level, finds agent
        messages whose ``related_message_id`` matches any of the current
        frontier IDs and whose task status is still actionable (not already
        completed/canceled/failed).  After collecting all descendant IDs, a
        single ``update_many`` bulk-writes them to ``canceled``.

        Returns the number of messages actually modified.
        """
        terminal_statuses = [s.value for s in (
            TaskState.completed,
            TaskState.canceled,
            TaskState.failed,
            TaskState.rejected,
        )]
        to_visit = [message_id]
        all_descendant_ids: list[str] = []

        while to_visit:
            cursor = self.room_agent_messages_collection.find(
                {
                    "related_message_id": {"$in": to_visit},
                    "message_content.message_task": {"$ne": None},
                    "message_content.message_task.status.state": {
                        "$nin": terminal_statuses
                    },
                },
                {"message_id": 1},
            )
            children = await cursor.to_list(length=None)
            child_ids = [c["message_id"] for c in children]
            all_descendant_ids.extend(child_ids)
            to_visit = child_ids

        if not all_descendant_ids:
            return 0

        result = await self.room_agent_messages_collection.update_many(
            {"message_id": {"$in": all_descendant_ids}},
            {
                "$set": {
                    "message_content.message_task.status.state": TaskState.canceled.value,
                }
            },
        )
        logger.info(
            "cancel_descendants: canceled %d descendant(s) of message %s",
            result.modified_count,
            message_id,
        )
        return result.modified_count

    async def cancel_agent_messages_by_ids(self, message_ids: list[str]) -> int:
        """Cancel agent messages by their message IDs.

        Sets ``message_content.message_task.status.state`` to ``"canceled"``
        for messages that are not already in a terminal state.

        Returns the number of messages actually modified.
        """
        if not message_ids:
            return 0
        terminal_statuses = [s.value for s in (
            TaskState.completed,
            TaskState.canceled,
            TaskState.failed,
            TaskState.rejected,
        )]
        result = await self.room_agent_messages_collection.update_many(
            {
                "message_id": {"$in": message_ids},
                "message_content.message_task": {"$ne": None},
                "message_content.message_task.status.state": {
                    "$nin": terminal_statuses
                },
            },
            {
                "$set": {
                    "message_content.message_task.status.state": TaskState.canceled.value,
                }
            },
        )
        if result.modified_count:
            logger.info(
                "cancel_agent_messages_by_ids: canceled %d of %d message(s)",
                result.modified_count,
                len(message_ids),
            )
        return result.modified_count

    async def update_room_agent_message_by_message_id(
        self, message_id: str, room_agent_message: RoomAgentMessage
    ) -> bool:
        """
        Update a room agent message by message_id.

        Note: This preserves task tracking fields (webhook_token_hash, etc.)
        if they are None in the update object, to avoid overwriting values set by
        enable_task_tracking_on_message.
        """
        update_data = room_agent_message.model_dump(mode="json")

        # Preserve task tracking fields if they are None in the update
        # These fields are set separately by enable_task_tracking_on_message
        task_tracking_fields = [
            "webhook_token_hash",
            "pending_continuation",
            "last_notified_state",
            "agent_url",
            "task_created_at",
            "task_updated_at",
            "task_content",
            "has_task_tracking",
        ]
        for field in task_tracking_fields:
            if update_data.get(field) is None:
                update_data.pop(field, None)

        result = await self.room_agent_messages_collection.update_one(
            {"message_id": message_id},
            {"$set": update_data},
        )
        # Use matched_count instead of modified_count because MongoDB returns
        # modified_count=0 when the update data is identical to existing data,
        # which is still a successful operation (document was found and processed)
        return result.matched_count > 0

    async def delete_room_agent_message_by_message_id(self, message_id: str) -> bool:
        """
        Delete a room agent message by message_id
        """
        result = await self.room_agent_messages_collection.delete_one(
            {"message_id": message_id}
        )
        return result.deleted_count > 0

    # Task tracking methods on room_agent_messages (consolidated from a2a_tasks)

    async def update_room_agent_message_task_fields(
        self,
        message_id: str,
        webhook_token_hash: str | None = None,
        pending_continuation: dict | None = None,
        last_notified_state: str | None = None,
        agent_url: str | None = None,
        task_created_at: str | None = None,
        task_updated_at: str | None = None,
    ) -> bool:
        """
        Update task tracking fields on a room agent message.
        """
        update_fields = {}
        if webhook_token_hash is not None:
            update_fields["webhook_token_hash"] = webhook_token_hash
        if pending_continuation is not None:
            update_fields["pending_continuation"] = pending_continuation
        if last_notified_state is not None:
            update_fields["last_notified_state"] = last_notified_state
        if agent_url is not None:
            update_fields["agent_url"] = agent_url
        if task_created_at is not None:
            update_fields["task_created_at"] = task_created_at
        if task_updated_at is not None:
            update_fields["task_updated_at"] = task_updated_at

        if not update_fields:
            return False

        result = await self.room_agent_messages_collection.update_one(
            {"message_id": message_id},
            {"$set": update_fields},
        )
        return result.modified_count > 0

    async def enable_task_tracking_on_message(
        self,
        message_id: str,
        webhook_token_hash: str,
        agent_url: str,
        task_created_at,  # datetime
        task_updated_at,  # datetime
        task_data: dict,
    ) -> bool:
        """
        Enable task tracking on a room agent message and set initial task data atomically.

        This sets has_task_tracking=True and stores the webhook token hash and task data.
        The message_id is used for webhook URLs and lookups.
        """
        result = await self.room_agent_messages_collection.update_one(
            {"message_id": message_id},
            {
                "$set": {
                    "has_task_tracking": True,
                    "webhook_token_hash": webhook_token_hash,
                    "agent_url": agent_url,
                    "task_created_at": task_created_at,
                    "task_updated_at": task_updated_at,
                    "message_content.message_task": task_data,
                }
            },
        )
        logger.info(
            "enable_task_tracking_on_message: message_id=%s, matched=%d, modified=%d",
            message_id,
            result.matched_count,
            result.modified_count,
        )

        # Verify the update by reading back the document
        if result.matched_count > 0:
            verify_doc = await self.room_agent_messages_collection.find_one(
                {"message_id": message_id}, {"has_task_tracking": 1, "message_id": 1}
            )
            if verify_doc:
                logger.info(
                    "enable_task_tracking_on_message: VERIFY - found doc with message_id=%s, "
                    "has_task_tracking=%s",
                    verify_doc.get("message_id"),
                    verify_doc.get("has_task_tracking"),
                )
            else:
                logger.error(
                    "enable_task_tracking_on_message: VERIFY FAILED - doc not found after update! "
                    "message_id=%s",
                    message_id,
                )

        if result.matched_count == 0:
            logger.error(
                "enable_task_tracking_on_message: No document found with message_id=%s",
                message_id,
            )
        elif result.modified_count == 0:
            # Document was found but data was identical - this is OK for retry scenarios
            logger.debug(
                "enable_task_tracking_on_message: Document found but not modified for message_id=%s "
                "(matched=%d, modified=%d) - data may be identical",
                message_id,
                result.matched_count,
                result.modified_count,
            )
        # Use matched_count instead of modified_count because MongoDB returns
        # modified_count=0 when the update data is identical to existing data,
        # which is still a successful operation (document was found and processed)
        return result.matched_count > 0

    async def verify_webhook_token_on_message(self, message_id: str) -> str | None:
        """
        Get the webhook_token_hash for a message by message_id.
        Returns the hash for verification, or None if not found.
        """
        result = await self.room_agent_messages_collection.find_one(
            {"message_id": message_id},
            {"webhook_token_hash": 1, "has_task_tracking": 1},
        )
        if not result:
            logger.warning(
                "verify_webhook_token_on_message: No document found with message_id=%s",
                message_id,
            )
            return None
        if not result.get("has_task_tracking"):
            logger.warning(
                "verify_webhook_token_on_message: Document found but has_task_tracking is not set "
                "for message_id=%s",
                message_id,
            )
            return None
        webhook_hash = result.get("webhook_token_hash")
        if not webhook_hash:
            logger.warning(
                "verify_webhook_token_on_message: Document found but webhook_token_hash is missing/empty "
                "for message_id=%s",
                message_id,
            )
        return webhook_hash

    async def update_last_notified_state(self, message_id: str, state: str) -> bool:
        """
        Update last_notified_state only if it's different (for idempotency).
        Returns True if this is a new notification (state changed).
        """
        result = await self.room_agent_messages_collection.update_one(
            {
                "message_id": message_id,
                "last_notified_state": {"$ne": state},
            },
            {"$set": {"last_notified_state": state}},
        )
        return result.modified_count > 0

    async def get_stale_task_messages(
        self, stale_minutes: int, non_terminal_states: list[str]
    ) -> list[RoomAgentMessage]:
        """
        Get room agent messages with tasks that haven't been updated recently.
        """
        threshold = utcnow() - timedelta(minutes=stale_minutes)
        cursor = self.room_agent_messages_collection.find(
            {
                "message_content.message_task.status.state": {
                    "$in": non_terminal_states
                },
                "task_updated_at": {"$lt": threshold},
                "has_task_tracking": True,
            }
        )
        results = await cursor.to_list(length=None)
        return [_ensure_task_validation(RoomAgentMessage(**msg)) for msg in results]

    async def get_expired_task_messages(
        self, max_age_hours: int, non_terminal_states: list[str]
    ) -> list[RoomAgentMessage]:
        """
        Get room agent messages with tasks that have been non-terminal for too long.
        """
        threshold = utcnow() - timedelta(hours=max_age_hours)
        cursor = self.room_agent_messages_collection.find(
            {
                "message_content.message_task.status.state": {
                    "$in": non_terminal_states
                },
                "task_created_at": {"$lt": threshold},
                "has_task_tracking": True,
            }
        )
        results = await cursor.to_list(length=None)
        return [_ensure_task_validation(RoomAgentMessage(**msg)) for msg in results]

    async def get_non_tracked_stale_task_messages(
        self, max_age_hours: int, non_terminal_states: list[str]
    ) -> list[RoomAgentMessage]:
        """
        Get non-tracked room agent messages with tasks stuck in non-terminal state.

        These are tasks where has_task_tracking is False (never started processing
        via the A2A tracked path) but have a task status set (not orphaned).
        Typically these are queued pipeline steps that were never picked up due to
        a server restart or processing failure.
        """
        threshold = utcnow() - timedelta(hours=max_age_hours)
        cursor = self.room_agent_messages_collection.find(
            {
                "message_content.message_task.status.state": {
                    "$in": non_terminal_states
                },
                "message_created_at": {"$lt": threshold},
                "has_task_tracking": {"$ne": True},
            }
        )
        results = await cursor.to_list(length=None)
        return [_ensure_task_validation(RoomAgentMessage(**msg)) for msg in results]

    async def get_orphaned_agent_messages(
        self, orphan_threshold_minutes: int
    ) -> list[RoomAgentMessage]:
        """
        Get room agent messages that were created but never processed.

        These are messages where:
        - has_task_tracking is False or missing (never started processing)
        - message_content.message_task.status is null/missing (no task status)
        - message_created_at is older than threshold (not just created)

        This catches messages orphaned when user refreshes before processRoomUserMessage runs.
        """
        threshold = utcnow() - timedelta(minutes=orphan_threshold_minutes)
        cursor = self.room_agent_messages_collection.find(
            {
                "message_type": "agent",
                "message_created_at": {"$lt": threshold},
                "$and": [
                    {
                        "$or": [
                            {"has_task_tracking": {"$ne": True}},
                            {"has_task_tracking": {"$exists": False}},
                        ]
                    },
                    {
                        "$or": [
                            {"message_content.message_task.status": {"$exists": False}},
                            {"message_content.message_task.status": None},
                        ]
                    },
                ],
            }
        )
        results = await cursor.to_list(length=None)
        return [_ensure_task_validation(RoomAgentMessage(**msg)) for msg in results]

    async def get_task_messages_for_room(
        self, room_id: str, limit: int = 50
    ) -> list[RoomAgentMessage]:
        """
        Get room agent messages with task tracking, newest first.
        """
        cursor = (
            self.room_agent_messages_collection.find(
                {
                    "room_id": room_id,
                    "has_task_tracking": True,
                }
            )
            .sort("task_created_at", -1)
            .limit(limit)
        )
        results = await cursor.to_list(length=None)
        return [_ensure_task_validation(RoomAgentMessage(**msg)) for msg in results]

    async def get_pending_task_messages_for_user(
        self, user_id: str, non_terminal_states: list[str]
    ) -> list[RoomAgentMessage]:
        """
        Get all non-terminal task messages for a user.
        """
        cursor = self.room_agent_messages_collection.find(
            {
                "user_id": user_id,
                "message_content.message_task.status.state": {
                    "$in": non_terminal_states
                },
                "has_task_tracking": True,
            }
        ).sort("task_created_at", -1)
        results = await cursor.to_list(length=None)
        return [_ensure_task_validation(RoomAgentMessage(**msg)) for msg in results]

    async def count_non_terminal_tasks_for_user(
        self, user_id: str, non_terminal_states: list[str]
    ) -> int:
        """
        Count non-terminal tasks for a user (for quota enforcement).
        """
        return await self.room_agent_messages_collection.count_documents(
            {
                "user_id": user_id,
                "message_content.message_task.status.state": {
                    "$in": non_terminal_states
                },
                "has_task_tracking": True,
            }
        )

    async def count_non_terminal_tasks_for_room(
        self, room_id: str, non_terminal_states: list[str]
    ) -> int:
        """
        Count non-terminal tasks for a room (for quota enforcement).
        """
        return await self.room_agent_messages_collection.count_documents(
            {
                "room_id": room_id,
                "message_content.message_task.status.state": {
                    "$in": non_terminal_states
                },
                "has_task_tracking": True,
            }
        )

    async def touch_task_message(self, message_id: str) -> bool:
        """
        Update task_updated_at timestamp without changing other fields.
        """
        result = await self.room_agent_messages_collection.update_one(
            {"message_id": message_id, "has_task_tracking": True},
            {"$set": {"task_updated_at": utcnow()}},
        )
        return result.modified_count > 0

    async def save_continuation_on_message(
        self, message_id: str, continuation_data: dict
    ) -> bool:
        """
        Save queue continuation state on a room agent message.
        """
        result = await self.room_agent_messages_collection.update_one(
            {"message_id": message_id},
            {
                "$set": {
                    "pending_continuation": continuation_data,
                    "task_updated_at": utcnow(),
                }
            },
        )
        if result.matched_count == 0:
            logger.error(
                "save_continuation_on_message: No document found with message_id=%s",
                message_id,
            )
        elif result.modified_count == 0:
            logger.warning(
                "save_continuation_on_message: Document found but not modified for message_id=%s "
                "(matched=%d, modified=%d)",
                message_id,
                result.matched_count,
                result.modified_count,
            )
        return result.modified_count > 0

    async def get_and_clear_continuation_on_message(
        self, message_id: str
    ) -> dict | None:
        """
        Get and atomically clear continuation state from a room agent message.
        """
        doc = await self.room_agent_messages_collection.find_one_and_update(
            {
                "message_id": message_id,
                "pending_continuation": {"$exists": True, "$ne": None},
            },
            {
                "$set": {
                    "pending_continuation": None,
                    "task_updated_at": utcnow(),
                }
            },
            return_document=False,
        )
        return doc.get("pending_continuation") if doc else None

    async def has_continuation_on_message(self, message_id: str) -> bool:
        """
        Check if a message has pending continuation state.
        """
        doc = await self.room_agent_messages_collection.find_one(
            {
                "message_id": message_id,
                "pending_continuation": {"$exists": True, "$ne": None},
            },
            {"_id": 1},
        )
        return doc is not None

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
        return result.matched_count > 0

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
        return result.matched_count > 0

    async def update_turn_notes(
        self, room_id: str, turn_id: str, turn_notes: dict
    ) -> bool:
        """
        Atomically update turn_notes for a single conversation turn using the
        MongoDB positional $ operator. Avoids the full-document read-modify-write
        cycle used by update_room_memory_by_room_id.
        """
        result = await self.room_memories_collection.update_one(
            {
                "room_id": room_id,
                "memory_content.conversation_history.turn_id": turn_id,
            },
            {
                "$set": {
                    "memory_content.conversation_history.$.turn_notes": turn_notes
                }
            },
        )
        return result.modified_count > 0

    # ------------------------------------------------------------------
    # Atomic room-memory mutations (Layer A)
    #
    # These methods mutate disjoint subsets of the room_memories document
    # using targeted MongoDB operators ($push, $set, $inc, arrayFilters)
    # so that concurrent calls do NOT conflict.
    #
    # LAYER B (future): Add optimistic concurrency control via a `version`
    #   field and conditional `{"version": expected_version}` filter on
    #   every update. On conflict, retry with exponential backoff.
    #   See docs/CONCURRENCY_ROADMAP.md for design sketch.
    #
    # LAYER C (future): For multi-instance deployments, add distributed
    #   locking (e.g. MongoDB advisory locks or Redis SETNX) so that only
    #   one backend instance processes a given room at a time.
    #   See docs/CONCURRENCY_ROADMAP.md for design sketch.
    # ------------------------------------------------------------------

    async def push_conversation_turn(
        self,
        room_id: str,
        turn: dict,
    ) -> tuple[bool, bool]:
        """Atomically append a turn to memory_content.conversation_history.

        Uses $push to avoid full-document read-modify-write.

        Returns:
            (modified, matched) — matched is False when the room_id document
            doesn't exist, letting callers distinguish 404 from write-failure.
        """
        result = await self.room_memories_collection.update_one(
            {"room_id": room_id},
            {
                "$push": {
                    "memory_content.conversation_history": turn,
                },
                "$inc": {"total_messages": 1},
                "$set": {"last_activity_at": utcnow()},
            },
        )
        return (result.modified_count > 0, result.matched_count > 0)

    async def push_and_trim_conversation_turn(
        self,
        room_id: str,
        turn: dict,
        max_turns: int,
        summary_stub: str,
        max_summary_chars: int = 4000,
    ) -> tuple[bool, bool]:
        """Atomically push a turn and trim history if it exceeds max_turns.

        Combines push_conversation_turn + get_conversation_history_length +
        trim_conversation_history into a single pipeline update, eliminating the
        race condition where concurrent writers could interleave between push
        and trim.

        Returns:
            (modified, matched) — matched is False when the room_id document
            doesn't exist.
        """
        if max_turns <= 0:
            return (False, False)
        if max_summary_chars < 10:
            max_summary_chars = 10

        result = await self.room_memories_collection.update_one(
            {"room_id": room_id},
            [
                {
                    "$set": {
                        "memory_content.conversation_history": {
                            "$concatArrays": [
                                {"$ifNull": ["$memory_content.conversation_history", []]},
                                [turn],
                            ]
                        },
                        "total_messages": {"$add": [{"$ifNull": ["$total_messages", 0]}, 1]},
                        "last_activity_at": utcnow(),
                    }
                },
                {
                    "$set": {
                        "memory_content.summary": {
                            "$cond": {
                                "if": {
                                    "$gt": [
                                        {"$size": "$memory_content.conversation_history"},
                                        max_turns,
                                    ]
                                },
                                "then": {
                                    "$let": {
                                        "vars": {
                                            "existing": {
                                                "$ifNull": ["$memory_content.summary", ""]
                                            },
                                        },
                                        "in": {
                                            "$let": {
                                                "vars": {
                                                    "concatenated": {
                                                        "$cond": {
                                                            "if": {"$eq": ["$$existing", ""]},
                                                            "then": summary_stub,
                                                            "else": {
                                                                "$concat": [
                                                                    "$$existing",
                                                                    "\n",
                                                                    summary_stub,
                                                                ]
                                                            },
                                                        }
                                                    }
                                                },
                                                "in": {
                                                    "$cond": {
                                                        "if": {
                                                            "$gt": [
                                                                {"$strLenCP": "$$concatenated"},
                                                                max_summary_chars,
                                                            ]
                                                        },
                                                        "then": {
                                                            "$concat": [
                                                                "...",
                                                                {
                                                                    "$substrCP": [
                                                                        "$$concatenated",
                                                                        {
                                                                            "$subtract": [
                                                                                {"$strLenCP": "$$concatenated"},
                                                                                max_summary_chars - 3,
                                                                            ]
                                                                        },
                                                                        max_summary_chars - 3,
                                                                    ]
                                                                },
                                                            ]
                                                        },
                                                        "else": "$$concatenated",
                                                    }
                                                },
                                            }
                                        },
                                    }
                                },
                                "else": {"$ifNull": ["$memory_content.summary", ""]},
                            }
                        },
                        "memory_content.conversation_history": {
                            "$slice": [
                                "$memory_content.conversation_history",
                                {"$multiply": [-1, max_turns]},
                            ]
                        },
                    }
                },
            ],
        )
        return (result.modified_count > 0, result.matched_count > 0)

    async def trim_conversation_history(
        self,
        room_id: str,
        max_turns: int,
        summary_addition: str,
        max_summary_chars: int = 4000,
    ) -> bool:
        """Atomically trim conversation_history and append to summary.

        Uses a pipeline update (MongoDB 4.2+) so both the summary
        concatenation and the array slice happen in a single atomic
        operation — no TOCTOU race on the summary field.
        """
        if max_turns <= 0:
            return False
        if max_summary_chars < 10:
            max_summary_chars = 10
        result = await self.room_memories_collection.update_one(
            {"room_id": room_id},
            [
                {
                    "$set": {
                        "memory_content.summary": {
                            "$let": {
                                "vars": {
                                    "existing": {
                                        "$ifNull": ["$memory_content.summary", ""]
                                    },
                                },
                                "in": {
                                    "$let": {
                                        "vars": {
                                            "concatenated": {
                                                "$cond": {
                                                    "if": {"$eq": ["$$existing", ""]},
                                                    "then": summary_addition,
                                                    "else": {
                                                        "$concat": [
                                                            "$$existing",
                                                            "\n",
                                                            summary_addition,
                                                        ]
                                                    },
                                                }
                                            }
                                        },
                                        "in": {
                                            "$cond": {
                                                "if": {
                                                    "$gt": [
                                                        {"$strLenCP": "$$concatenated"},
                                                        max_summary_chars,
                                                    ]
                                                },
                                                "then": {
                                                    "$concat": [
                                                        "...",
                                                        {
                                                            "$substrCP": [
                                                                "$$concatenated",
                                                                {
                                                                    "$subtract": [
                                                                        {"$strLenCP": "$$concatenated"},
                                                                        max_summary_chars - 3,
                                                                    ]
                                                                },
                                                                max_summary_chars - 3,
                                                            ]
                                                        },
                                                    ]
                                                },
                                                "else": "$$concatenated",
                                            }
                                        },
                                    }
                                },
                            }
                        },
                        "memory_content.conversation_history": {
                            "$slice": [
                                {"$ifNull": ["$memory_content.conversation_history", []]},
                                {"$multiply": [-1, max_turns]},
                            ]
                        },
                    }
                }
            ],
        )
        return result.modified_count > 0

    async def update_room_summary_atomic(
        self,
        room_id: str,
        room_summary: dict,
        new_facts: list[dict] | None = None,
        max_facts: int = 50,
    ) -> bool:
        """Atomically update room_summary and optionally push new facts.

        Does NOT touch conversation_history — safe to run concurrently
        with push_conversation_turn and compact_turns_bulk.
        """
        update: dict = {
            "$set": {"room_summary": room_summary},
        }

        if new_facts:
            update["$push"] = {
                "room_facts": {
                    "$each": new_facts,
                    "$slice": -max_facts,
                }
            }

        result = await self.room_memories_collection.update_one(
            {"room_id": room_id},
            update,
        )
        return result.modified_count > 0

    async def compact_turns_bulk(
        self,
        room_id: str,
        compacted_turns: list[dict],
    ) -> bool:
        """Mark turns as compact using arrayFilters + bulk_write.

        NOTE: bulk_write(ordered=True) guarantees ordering but NOT full atomicity.
        A crash mid-batch may leave some turns compacted and others still FULL.
        This is safe because: (1) content is already persisted in
        conversation_content before this call, and (2) re-running compaction on
        an already-compacted turn is a no-op (the arrayFilter won't match).

        Each entry in compacted_turns must have:
          - turn_id: str
          - content_ref: dict (ContentReference.model_dump())
          - estimated_tokens_compact: int

        Does NOT rewrite the entire document — only touches the specific
        array elements being compacted plus the total_compactions counter.
        """
        from pymongo import UpdateOne

        operations = []
        for t in compacted_turns:
            operations.append(
                UpdateOne(
                    {"room_id": room_id},
                    {
                        "$set": {
                            "memory_content.conversation_history.$[elem].representation": "compact",
                            "memory_content.conversation_history.$[elem].content": None,
                            "memory_content.conversation_history.$[elem].content_ref": t["content_ref"],
                            "memory_content.conversation_history.$[elem].estimated_tokens_compact": t.get(
                                "estimated_tokens_compact", 0
                            ),
                        },
                    },
                    array_filters=[{"elem.turn_id": t["turn_id"]}],
                )
            )

        operations.append(
            UpdateOne(
                {"room_id": room_id},
                {
                    "$inc": {"total_compactions": 1},
                    "$set": {"last_activity_at": utcnow()},
                },
            )
        )

        result = await self.room_memories_collection.bulk_write(
            operations, ordered=True
        )
        return result.modified_count > 0

    async def get_room_summary_projection(
        self, room_id: str
    ) -> dict | None:
        """Lightweight projection: fetch only room_summary and room_facts."""
        return await self.room_memories_collection.find_one(
            {"room_id": room_id},
            {"room_summary": 1, "room_facts": 1},
        )

    async def get_conversation_history_length(
        self, room_id: str
    ) -> int:
        """Return the number of turns in memory_content.conversation_history."""
        pipeline = [
            {"$match": {"room_id": room_id}},
            {"$project": {"count": {"$size": {"$ifNull": ["$memory_content.conversation_history", []]}}}},
        ]
        async for doc in self.room_memories_collection.aggregate(pipeline):
            return doc.get("count", 0)
        return 0

    async def delete_room_memory_by_room_id(self, room_id: str) -> bool:
        """
        Delete a room memory by room_id
        """
        result = await self.room_memories_collection.delete_one({"room_id": room_id})
        return result.deleted_count > 0

    # ======================== User Memory management ========================

    async def get_user_memory(self, user_id: str):
        """Get or create a UserMemory document by user_id."""
        doc = await self.user_memories_collection.find_one({"user_id": user_id})
        if doc:
            from models.memory import UserMemory
            return UserMemory(**doc)
        return None

    async def upsert_user_memory(self, user_id: str, update_fields: dict) -> bool:
        """Upsert a UserMemory document. Creates if not found."""
        result = await self.user_memories_collection.update_one(
            {"user_id": user_id},
            {"$set": update_fields},
            upsert=True,
        )
        return result.matched_count > 0 or result.upserted_id is not None

    async def increment_user_interactions(self, user_id: str) -> bool:
        """Atomically increment total_interactions and update last_active_at."""
        from common.utils.time import utcnow

        result = await self.user_memories_collection.update_one(
            {"user_id": user_id},
            {
                "$inc": {"total_interactions": 1},
                "$set": {"last_active_at": utcnow()},
                "$setOnInsert": {"user_id": user_id, "created_at": utcnow()},
            },
            upsert=True,
        )
        return result.matched_count > 0 or result.upserted_id is not None

    # ======================== Agent Memory management ========================

    async def get_agent_memory(self, agent_id: str):
        """Get an AgentMemory document by agent_id."""
        doc = await self.agent_memories_collection.find_one({"agent_id": agent_id})
        if doc:
            from models.memory import AgentMemory
            return AgentMemory(**doc)
        return None

    async def record_agent_call(
        self,
        agent_id: str,
        success: bool,
        response_time_ms: float,
    ) -> bool:
        """Atomically record an agent call outcome.

        Stores total_response_time_ms alongside total_calls so that
        average_response_time_ms can be computed without a second round-trip.
        """
        from common.utils.time import utcnow

        inc_fields: dict = {"total_calls": 1, "total_response_time_ms": response_time_ms}
        if success:
            inc_fields["successful_calls"] = 1

        result = await self.agent_memories_collection.update_one(
            {"agent_id": agent_id},
            {
                "$inc": inc_fields,
                "$set": {"last_called_at": utcnow()},
                "$setOnInsert": {
                    "agent_id": agent_id,
                },
            },
            upsert=True,
        )

        return result.matched_count > 0 or result.upserted_id is not None

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

    async def claim_stuck_supervisor_trajectory(
        self, message_id: str
    ) -> bool:
        """Atomically transition a supervisor trajectory from "running" to "recovering".

        Uses ``find_one_and_update`` with a status precondition so that only one
        recovery worker (even across multiple server instances) can claim a given
        stuck trajectory.

        Returns True if this call successfully claimed the message, False if
        another worker already claimed it or the message was not found.
        """
        result = await self.room_user_messages_collection.find_one_and_update(
            {
                "message_id": message_id,
                "extend_info.supervisor_trajectory.status": TrajectoryStatus.RUNNING,
            },
            {
                "$set": {
                    "extend_info.supervisor_trajectory.status": TrajectoryStatus.RECOVERING,
                }
            },
        )
        if result:
            logger.info(
                "claim_stuck_supervisor_trajectory: claimed message %s",
                message_id,
            )
            return True
        return False

    async def get_stuck_supervisor_trajectory_messages(
        self, older_than_minutes: int, limit: int = 100
    ) -> list[dict]:
        """Return user messages whose supervisor trajectory is stuck in ``running``.

        Only messages older than ``older_than_minutes`` are returned so that
        actively-running trajectories are not mistakenly flagged.

        Each result dict contains only ``message_id`` and ``room_id``.
        """
        threshold = utcnow() - timedelta(minutes=older_than_minutes)
        docs = await self.room_user_messages_collection.find(
            {
                "extend_info.supervisor_trajectory.status": TrajectoryStatus.RUNNING,
                "extend_info.supervisor_v2": True,
                "message_created_at": {"$lt": threshold},
            },
            {"message_id": 1, "room_id": 1, "_id": 0},
        ).to_list(length=limit)
        return docs

    async def cancel_message(self, message_id: str, user_id: str) -> bool:
        """
        Mark a message as cancelled in the database.

        Args:
            message_id: The message ID to cancel
            user_id: The user who cancelled the message

        Returns:
            bool: True if cancellation was recorded successfully
        """
        doc = {
            "message_id": message_id,
            "user_id": user_id,
            "cancelled_at": utcnow(),
        }

        try:
            # Only insert if not exist (upsert with $setOnInsert)
            await self.cancelled_messages_collection.update_one(
                {"message_id": message_id}, {"$setOnInsert": doc}, upsert=True
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
  
    async def create_task_tracking_indexes(self) -> None:
        """
        Create indexes for task tracking on room_agent_messages collection.
        Should be called on application startup.
        """
        try:
            collection = self.room_agent_messages_collection

            # Task tracking flag index (for filtering messages with task tracking)
            await collection.create_index(
                "has_task_tracking",
                sparse=True,
            )

            # Stale task detection (tasks not updated recently)
            await collection.create_index(
                [
                    ("task_updated_at", 1),
                    ("message_content.message_task.status.state", 1),
                ],
                sparse=True,
            )

            # Expired task detection (tasks created too long ago)
            await collection.create_index(
                [
                    ("task_created_at", 1),
                    ("message_content.message_task.status.state", 1),
                ],
                sparse=True,
            )

            # User's pending tasks lookup
            await collection.create_index(
                [
                    ("user_id", 1),
                    ("message_content.message_task.status.state", 1),
                    ("has_task_tracking", 1),
                ],
                sparse=True,
            )

            # Room's task messages lookup
            await collection.create_index(
                [("room_id", 1), ("has_task_tracking", 1), ("task_created_at", -1)],
                sparse=True,
            )

            print("Task tracking indexes created successfully on room_agent_messages")
        except Exception as e:
            print(f"Error creating task tracking indexes: {e}")

    async def create_context_memory_indexes(self) -> None:
        """
        Create indexes for context memory system collections.

        This creates indexes on:
        - conversation_content: For lossless compaction storage
        - user_memories: For user preferences
        - agent_memories: For agent performance history
        - room_memories: Unique constraint on room_id (§9.1)

        See CONTEXT_MEMORY_SYSTEM_DESIGN.md §6.6 and §9.1 for schema details.
        Should be called on application startup.

        Raises:
            Exception: Re-raised if a critical unique index fails (indicates
                duplicate data that must be resolved before the system can
                guarantee data integrity).
        """
        critical_unique_indexes: list[tuple[str, str]] = []

        async def _create_index(
            coll,
            keys,
            *,
            name: str,
            unique: bool = False,
            critical: bool = False,
            **kwargs,
        ) -> None:
            """Helper to create an index with proper error handling."""
            try:
                await coll.create_index(keys, unique=unique, name=name, **kwargs)
                logger.info("Index '%s' created on %s", name, coll.name)
            except Exception as e:
                if unique and critical:
                    logger.error(
                        "CRITICAL: Failed to create unique index '%s' on %s: %s. "
                        "This likely means duplicate documents exist. "
                        "Run a deduplication script before retrying.",
                        name,
                        coll.name,
                        e,
                    )
                    critical_unique_indexes.append((coll.name, name))
                elif unique:
                    logger.warning(
                        "Failed to create unique index '%s' on %s: %s. "
                        "Duplicate documents may exist.",
                        name,
                        coll.name,
                        e,
                    )
                else:
                    logger.warning(
                        "Failed to create index '%s' on %s (may already exist): %s",
                        name,
                        coll.name,
                        e,
                    )

        # === conversation_content collection ===
        content_coll = self.conversation_content_collection

        # UNIQUE index for idempotent upsert in compact_room_memory (§6.3)
        # Ensures crashed-and-retried compaction never creates duplicate documents
        await _create_index(
            content_coll,
            [("room_id", 1), ("turn_id", 1)],
            name="room_turn_unique",
            unique=True,
            critical=True,
        )

        # Fast room-level queries for content retrieval
        await _create_index(
            content_coll,
            [("room_id", 1), ("stored_at", -1)],
            name="room_stored_at",
        )

        # Text index on content and turn_notes for hybrid search (§8.3)
        # Enables keyword search on both full content and compact turn metadata
        await _create_index(
            content_coll,
            [
                ("content", "text"),
                ("turn_notes.keywords", "text"),
                ("turn_notes.entities", "text"),
                ("turn_notes.one_liner", "text"),
            ],
            name="turn_notes_text",
        )

        # TTL index for content expiry (if configured)
        # Only applies to documents with expires_at set
        await _create_index(
            content_coll,
            "expires_at",
            name="content_ttl",
            expireAfterSeconds=0,
            sparse=True,
        )

        # === user_memories collection ===
        await _create_index(
            self.user_memories_collection,
            "user_id",
            name="user_id_unique",
            unique=True,
            critical=True,
        )

        # === agent_memories collection ===
        await _create_index(
            self.agent_memories_collection,
            "agent_id",
            name="agent_id_unique",
            unique=True,
            critical=True,
        )

        # === room_memories collection ===
        await _create_index(
            self.room_memories_collection,
            "room_id",
            name="room_id_unique",
            unique=True,
            critical=True,
        )

        # If any critical unique index failed, raise to alert operators
        if critical_unique_indexes:
            failed = ", ".join(f"{coll}.{idx}" for coll, idx in critical_unique_indexes)
            raise RuntimeError(
                f"Critical unique index creation failed: {failed}. "
                "Duplicate documents likely exist. Resolve before proceeding."
            )


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
