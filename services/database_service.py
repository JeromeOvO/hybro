import uuid
from typing import Any

from a2a.types import AgentCard

from common.utils.logger import get_logger
from database.mongodb import mongodb
from database.pinecone_db import pinecone_db
from models.agent import Agent, AgentStatus
from models.agent_group import AgentGroup
from models.memory import ChatContext, RoomMemory
from models.room import MessageContent, Room, RoomAgentMessage, RoomUserMessage
from models.task import BaseTask, MetaTask, TaskSession
from services.openai_service import openai_service

logger = get_logger(__name__)

# Database Service designed for:
# - consistent and available both in different databases
# - uuid management
# - One for all DB services implementation


class DatabaseService:
    def __init__(self):
        self.mongo = mongodb
        self.pinecone = pinecone_db
        # Import here to avoid circular import

        self.ai_service = openai_service

    # agent management
    async def add_agent(self, agent: Agent) -> bool:
        """
        Add an agent to both MongoDB and Pinecone databases.
        Ensures consistency by rolling back if either operation fails.

        Args:
            agent: Agent

        Returns:
            bool: True if successful, False otherwise

        Raises:
            Exception: If any database operation fails
        """
        # check if agent_id is provided
        if agent.agent_id == "":
            agent.agent_id = str(uuid.uuid4())
        else:
            # check if agent already exists
            mongo_result = await self.get_agent_by_agent_id(agent.agent_id)
            if mongo_result:
                raise ValueError("Agent already exists, repeated agent_id")

        # get embedding of agent description
        embedding_data = await self.ai_service.get_embedding(
            agent.agent_card.description
        )
        vector_data = {
            "id": str(agent.agent_id),
            "values": embedding_data,
            "metadata": {"type": "a2a_agent", "agent_id": str(agent.agent_id)},
        }

        mongo_id = None

        try:
            # Add to MongoDB
            mongo_id = await self.mongo.add_agent(agent)
            # Add to Pinecone
            self.pinecone.upsert([vector_data])

            return True

        except Exception as e:
            # Rollback MongoDB insertion if needed
            if mongo_id:
                try:
                    await self.mongo.delete_agent_by_agent_id(agent.agent_id)
                except Exception as delete_error:
                    print(f"Rollback failed: {delete_error}")

            logger.error(f"Failed to add agent {agent.agent_id} to databases: {str(e)}")
            return False

    async def delete_agent_by_agent_id(self, agent_id: str) -> bool:
        """
        Delete an agent from both MongoDB and Pinecone databases.
        Ensures consistency by attempting to delete from both databases.

        Args:
            agent_id: The ID of the agent to delete

        Returns:
            bool: True if deletion was successful

        Raises:
            Exception: If any database operation fails
        """
        mongo_success = False
        pinecone_success = False

        try:
            # Delete from MongoDB
            mongo_success = await self.mongo.delete_agent_by_agent_id(agent_id)

            # Delete from Pinecone
            self.pinecone.delete([str(agent_id)])
            pinecone_success = True

            return mongo_success and pinecone_success

        except Exception as e:
            logger.error(f"Failed to delete agent {agent_id} from databases: {str(e)}")
            return False

    async def get_agent_by_agent_id(self, agent_id: str) -> Agent | None:
        """
        Get an agent by agent_id from both MongoDB and Pinecone databases.
        """
        return await self.mongo.get_agent_by_agent_id(agent_id)

    async def get_agents_by_provider_id(self, provider_id: str) -> Agent | None:
        """
        Get agents by provider_id from MongoDB.
        """
        return await self.mongo.get_agents_by_provider_id(provider_id)

    async def get_agent_name_by_agent_id(self, agent_id: str) -> str | None:
        """
        Get an agent's name by agent_id from both MongoDB and Pinecone databases.
        """
        agent = await self.get_agent_by_agent_id(agent_id)
        return agent.agent_card.name if agent else None

    async def get_all_agents(self) -> list[Agent]:
        """
        Get all agents from both MongoDB and Pinecone databases.
        """
        return await self.mongo.get_all_agents()

    def _build_visibility_filter(self, user_id: str | None) -> dict[str, Any]:
        """Build a MongoDB filter for agent visibility.

        Public agents include documents where is_public is True or missing.
        If user_id is provided, include that user's private agents.
        """
        public_filter: dict[str, Any] = {
            "$or": [
                {"is_public": True},
                {"is_public": {"$exists": False}},
            ]
        }
        if user_id:
            return {
                "$or": [
                    {"provider_id": user_id},
                    {"is_public": True},
                    {"is_public": {"$exists": False}},
                ]
            }
        return public_filter

    async def get_all_visible_agents(self, user_id: str | None = None) -> list[Agent]:
        """
        Get all visible agents from MongoDB.

        Args:
            user_id: Optional user ID - if provided, includes user's private agents

        Returns:
            List[Agent]: List of agents visible to the requesting user
        """
        query = self._build_visibility_filter(user_id)
        return await self.mongo.get_agents_with_conditions(query)

    async def get_all_active_agents(self, user_id: str | None = None) -> list[Agent]:
        """
        Get all active and visible agents from MongoDB.
        
        Args:
            user_id: Optional user ID - if provided, includes user's private agents
        
        Returns:
            List[Agent]: List of agents with active status that are either:
                - Public (visible to everyone)
                - Private but owned by the requesting user
        """
        visibility_filter = self._build_visibility_filter(user_id)
        query: dict[str, Any] = {
            "$and": [
                {"agent_status": AgentStatus.active.value},
                visibility_filter,
            ]
        }
        visible_agents = await self.mongo.get_agents_with_conditions(query)
        logger.debug(
            "DatabaseService: Found %d visible active agents (user_id=%s)",
            len(visible_agents),
            user_id
        )
        return visible_agents

    async def get_agents_with_conditions(
        self, query: dict[str, Any] | None = None, limit: int = 0
    ) -> list[Agent]:
        """
        Get agents with conditions from both MongoDB and Pinecone databases.
        """
        return await self.mongo.get_agents_with_conditions(query, limit)

    async def get_agents_with_conditions_visible(
        self,
        user_id: str | None = None,
        query: dict[str, Any] | None = None,
        limit: int = 0,
    ) -> list[Agent]:
        """
        Get agents with conditions, filtered by visibility.
        """
        visibility_filter = self._build_visibility_filter(user_id)

        combined_query: dict[str, Any]
        if not query:
            combined_query = visibility_filter
        elif "$and" in query:
            combined_query = {"$and": [*query["$and"], visibility_filter]}
        else:
            combined_query = {"$and": [query, visibility_filter]}

        return await self.mongo.get_agents_with_conditions(combined_query, limit)

    async def query_similar_agents(
        self,
        query_text: str,
        count: int = 5,
        allowed_agent_ids: list[str] | None = None,
        active_only: bool = True,
        user_id: str | None = None,
    ) -> list[Agent]:
        """
        Find similar agents based on task description embedding and return their full information

        Args:
            query_text: Text to find similar agents for
            count: Number of results to return
            allowed_agent_ids: Optional list of agent IDs to restrict the search to
            active_only: If True, only return agents with active status (default: True)
            user_id: Optional user ID to include private agents

        Returns:
            List[Agent]: List of similar agents with complete information from MongoDB
        """
        # Make sure to await the embedding generation
        embedding = await self.ai_service.get_embedding(query_text)

        # Request more candidates from Pinecone to account for filtering
        # We may need to filter out inactive agents, so get extra candidates
        top_k = count * 3 if active_only else count
        pinecone_filter = None
        if allowed_agent_ids:
            # Limit search to the allowed IDs; bump top_k to avoid truncation.
            top_k = max(len(allowed_agent_ids), top_k)
            pinecone_filter = {
                "agent_id": {"$in": [str(aid) for aid in allowed_agent_ids]}
            }

        # Then use the embedding with Pinecone - remove the incompatible parameter
        results = self.pinecone.query(
            vector=embedding, top_k=top_k, filter=pinecone_filter
        )

        # Extract agent IDs from Pinecone results
        agent_ids = (
            [match["id"] for match in getattr(results, "matches", [])]
            if results
            else []
        )

        if allowed_agent_ids:
            # Ensure we only keep allowed IDs, even if filter was empty
            allowed_set = set(str(aid) for aid in allowed_agent_ids)
            agent_ids = [aid for aid in agent_ids if aid in allowed_set]

        if not agent_ids:
            return []

        # Fetch complete agent information from MongoDB
        query = {"agent_id": {"$in": agent_ids}}
        
        # Apply visibility filter
        visibility_filter = self._build_visibility_filter(user_id)
        query = {"$and": [query, visibility_filter]}
        
        agents = await self.mongo.get_agents_with_conditions(query)

        # Filter for active agents only if requested
        if active_only:
            agents = [
                agent for agent in agents if agent.agent_status == AgentStatus.active
            ]
            logger.debug(
                "DatabaseService: Filtered to %d active agents from query results",
                len(agents),
            )

        # Sort agents in the same order as the Pinecone results
        id_to_position = {id: i for i, id in enumerate(agent_ids)}
        sorted_agents = sorted(
            agents, key=lambda agent: id_to_position.get(agent.agent_id, float("inf"))
        )

        # Return only the requested count
        return sorted_agents[:count]

    async def update_agent_agent_card_by_agent_id(
        self, agent_id: str, agent_card: AgentCard
    ) -> bool:
        """
        Update an agent's agent card in both MongoDB and Pinecone databases.
        """

        # get agent from mongo
        mongo_result = await self.mongo.get_agent_by_agent_id(agent_id)
        if not mongo_result:
            raise ValueError("Agent not found")

        # get embedding of agent description
        embedding_data = await self.ai_service.get_embedding(agent_card.description)
        vector_data = {
            "id": str(agent_id),
            "values": embedding_data,
            "metadata": {"type": "a2a_agent", "agent_id": str(agent_id)},
        }

        try:
            # Update MongoDB
            mongo_success = await self.mongo.update_agent_agent_card_by_agent_id(
                agent_id, agent_card
            )
            # Update Pinecone
            self.pinecone.upsert([vector_data])
            return mongo_success
        except Exception as e:
            logger.error(f"Failed to update agent {agent_id} in databases: {str(e)}")
            return False

    async def update_agent_by_agent_id(self, agent_id: str, agent: Agent) -> bool:
        """
        Update an agent in both MongoDB and Pinecone databases.
        """

        # get agent from mongo
        mongo_result = await self.mongo.get_agent_by_agent_id(agent_id)
        if not mongo_result:
            raise ValueError("Agent not found")

        # get embedding of agent description
        embedding_data = await self.ai_service.get_embedding(
            agent.agent_card.description
        )
        vector_data = {
            "id": str(agent_id),
            "values": embedding_data,
            "metadata": {"type": "a2a_agent", "agent_id": str(agent_id)},
        }

        try:
            # Update MongoDB
            mongo_success = await self.mongo.update_agent_by_agent_id(agent_id, agent)
            # Update Pinecone
            self.pinecone.upsert([vector_data])
            return mongo_success
        except Exception as e:
            logger.error(f"Failed to update agent {agent_id} in databases: {str(e)}")
            return False

    # task management
    async def add_base_task(self, base_task: BaseTask) -> bool:
        """
        Add a base task to the database
        """

        # check if task_id is provided
        if base_task.task_id == "":
            base_task.task_id = str(uuid.uuid4())

        try:
            await self.mongo.add_base_task(base_task)
            return True
        except Exception as e:
            logger.error(
                f"Failed to add base task {base_task.task_id} to databases: {str(e)}"
            )
            return False

    async def add_meta_task(self, meta_task: MetaTask) -> bool:
        """
        Add a meta task to the database
        """

        # check if task_id is provided
        if meta_task.task_id == "":
            meta_task.task_id = str(uuid.uuid4())

        try:
            await self.mongo.add_meta_task(meta_task)
            return True
        except Exception as e:
            logger.error(
                f"Failed to add meta task {meta_task.task_id} to databases: {str(e)}"
            )
            return False

    async def add_task_session(self, task_session: TaskSession) -> bool:
        """
        Add a task session to the database
        """

        # check if session_id is provided
        if task_session.session_id == "":
            task_session.session_id = str(uuid.uuid4())

        try:
            await self.mongo.add_task_session(task_session)
            return True
        except Exception as e:
            logger.error(
                f"Failed to add task session {task_session.session_id} to databases: {str(e)}"
            )
            return False

    async def delete_meta_tasks_by_parent_task_id(self, parent_task_id: str):
        """
        Recursively delete all meta tasks whose parent_task_id is the given parent_task_id.
        """
        meta_tasks = await self.mongo.get_meta_tasks_by_parent_task_id(parent_task_id)
        for meta_task in meta_tasks:
            await self.delete_meta_task_by_task_id(meta_task["task_id"])

    async def delete_base_task_by_task_id(self, task_id: str) -> bool:
        """
        Delete a base task by task_id, and recursively delete all its meta tasks.
        """
        try:
            await self.delete_meta_tasks_by_parent_task_id(task_id)
            await self.mongo.delete_base_task_by_task_id(task_id)
            return True
        except Exception as e:
            logger.error(
                f"Failed to delete base task {task_id} from databases: {str(e)}"
            )
            return False

    async def delete_meta_task_by_task_id(self, task_id: str) -> bool:
        """
        Delete a meta task by task_id, and recursively delete all its sub meta tasks.
        """
        try:
            await self.delete_meta_tasks_by_parent_task_id(task_id)
            await self.mongo.delete_meta_task_by_task_id(task_id)
            return True
        except Exception as e:
            logger.error(
                f"Failed to delete meta task {task_id} from databases: {str(e)}"
            )
            return False

    async def delete_task_session_by_session_id(self, session_id: str) -> bool:
        """
        Delete a task session by session_id, and delete all its base tasks and their meta tasks.
        """
        try:
            base_tasks = await self.mongo.get_base_tasks_by_session_id(session_id)
            for base_task in base_tasks:
                await self.delete_base_task_by_task_id(base_task["task_id"])
            await self.mongo.delete_task_session_by_session_id(session_id)
            return True
        except Exception as e:
            logger.error(
                f"Failed to delete task session {session_id} from databases: {str(e)}"
            )
            return False

    async def get_base_task_by_task_id(self, task_id: str) -> BaseTask | None:
        """
        Get a base task by task_id
        """
        return await self.mongo.get_base_task_by_task_id(task_id)

    async def get_meta_task_by_task_id(self, task_id: str) -> MetaTask | None:
        """
        Get a meta task by task_id
        """
        return await self.mongo.get_meta_task_by_task_id(task_id)

    async def get_task_session_by_session_id(
        self, session_id: str
    ) -> TaskSession | None:
        """
        Get a task session by session_id
        """
        return await self.mongo.get_task_session_by_session_id(session_id)

    async def get_task_sessions_by_user_name(self, user_name: str) -> list[TaskSession]:
        """
        Get all task sessions by user_name
        """
        return await self.mongo.get_task_sessions_by_user_name(user_name)

    async def get_base_tasks_by_session_id(self, session_id: str) -> list[BaseTask]:
        """
        Get all base tasks by session_id
        """
        return await self.mongo.get_base_tasks_by_session_id(session_id)

    async def get_meta_tasks_by_parent_task_id(
        self, parent_task_id: str
    ) -> list[MetaTask]:
        """
        Get all meta tasks by parent_task_id
        """
        return await self.mongo.get_meta_tasks_by_parent_task_id(parent_task_id)

    async def update_meta_task_by_task_id(
        self, task_id: str, meta_task: MetaTask
    ) -> bool:
        """
        Update a meta task by task_id
        """
        return await self.mongo.update_meta_task_by_task_id(task_id, meta_task)

    async def update_base_task_by_task_id(
        self, task_id: str, base_task: BaseTask
    ) -> bool:
        """
        Update a base task by task_id
        """
        return await self.mongo.update_base_task_by_task_id(task_id, base_task)

    async def update_task_session_by_session_id(
        self, session_id: str, task_session: TaskSession
    ) -> bool:
        """
        Update a task session by session_id
        """
        return await self.mongo.update_task_session_by_session_id(
            session_id, task_session
        )

    # chat context management
    async def add_chat_context(self, chat_context: ChatContext) -> bool:
        """
        Add a chat context to the database
        """
        if chat_context.memory_id == "":
            chat_context.memory_id = str(uuid.uuid4())
        try:
            await self.mongo.add_chat_context(chat_context)
            return True
        except Exception as e:
            logger.error(
                f"Failed to add chat context {chat_context.memory_id} to databases: {str(e)}"
            )
            return False

    async def get_chat_context_by_session_id(
        self, session_id: str
    ) -> ChatContext | None:
        """
        Get a chat context by session_id
        """
        return await self.mongo.get_chat_context_by_session_id(session_id)

    async def update_chat_context_by_session_id(
        self, session_id: str, chat_context: ChatContext
    ) -> bool:
        """
        Update a chat context by session_id
        """
        try:
            await self.mongo.update_chat_context_by_session_id(session_id, chat_context)
            return True
        except Exception as e:
            logger.error(
                f"Failed to update chat context {session_id} in databases: {str(e)}"
            )
            return False

    async def delete_chat_context_by_session_id(self, session_id: str) -> bool:
        """
        Delete a chat context by session_id
        """
        try:
            await self.mongo.delete_chat_context_by_session_id(session_id)
            return True
        except Exception as e:
            logger.error(
                f"Failed to delete chat context {session_id} from databases: {str(e)}"
            )
            return False

    # room management
    async def add_room(self, room: Room) -> bool:
        """
        Add a room to the database
        """
        if room.room_id == "":
            room.room_id = str(uuid.uuid4())
        try:
            await self.mongo.add_room(room)
            return True
        except Exception as e:
            logger.error(f"Failed to add room {room.room_id} to databases: {str(e)}")
            return False

    async def get_room_by_room_id(self, room_id: str) -> Room | None:
        """
        Get a room by room_id
        """
        try:
            return await self.mongo.get_room_by_room_id(room_id)
        except Exception as e:
            logger.error(f"Failed to get room {room_id} from databases: {str(e)}")
            return None

    async def get_rooms_by_room_owner_id(self, room_owner_id: str) -> list[Room]:
        """
        Get rooms by room_owner_id
        """
        try:
            return await self.mongo.get_rooms_by_room_owner_id(room_owner_id)
        except Exception as e:
            logger.error(
                f"Failed to get rooms by room owner id {room_owner_id} from databases: {str(e)}"
            )
            return []

    async def update_room_by_room_id(self, room_id: str, room: Room) -> bool:
        """
        Update a room by room_id
        """
        try:
            return await self.mongo.update_room_by_room_id(room_id, room)
        except Exception as e:
            logger.error(f"Failed to update room {room_id} in databases: {str(e)}")
            return False

    async def delete_room_by_room_id(self, room_id: str) -> bool:
        """
        Delete a room by room_id
        """
        try:
            return await self.mongo.delete_room_by_room_id(room_id)
        except Exception as e:
            logger.error(f"Failed to delete room {room_id} from databases: {str(e)}")
            return False

    # room user message management
    async def add_room_user_message(self, room_user_message: RoomUserMessage) -> bool:
        """
        Add a room user message to the database
        """
        if room_user_message.message_id == "":
            room_user_message.message_id = str(uuid.uuid4())
        try:
            await self.mongo.add_room_user_message(room_user_message)
            return True
        except Exception as e:
            logger.error(
                f"Failed to add room user message {room_user_message.message_id} to databases: {str(e)}"
            )
            return False

    async def get_room_user_messages_by_room_id(
        self, room_id: str
    ) -> list[RoomUserMessage]:
        """
        Get room user messages by room_id
        """
        try:
            return await self.mongo.get_room_user_messages_by_room_id(room_id)
        except Exception as e:
            logger.error(
                f"Failed to get room user messages by room id {room_id} from databases: {str(e)}"
            )
            return []

    async def get_room_user_message_by_message_id(
        self, message_id: str
    ) -> RoomUserMessage | None:
        """
        Get a room user message by message_id
        """
        try:
            return await self.mongo.get_room_user_message_by_message_id(message_id)
        except Exception as e:
            logger.error(
                f"Failed to get room user message by message id {message_id} from databases: {str(e)}"
            )
            return None

    async def update_room_user_message_by_message_id(
        self, message_id: str, room_user_message: RoomUserMessage
    ) -> bool:
        """
        Update a room user message by message_id
        """
        try:
            return await self.mongo.update_room_user_message_by_message_id(
                message_id, room_user_message
            )
        except Exception as e:
            logger.error(
                f"Failed to update room user message {message_id} in databases: {str(e)}"
            )
            return False

    # room agent message management
    async def add_room_agent_message(
        self, room_agent_message: RoomAgentMessage
    ) -> bool:
        """
        Add a room agent message to the database
        """
        if room_agent_message.message_id == "":
            room_agent_message.message_id = str(uuid.uuid4())
        try:
            await self.mongo.add_room_agent_message(room_agent_message)
            return True
        except Exception as e:
            logger.error(
                f"Failed to add room agent message {room_agent_message.message_id} to databases: {str(e)}"
            )
            return False

    async def get_room_agent_messages_by_room_id(
        self, room_id: str
    ) -> list[RoomAgentMessage]:
        """
        Get room agent messages by room_id
        """
        try:
            return await self.mongo.get_room_agent_messages_by_room_id(room_id)
        except Exception as e:
            logger.error(
                f"Failed to get room agent messages by room id {room_id} from databases: {str(e)}"
            )
            return []

    async def get_room_agent_message_by_message_id(
        self, message_id: str
    ) -> RoomAgentMessage | None:
        """
        Get a room agent message by message_id
        """
        try:
            return await self.mongo.get_room_agent_message_by_message_id(message_id)
        except Exception as e:
            logger.error(
                f"Failed to get room agent message by message id {message_id} from databases: {str(e)}"
            )
            return None

    async def get_room_agent_messages_by_related_message_id(
        self, related_message_id: str
    ) -> list[RoomAgentMessage]:
        """
        Get room agent messages by related_message_id
        """
        try:
            return await self.mongo.get_room_agent_messages_by_related_message_id(
                related_message_id
            )
        except Exception as e:
            logger.error(
                f"Failed to get room agent messages by related message id {related_message_id} from databases: {str(e)}"
            )
            return []

    async def get_room_agent_message_by_task_internal_id(
        self, internal_id: str
    ) -> RoomAgentMessage | None:
        """
        Get room agent message by stored A2A task internal id.
        """
        try:
            return await self.mongo.get_room_agent_message_by_task_internal_id(
                internal_id
            )
        except Exception as e:
            logger.error(
                f"Failed to get room agent message by task id {internal_id} from databases: {str(e)}"
            )
            return None

    async def update_room_agent_message_by_message_id(
        self, message_id: str, room_agent_message: RoomAgentMessage
    ) -> bool:
        """
        Update a room agent message by message_id
        """
        try:
            return await self.mongo.update_room_agent_message_by_message_id(
                message_id, room_agent_message
            )
        except Exception as e:
            logger.error(
                f"Failed to update room agent message {message_id} in databases: {str(e)}"
            )
            return False

    async def set_room_agent_message_task_internal_id(
        self, message_id: str, internal_id: str
    ) -> bool:
        """
        Set the task internal_id on a room agent message.
        """
        try:
            return await self.mongo.set_room_agent_message_task_internal_id(
                message_id, internal_id
            )
        except Exception as e:
            logger.error(
                "Failed to set task internal_id %s on message %s: %s",
                internal_id,
                message_id,
                str(e),
            )
            return False

    async def update_room_agent_message_with_new_message_content_by_message_id(
        self, message_id: str, message_content: MessageContent
    ) -> bool:
        """
        Update a room agent message by message_id with new message content
        """
        try:
            room_agent_message = await self.get_room_agent_message_by_message_id(
                message_id
            )
            if room_agent_message is None:
                return False
            room_agent_message.message_content = message_content

            return await self.mongo.update_room_agent_message_by_message_id(
                message_id, room_agent_message
            )
        except Exception as e:
            logger.error(
                f"Failed to update room agent message {message_id} in databases: {str(e)}"
            )
            return False

    async def delete_room_agent_message_by_message_id(self, message_id: str) -> bool:
        """
        Delete a room agent message by message_id
        """
        try:
            return await self.mongo.delete_room_agent_message_by_message_id(message_id)
        except Exception as e:
            logger.error(
                f"Failed to delete room agent message {message_id} from databases: {str(e)}"
            )
            return False

    # room memory management
    async def add_room_memory(self, room_memory: RoomMemory) -> bool:
        """
        Add a room memory to the database
        """
        if room_memory.memory_id == "":
            room_memory.memory_id = str(uuid.uuid4())
        try:
            await self.mongo.add_room_memory(room_memory)
            return True
        except Exception as e:
            logger.error(
                f"Failed to add room memory {room_memory.memory_id} to databases: {str(e)}"
            )
            return False

    async def get_room_memory_by_room_id(self, room_id: str) -> RoomMemory | None:
        """
        Get a room memory by room_id
        """
        try:
            return await self.mongo.get_room_memory_by_room_id(room_id)
        except Exception as e:
            logger.error(
                f"Failed to get room memory by room id {room_id} from databases: {str(e)}"
            )
            return None

    async def get_room_memory_by_memory_id(self, memory_id: str) -> RoomMemory | None:
        """
        Get a room memory by memory_id
        """
        try:
            return await self.mongo.get_room_memory_by_memory_id(memory_id)
        except Exception as e:
            logger.error(
                f"Failed to get room memory by memory id {memory_id} from databases: {str(e)}"
            )
            return None

    async def update_room_memory_by_memory_id(
        self, memory_id: str, room_memory: RoomMemory
    ) -> bool:
        """
        Update a room memory by memory_id
        """
        try:
            return await self.mongo.update_room_memory_by_memory_id(
                memory_id, room_memory
            )
        except Exception as e:
            logger.error(
                f"Failed to update room memory {memory_id} in databases: {str(e)}"
            )
            return False

    async def delete_room_memory_by_memory_id(self, memory_id: str) -> bool:
        """
        Delete a room memory by memory_id
        """
        try:
            return await self.mongo.delete_room_memory_by_memory_id(memory_id)
        except Exception as e:
            logger.error(
                f"Failed to delete room memory {memory_id} from databases: {str(e)}"
            )
            return False

    async def update_room_memory_by_room_id(
        self, room_id: str, room_memory: RoomMemory
    ) -> bool:
        """
        Update a room memory by room_id
        """
        try:
            return await self.mongo.update_room_memory_by_room_id(room_id, room_memory)
        except Exception as e:
            logger.error(
                f"Failed to update room memory {room_id} in databases: {str(e)}"
            )
            return False

    # Agent Group management
    async def add_agent_group(self, agent_group: AgentGroup) -> bool:
        """
        Add an agent group to the database
        """
        if not agent_group.group_id:
            agent_group.group_id = str(uuid.uuid4())
        try:
            await self.mongo.add_agent_group(agent_group)
            return True
        except Exception as e:
            logger.error(f"Failed to add agent group {agent_group.group_id}: {str(e)}")
            return False

    async def get_agent_groups_by_owner(self, owner_id: str) -> list[AgentGroup]:
        """
        Get all agent groups owned by a user
        """
        try:
            return await self.mongo.get_agent_groups_by_owner(owner_id)
        except Exception as e:
            logger.error(f"Failed to get agent groups for owner {owner_id}: {str(e)}")
            return []

    async def get_agent_group_by_id(self, group_id: str) -> AgentGroup | None:
        """
        Get an agent group by its ID
        """
        try:
            return await self.mongo.get_agent_group_by_id(group_id)
        except Exception as e:
            logger.error(f"Failed to get agent group {group_id}: {str(e)}")
            return None

    async def update_agent_group(self, group_id: str, updates: dict) -> bool:
        """
        Update an agent group by its ID
        """
        try:
            return await self.mongo.update_agent_group(group_id, updates)
        except Exception as e:
            logger.error(f"Failed to update agent group {group_id}: {str(e)}")
            return False

    async def delete_agent_group(self, group_id: str) -> bool:
        """
        Delete an agent group by its ID
        """
        try:
            return await self.mongo.delete_agent_group(group_id)
        except Exception as e:
            logger.error(f"Failed to delete agent group {group_id}: {str(e)}")
            return False


db_service = DatabaseService()
