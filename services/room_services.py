import re
from datetime import datetime
from uuid import uuid4
from config.settings import settings
from a2a.types import Message, Role, Task, TaskState, TaskStatus, TextPart

from common.utils.logger import get_logger
from models.memory import MemoryContent, RoomMemory
from models.request import (
    AgentCenterRequest,
    RoomCenterAgentMessageRequest,
    RoomCenterRoomMessageRequest,
    RoomCenterRoomSettingRequest,
    RoomCenterUserMessageRequest,
)
from models.response import (
    RoomCenterAgentMessageResponse,
    RoomCenterRoomMessageResponse,
    RoomCenterRoomSettingResponse,
    RoomCenterUserMessageResponse,
)
from models.room import (
    MessageContent,
    Room,
    RoomAgentMessage,
    RoomMessage,
    RoomUserMessage,
)
from services.a2a_service import A2AService
from services.agent_service import AgentService
from services.database_service import DatabaseService
from services.memory_service import RoomMemoryService
from services.openai_service import OpenAIService
from services.sse_services import sse_manager

logger = get_logger(__name__)


class RoomServices:
    def __init__(self):
        self.database_service = DatabaseService()
        self.agent_service = AgentService()
        self.openai_service = OpenAIService()
        self.a2a_service = A2AService()
        self.room_memory_service = RoomMemoryService()
        self.sse_manager = sse_manager

    # room setting management
    async def create_new_room(
        self, room_create_request: RoomCenterRoomSettingRequest
    ) -> RoomCenterRoomSettingResponse:
        if room_create_request.room_name is None:
            return RoomCenterRoomSettingResponse(
                room_id=None,
                success=False,
                error="Room name is required",
                status_code=400,
            )
        if room_create_request.room_owner_id is None:
            return RoomCenterRoomSettingResponse(
                room_id=None,
                success=False,
                error="Room owner id is required",
                status_code=400,
            )
        if room_create_request.room_owner_name is None:
            return RoomCenterRoomSettingResponse(
                room_id=None,
                success=False,
                error="Room owner name is required",
                status_code=400,
            )

        if room_create_request.room is not None:
            room = room_create_request.room
        else:
            room = Room(
                room_id=str(uuid4()),
                room_name=room_create_request.room_name,
                room_owner_id=room_create_request.room_owner_id,
                room_owner_name=room_create_request.room_owner_name,
                room_agent_set=room_create_request.room_agent_set or dict(),
                room_created_at=datetime.now(),
                extend_info=room_create_request.extend_info or None,
            )

        success = await self.database_service.add_room(room)
        if success:
            return RoomCenterRoomSettingResponse(
                room_id=room.room_id,
                room=room,
                success=True,
                error=None,
                status_code=200,
            )
        else:
            return RoomCenterRoomSettingResponse(
                room_id=None,
                room=None,
                success=False,
                error="Failed to create room",
                status_code=500,
            )

    async def inquiry_room_setting(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterRoomSettingResponse:
        if request.room_id is None:
            return RoomCenterRoomSettingResponse(
                room_id=None,
                room=None,
                success=False,
                error="Room id is required",
                status_code=400,
            )

        room_id = request.room_id
        room = await self.database_service.get_room_by_room_id(room_id)
        if room is None:
            return RoomCenterRoomSettingResponse(
                room_id=None,
                room=None,
                success=False,
                error="Room not found",
                status_code=404,
            )
        else:
            return RoomCenterRoomSettingResponse(
                room_id=room.room_id,
                room=room,
                success=True,
                error=None,
                status_code=200,
            )

    async def inquiry_rooms_by_room_owner_id(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterRoomSettingResponse:
        if request.room_owner_id is None:
            return RoomCenterRoomSettingResponse(
                room_list=None,
                success=False,
                error="Room owner id is required",
                status_code=400,
            )

        room_owner_id = request.room_owner_id
        rooms = await self.database_service.get_rooms_by_room_owner_id(room_owner_id)
        return RoomCenterRoomSettingResponse(
            room_list=rooms, success=True, error=None, status_code=200
        )

    async def update_room_agent_set(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterRoomSettingResponse:
        if request.room_id is None:
            return RoomCenterRoomSettingResponse(
                room_id=None,
                room=None,
                success=False,
                error="Room id is required",
                status_code=400,
            )

        room_id = request.room_id
        room = await self.database_service.get_room_by_room_id(room_id)
        if room is None:
            return RoomCenterRoomSettingResponse(
                room_id=None,
                room=None,
                success=False,
                error="Room not found",
                status_code=404,
            )

        if request.room_agent_set is None:
            return RoomCenterRoomSettingResponse(
                room_id=None,
                room=None,
                success=False,
                error="Room agent set is required",
                status_code=400,
            )

        room.room_agent_set = request.room_agent_set
        success = await self.database_service.update_room_by_room_id(room_id, room)
        if success:
            return RoomCenterRoomSettingResponse(
                room_id=room_id, room=room, success=True, error=None, status_code=200
            )
        else:
            return RoomCenterRoomSettingResponse(
                room_id=room_id,
                room=None,
                success=False,
                error="Failed to update room agent set",
                status_code=500,
            )

    async def update_room_name(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterRoomSettingResponse:
        if request.room_id is None:
            return RoomCenterRoomSettingResponse(
                room_id=None,
                room=None,
                success=False,
                error="Room id is required",
                status_code=400,
            )

        room_id = request.room_id
        room = await self.database_service.get_room_by_room_id(room_id)
        if room is None:
            return RoomCenterRoomSettingResponse(
                room_id=None,
                room=None,
                success=False,
                error="Room not found",
                status_code=404,
            )

        if request.room_name is None:
            return RoomCenterRoomSettingResponse(
                room_id=None,
                room=None,
                success=False,
                error="Room name is required",
                status_code=400,
            )

        room.room_name = request.room_name
        success = await self.database_service.update_room_by_room_id(room_id, room)
        if success:
            return RoomCenterRoomSettingResponse(
                room_id=room_id, room=room, success=True, error=None, status_code=200
            )
        else:
            return RoomCenterRoomSettingResponse(
                room_id=room_id,
                room=None,
                success=False,
                error="Failed to update room name",
                status_code=500,
            )

    async def update_room_extend_info(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterRoomSettingResponse:
        if request.room_id is None:
            return RoomCenterRoomSettingResponse(
                room_id=None,
                room=None,
                success=False,
                error="Room id is required",
                status_code=400,
            )

        room_id = request.room_id
        room = await self.database_service.get_room_by_room_id(room_id)
        if room is None:
            return RoomCenterRoomSettingResponse(
                room_id=None,
                room=None,
                success=False,
                error="Room not found",
                status_code=404,
            )
        
        if request.extend_info is None:
            return RoomCenterRoomSettingResponse(
                room_id=None,
                room=None,
                success=False,
                error="Extend info is required",
                status_code=400,
            )
        
        room.extend_info = request.extend_info
        success = await self.database_service.update_room_by_room_id(room_id, room)
        if success:
            return RoomCenterRoomSettingResponse(
                room_id=room_id, room=room, success=True, error=None, status_code=200
            )
        else:
            return RoomCenterRoomSettingResponse(
                room_id=room_id,
                room=None,
                success=False,
                error="Failed to update room extend info",
                status_code=500,
            )

    async def delete_room_by_room_id(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterRoomSettingResponse:
        if request.room_id is None:
            return RoomCenterRoomSettingResponse(
                room_id=None,
                room=None,
                success=False,
                error="Room id is required",
                status_code=400,
            )

        room_id = request.room_id
        success = await self.database_service.delete_room_by_room_id(room_id)
        if success:
            return RoomCenterRoomSettingResponse(
                room_id=room_id, room=None, success=True, error=None, status_code=200
            )
        else:
            return RoomCenterRoomSettingResponse(
                room_id=room_id,
                room=None,
                success=False,
                error="Failed to delete room",
                status_code=500,
            )

    # room user message management
    def parse_agent_mentions(
        self, message_text: str, room_agent_set: dict
    ) -> list[dict]:
        """
        Parse @agent mentions in format "<@agent-id|agentname>"
        
        Args:
            message_text: User input text with format "<@agent-id|agentname>"
            room_agent_set: Agent set in the room {agent_id: agent_name}
        
        Returns:
            list[dict]: Parsed mentions [{"agent_id": "xxx", "agent_name": "yyy", "mention_text": "<@xxx|yyy>"}]
        """
        mentions = []
        
        #pattern: <@agent_id|agent_name>
        slack_pattern = r"<@([^|]+)\|([^>]+)>"
        
        for match in re.finditer(slack_pattern, message_text):
            agent_id = match.group(1).strip()
            agent_name = match.group(2).strip()
            position = match.start()
            
            # Check if agent exists in room by agent_id
            if agent_id in room_agent_set:
                # Agent found in room
                room_agent_name = room_agent_set[agent_id]
                mentions.append({
                    "agent_id": agent_id,
                    "agent_name": room_agent_name,  # Use the name from room_agent_set
                    "mention_text": match.group(0),
                    "position": position,
                })
            else:
                # Agent not found in room, but still parse it
                mentions.append({
                    "agent_id": agent_id,
                    "agent_name": agent_name,  # Use the name from the mention
                    "mention_text": match.group(0),
                    "position": position,
                    "warning": "Agent not in current room",
                })
        
        # Sort by position to maintain order
        mentions.sort(key=lambda x: x["position"])
        return mentions

    def extract_agent_message_content(
        self,
        message_text: str,
        target_agent_id: str,
        target_agent_name: str,
        all_mentions: list,
    ) -> str:
        """
        Extract message content relevant to a specific agent
        Remove @mentions and return clean task content
        
        Args:
            message_text: Original message text
            target_agent_id: Target agent ID
            target_agent_name: Target agent name
            all_mentions: All parsed mentions from the message
        
        Returns:
            str: Clean message content relevant to the target agent
        """
        # Find all mentions for this specific agent
        agent_mentions = [m for m in all_mentions if m["agent_id"] == target_agent_id]
        
        if not agent_mentions:
            # No mentions found, remove all mentions and return clean content
            processed_text = message_text
            for mention in all_mentions:
                processed_text = processed_text.replace(mention["mention_text"], "")
            return re.sub(r'\s+', ' ', processed_text).strip()
        
        # Strategy 1: Extract text around each mention of this agent
        relevant_parts = []
        
        for mention in agent_mentions:
            mention_pos = mention["position"]
            mention_text = mention["mention_text"]
            
            # Find the sentence or context around this mention
            start_pos = mention_pos
            end_pos = mention_pos + len(mention_text)
            
            # Extend backwards to find sentence start
            while start_pos > 0 and message_text[start_pos - 1] not in ".!?\n":
                start_pos -= 1
            
            # Extend forwards to find sentence end
            while end_pos < len(message_text) and message_text[end_pos] not in ".!?\n":
                end_pos += 1
            
            # Include the sentence ending punctuation
            if end_pos < len(message_text) and message_text[end_pos] in ".!?\n":
                end_pos += 1
            
            # Extract the relevant sentence/context
            context = message_text[start_pos:end_pos].strip()
            
            # Remove ALL mentions from this context, not just replace with @agent_name
            context_clean = context
            for m in all_mentions:
                context_clean = context_clean.replace(m["mention_text"], "")
            
            # Clean up whitespace
            context_clean = re.sub(r'\s+', ' ', context_clean).strip()
            
            if context_clean and context_clean not in relevant_parts:
                relevant_parts.append(context_clean)
        
        # Join all relevant parts
        if relevant_parts:
            return " ".join(relevant_parts)
        else:
            # Fallback: return original message with all mentions removed
            processed_text = message_text
            for mention in all_mentions:
                processed_text = processed_text.replace(mention["mention_text"], "")
            return re.sub(r'\s+', ' ', processed_text).strip()

    def group_mentions_by_context(self, message_text: str, mentions: list) -> dict:
        """
        Group mentions by their shared context/sentence and detect consecutive mentions
        
        Args:
            message_text: Original message text
            mentions: List of parsed mentions
        
        Returns:
            dict: {context_text: {"mentions": [mentions], "is_consecutive": bool}}
        """
        context_groups = {}
        
        for mention in mentions:
            mention_pos = mention["position"]
            mention_text = mention["mention_text"]
            
            # Find sentence boundaries around this mention
            start_pos = mention_pos
            end_pos = mention_pos + len(mention_text)
            
            # Extend backwards to find sentence start
            while start_pos > 0 and message_text[start_pos - 1] not in ".!?\n":
                start_pos -= 1
            
            # Extend forwards to find sentence end
            while end_pos < len(message_text) and message_text[end_pos] not in ".!?\n":
                end_pos += 1
            
            # Include the sentence ending punctuation
            if end_pos < len(message_text) and message_text[end_pos] in ".!?\n":
                end_pos += 1
            
            # Extract the sentence context
            context = message_text[start_pos:end_pos].strip()
            
            # Group mentions by context
            if context not in context_groups:
                context_groups[context] = {"mentions": [], "is_consecutive": False}
            context_groups[context]["mentions"].append(mention)
        
        # Detect consecutive mentions within each context
        for context, group_info in context_groups.items():
            mentions_in_context = group_info["mentions"]
            if len(mentions_in_context) > 1:
                # Check if mentions are consecutive (close together with minimal text between)
                mentions_in_context.sort(key=lambda x: x["position"])
                
                is_consecutive = True
                for i in range(len(mentions_in_context) - 1):
                    current_mention = mentions_in_context[i]
                    next_mention = mentions_in_context[i + 1]
                    
                    # Get text between mentions
                    between_start = current_mention["position"] + len(current_mention["mention_text"])
                    between_end = next_mention["position"]
                    between_text = message_text[between_start:between_end].strip()
                    
                    # If there's significant text between mentions (more than just spaces/commas), 
                    # they're not consecutive
                    if len(between_text) > 10 or any(word in between_text.lower() for word in 
                        ['and', 'then', 'also', 'but', 'however', 'meanwhile']):
                        is_consecutive = False
                        break
                
                group_info["is_consecutive"] = is_consecutive
        
        return context_groups

    def create_shared_message_content(
        self, context_text: str, mentions_in_context: list
    ) -> str:
        """
        Create message content for multiple agents sharing the same context
        Remove all @mentions and return clean task content
        
        Args:
            context_text: The shared context/sentence
            mentions_in_context: List of mentions in this context
        
        Returns:
            str: Clean message content without @mentions
        """
        processed_text = context_text
        
        # Remove all mentions (both simple @agent and Slack-style <@id|name>)
        for mention in mentions_in_context:
            mention_text = mention["mention_text"]
            processed_text = processed_text.replace(mention_text, "")
        
        # Clean up extra spaces and normalize whitespace
        processed_text = re.sub(r'\s+', ' ', processed_text).strip()
        
        return processed_text

    def create_task_for_agent(
        self,
        user_message: RoomUserMessage,
        agent_id: str,
        agent_name: str,
        all_mentions: list,
    ) -> Task:
        """
        Create a2a Task for specific agent with relevant message content only

        Args:
            user_message: User message
            agent_id: Target agent ID
            agent_name: Target agent name
            all_mentions: All parsed mentions from the message

        Returns:
            Task: a2a protocol Task object
        """
        # Extract relevant message content for this agent
        original_text = user_message.message_content.message_text
        agent_relevant_text = self.extract_agent_message_content(
            original_text, agent_id, agent_name, all_mentions
        )

        # Create Message
        message = Message(
            message_id=user_message.message_id,
            role=Role.user,
            parts=[TextPart(text=agent_relevant_text)],  # Use filtered content
            context_id=user_message.room_id,
            metadata={},
        )

        # Create Task status
        task_status = TaskStatus(
            state=TaskState.submitted, timestamp=datetime.now().isoformat()
        )

        # Create Task
        task = Task(
            id=str(uuid4()),
            context_id=user_message.room_id,
            status=task_status,
            history=[message],
        )

        return task

    async def create_task_for_agents_group(
        self, user_message: RoomUserMessage, mentions_group: list, shared_content: str
    ) -> list:
        """
        Create a2a Tasks for a group of agents sharing the same message content

        Args:
            user_message: User message
            mentions_group: List of mentions sharing the same context
            shared_content: Shared message content

        Returns:
            list: List of Task objects for each agent
        """
        tasks = []

        for mention in mentions_group:
            agent_id = mention["agent_id"]
            agent_name = mention["agent_name"]

            # Create Message with shared content
            message = Message(
                message_id=f"{user_message.message_id}_{agent_id}",  # Unique ID per agent
                role=Role.user,
                parts=[TextPart(text=shared_content)],
                context_id=user_message.room_id,
                metadata={},
            )

            # Create Task status
            task_status = TaskStatus(
                state=TaskState.submitted, timestamp=datetime.now().isoformat()
            )

            # Create Task
            task = Task(
                id=str(uuid4()),
                context_id=user_message.room_id,
                status=task_status,
                history=[message],
            )

            tasks.append({"task": task, "agent_id": agent_id, "agent_name": agent_name})

        return tasks

    async def create_and_parse_user_message(
        self, request: RoomCenterUserMessageRequest
    ) -> RoomCenterUserMessageResponse:
        """Send user message and handle @agent parsing with context grouping"""

        if request.room_id is None:
            return RoomCenterUserMessageResponse(
                message_id=None,
                message=None,
                success=False,
                error="Room id is required",
                status_code=400,
            )

        room_id = request.room_id
        message = request.message
        if message is None:
            return RoomCenterUserMessageResponse(
                message_id=None,
                message=None,
                success=False,
                error="Message is required",
                status_code=400,
            )

        # 1. Save user message
        add_message_success = await self.database_service.add_room_user_message(message)
        if not add_message_success:
            return RoomCenterUserMessageResponse(
                message_id=None,
                message=None,
                success=False,
                error="Failed to add message",
                status_code=500,
            )

        logger.info(f"RoomServices: Sending processing status to room {room_id} for message {message.message_id}")
        await sse_manager.send_processing_status(room_id, "processing", message.message_id)

        room_memory = await self.database_service.get_room_memory_by_room_id(room_id)
        if not room_memory:
            room_memory = RoomMemory(
                room_id=room_id,
                memory_id=str(uuid4()),
                memory_content=MemoryContent(
                    memory_text=message.message_content.message_text
                ),
            )
            add_room_memory_success = await self.database_service.add_room_memory(
                room_memory
            )
            if not add_room_memory_success:
                return RoomCenterUserMessageResponse(
                    message_id=message.message_id,
                    message=message,
                    success=True,
                    error="Failed to add room memory",
                    status_code=500,
                )

        # Safely and clearly update room memory content with labeled delimiter
        prev_text = (
            (room_memory.memory_content.memory_text or "")
            if room_memory and room_memory.memory_content
            else ""
        )
        user_text = ""
        if message and message.message_content and message.message_content.message_text:
            user_text = message.message_content.message_text
        # Add clear labels and separation to avoid blending
        addition = f"\n\n[User Message at {datetime.now().isoformat()}]\n{user_text}\n"
        new_room_memory_content_text = f"{prev_text}{addition}".strip()
        room_memory_response = (
            await self.database_service.update_room_memory_by_room_id(
                room_id,
                RoomMemory(
                    room_id=room_id,
                    memory_id=room_memory.memory_id,
                    memory_content=MemoryContent(
                        memory_text=new_room_memory_content_text
                    ),
                ),
            )
        )

        if not room_memory_response:
            logger.error("RoomServices: Failed to update room memory")

        # 2. Get room information
        room = await self.database_service.get_room_by_room_id(room_id)
        if not room:
            return RoomCenterUserMessageResponse(
                message_id=message.message_id,
                message=message,
                success=True,
                error="Room not found, but message saved",
                status_code=200,
            )

        # 3. Parse @agent mentions
        message_text = message.message_content.message_text
        mentions = self.parse_agent_mentions(message_text, room.room_agent_set)

        # 4. Group mentions by context and detect consecutive patterns
        context_groups = self.group_mentions_by_context(message_text, mentions)

        # 5. Create tasks with appropriate dependency chains
        created_agent_messages = []
        for context_text, group_info in context_groups.items():
            mentions_in_context = group_info["mentions"]
            is_consecutive = group_info["is_consecutive"]
            
            try:
                # Create shared message content
                shared_content = self.create_shared_message_content(
                    context_text, mentions_in_context
                )
                
                # Create tasks for all agents in this context
                tasks_group = await self.create_task_for_agents_group(
                    message, mentions_in_context, shared_content
                )
                
                if is_consecutive:
                    # Consecutive mentions: create dependency chain
                    previous_message_id = message.message_id  # Start with user message ID
                    
                    for i, task_info in enumerate(tasks_group):
                        agent_message = RoomAgentMessage(
                            room_id=room_id,
                            message_id=str(uuid4()),
                            related_message_id=previous_message_id,  # Chain dependency
                            agent_id=task_info["agent_id"],
                            message_content=MessageContent(
                                message_task=task_info["task"]
                            ),
                            message_created_at=datetime.now(),
                        )
                        
                        # Save to database
                        agent_message_success = (
                            await self.database_service.add_room_agent_message(
                                agent_message
                            )
                        )
                        if agent_message_success:
                            created_agent_messages.append(agent_message)
                            # Update previous_message_id for next agent in chain
                            previous_message_id = agent_message.message_id
                else:
                    # Non-consecutive mentions: all relate directly to user message
                    for task_info in tasks_group:
                        agent_message = RoomAgentMessage(
                            room_id=room_id,
                            message_id=str(uuid4()),
                            related_message_id=message.message_id,  # All relate to user message
                            agent_id=task_info["agent_id"],
                            message_content=MessageContent(
                                message_task=task_info["task"]
                            ),
                            message_created_at=datetime.now(),
                        )
                        
                        # Save to database
                        agent_message_success = (
                            await self.database_service.add_room_agent_message(
                                agent_message
                            )
                        )
                        if agent_message_success:
                            created_agent_messages.append(agent_message)
        
            except Exception as e:
                print(f"Error creating agent messages for context '{context_text}': {e}")
        
        return RoomCenterUserMessageResponse(
            message_id=message.message_id,
            message=message,
            success=True,
            error=None,
            status_code=200,
        )
    
    
    async def create_and_parse_user_message_with_debate(
        self, request: RoomCenterUserMessageRequest
    ) -> RoomCenterUserMessageResponse:
        """Send user message and handle @agent parsing with context grouping in debate mode"""

        if request.room_id is None:
            return RoomCenterUserMessageResponse(
                message_id=None,
                message=None,
                success=False,
                error="Room id is required",
                status_code=400,
            )

        room_id = request.room_id
        message = request.message
        if message is None:
            return RoomCenterUserMessageResponse(
                message_id=None,
                message=None,
                success=False,
                error="Message is required",
                status_code=400,
            )

        # 1. Save user message
        add_message_success = await self.database_service.add_room_user_message(message)
        if not add_message_success:
            return RoomCenterUserMessageResponse(
                message_id=None,
                message=None,
                success=False,
                error="Failed to add message",
                status_code=500,
            )
        
        logger.info(f"RoomServices: Sending processing status to room {room_id} for message {message.message_id}")
        await sse_manager.send_processing_status(room_id, "processing", message.message_id)

        room_memory = await self.database_service.get_room_memory_by_room_id(room_id)
        if not room_memory:
            room_memory = RoomMemory(
                room_id=room_id,
                memory_id=str(uuid4()),
                memory_content=MemoryContent(
                    memory_text=message.message_content.message_text
                ),
            )
            add_room_memory_success = await self.database_service.add_room_memory(
                room_memory
            )
            if not add_room_memory_success:
                return RoomCenterUserMessageResponse(
                    message_id=message.message_id,
                    message=message,
                    success=True,
                    error="Failed to add room memory",
                    status_code=500,
                )

        # Safely and clearly update room memory content with labeled delimiter
        prev_text = (
            (room_memory.memory_content.memory_text or "")
            if room_memory and room_memory.memory_content
            else ""
        )
        user_text = ""
        if message and message.message_content and message.message_content.message_text:
            user_text = message.message_content.message_text
        # Add clear labels and separation to avoid blending
        addition = f"\n\n[User Message at {datetime.now().isoformat()}]\n{user_text}\n"
        new_room_memory_content_text = f"{prev_text}{addition}".strip()
        room_memory_response = (
            await self.database_service.update_room_memory_by_room_id(
                room_id,
                RoomMemory(
                    room_id=room_id,
                    memory_id=room_memory.memory_id,
                    memory_content=MemoryContent(
                        memory_text=new_room_memory_content_text
                    ),
                ),
            )
        )

        if not room_memory_response:
            logger.error("RoomServices: Failed to update room memory")

        # 2. Get room information
        room = await self.database_service.get_room_by_room_id(room_id)
        if not room:
            return RoomCenterUserMessageResponse(
                message_id=message.message_id,
                message=message,
                success=True,
                error="Room not found, but message saved",
                status_code=200,
            )

        # 3. Parse @agent mentions
        message_text = message.message_content.message_text
        mentions = self.parse_agent_mentions(message_text, room.room_agent_set)

        # 4. Group mentions by context and detect consecutive patterns
        context_groups = self.group_mentions_by_context(message_text, mentions)

        # 5. Create tasks with appropriate dependency chains
        created_agent_messages = []
        for context_text, group_info in context_groups.items():
            mentions_in_context = group_info["mentions"]
            is_consecutive = group_info["is_consecutive"]
            
            try:
                # Create shared message content
                shared_content = self.create_shared_message_content(
                    context_text, mentions_in_context
                )
                
                # Create tasks for all agents in this context
                tasks_group = await self.create_task_for_agents_group(
                    message, mentions_in_context, shared_content
                )
                
                if is_consecutive:
                    # Consecutive mentions: create dependency chain
                    iteration_rounds = settings.debate_rounds # todo: can be as parameter


                    previous_message_id = message.message_id  # Start with user message ID
                    
                    for round_num in range(1, iteration_rounds + 1):
                        for i, task_info in enumerate(tasks_group):
                            agent_message = RoomAgentMessage(
                                room_id=room_id,
                                message_id=str(uuid4()),
                                related_message_id=previous_message_id,  # Chain dependency
                                agent_id=task_info["agent_id"],
                                message_content=MessageContent(
                                    message_task=task_info["task"]  # use original task content, not add iteration mark
                                ),
                                message_created_at=datetime.now(),
                                extend_info={
                                    "iteration_round": round_num,
                                    "total_rounds": iteration_rounds,
                                    "agent_sequence": i + 1,
                                    "total_agents": len(tasks_group)
                                }
                            )
                            
                            # Save to database
                            agent_message_success = (
                                await self.database_service.add_room_agent_message(
                                    agent_message
                                )
                            )
                            if agent_message_success:
                                created_agent_messages.append(agent_message)
                                # Update previous_message_id for next agent in chain
                                previous_message_id = agent_message.message_id
                else:
                    # Non-consecutive mentions: all relate directly to user message
                    for task_info in tasks_group:
                        agent_message = RoomAgentMessage(
                            room_id=room_id,
                            message_id=str(uuid4()),
                            related_message_id=message.message_id,  # All relate to user message
                            agent_id=task_info["agent_id"],
                            message_content=MessageContent(
                                message_task=task_info["task"]
                            ),
                            message_created_at=datetime.now(),
                        )
                        
                        # Save to database
                        agent_message_success = (
                            await self.database_service.add_room_agent_message(
                                agent_message
                            )
                        )
                        if agent_message_success:
                            created_agent_messages.append(agent_message)
        
            except Exception as e:
                print(f"Error creating agent messages for context '{context_text}': {e}")
        
        return RoomCenterUserMessageResponse(
            message_id=message.message_id,
            message=message,
            success=True,
            error=None,
            status_code=200,
        )

    async def process_agent_message(
        self, request: RoomCenterAgentMessageRequest, room_memory_content_text: str
    ) -> RoomCenterAgentMessageResponse:
        message = request.message
        if message is None:
            return RoomCenterAgentMessageResponse(
                message_id=None,
                message=None,
                success=False,
                error="Agent Message is required",
                status_code=400,
            )

        agent_id = message.agent_id
        query_agent_url_response = await self.agent_service.get_agent_url_by_agent_id(
            AgentCenterRequest(agent_id=agent_id)
        )
        if query_agent_url_response.agent_url is None:
            return RoomCenterAgentMessageResponse(
                message_id=None,
                message=None,
                success=False,
                error="Agent url is not found",
                status_code=400,
            )

        agent_url = query_agent_url_response.agent_url

        agent_msg = request.message
        if (
            agent_msg.message_content
            and agent_msg.message_content.message_task
            and agent_msg.message_content.message_task.history
        ):
            agent_message = agent_msg.message_content.message_task.history[0]
        else:
            return RoomCenterAgentMessageResponse(
                message_id=None,
                message=None,
                success=False,
                error="No task content found",
                status_code=400,
            )

        # Temporary: Get all messages for context
        # TODO: Create a more robust context and memory solution.
        latest_messages_text = ""
        try:
            room_messages_response = await self.inquiry_room_messages_by_room_id(
                RoomCenterRoomMessageRequest(room_id=message.room_id)
            )

            if room_messages_response.success and room_messages_response.message_list:
                recent_messages = room_messages_response.message_list

                latest_messages_parts = []
                for msg in recent_messages:
                    if msg.message_type == "user":
                        user_id = msg.user_id or "Unknown User"
                        text = msg.message_content.message_text or ""
                        latest_messages_parts.append(f"User ({user_id}): {text}")
                    elif msg.message_type == "agent":
                        agent_id = msg.agent_id or "Unknown Agent"
                        text = msg.message_content.message_text or ""
                        latest_messages_parts.append(f"Agent ({agent_id}): {text}")

                if latest_messages_parts:
                    latest_messages_text = "\n".join(latest_messages_parts)
        except Exception:
            # If we can't get recent messages, continue without them
            latest_messages_text = ""

        # Inject context with clear delimiter and guard structure access
        try:
            if agent_message and agent_message.parts and len(agent_message.parts) > 0:
                original_text = agent_message.parts[0].root.text or ""

                # Build enhanced context with room memory and latest messages
                context_parts = []
                if room_memory_content_text.strip():
                    context_parts.append(f"Context:\n{room_memory_content_text}")

                if latest_messages_text:
                    context_parts.append(f"Latest messages:\n{latest_messages_text}")

                if context_parts:
                    injected = (
                        f"{chr(10).join(context_parts)}\n\nUser:\n{original_text}"
                    )
                else:
                    injected = f"User:\n{original_text}"

                agent_message.parts[0].root.text = injected
        except Exception:
            # Leave message as-is if structure is unexpected
            pass
        
        # Return the prepared message without sending
        # OrchestrationCenter will handle the actual sending with streaming support
        return RoomCenterAgentMessageResponse(
            message_id=message.message_id,
            message=message,
            a2a_message=agent_message,  # Return the prepared A2A message
            success=True,
            error=None,
            status_code=200,
        )

    async def update_agent_message_by_message_id(
        self, request: RoomCenterAgentMessageRequest
    ) -> RoomCenterAgentMessageResponse:
        if request.message_id is None:
            return RoomCenterAgentMessageResponse(
                message=None,
                success=False,
                error="Message id is required",
                status_code=400,
            )

        message_id = request.message_id
        message = request.message
        if message is None:
            return RoomCenterAgentMessageResponse(
                message=None,
                success=False,
                error="Message is required",
                status_code=400,
            )

        update_message_success = (
            await self.database_service.update_room_agent_message_by_message_id(
                message_id, message
            )
        )
        if update_message_success:
            return RoomCenterAgentMessageResponse(
                message=message, success=True, error=None, status_code=200
            )
        else:
            return RoomCenterAgentMessageResponse(
                message=None,
                success=False,
                error="Failed to update message",
                status_code=500,
            )

    async def inquiry_user_messages_by_room_id(
        self, request: RoomCenterUserMessageRequest
    ) -> RoomCenterUserMessageResponse:
        if request.room_id is None:
            return RoomCenterUserMessageResponse(
                message_list=None,
                success=False,
                error="Room id is required",
                status_code=400,
            )

        room_id = request.room_id
        messages = await self.database_service.get_room_user_messages_by_room_id(
            room_id
        )
        return RoomCenterUserMessageResponse(
            message_list=messages, success=True, error=None, status_code=200
        )

    async def inquiry_agent_messages_by_room_id(
        self, request: RoomCenterAgentMessageRequest
    ) -> RoomCenterAgentMessageResponse:
        if request.room_id is None:
            return RoomCenterAgentMessageResponse(
                message_list=None,
                success=False,
                error="Room id is required",
                status_code=400,
            )

        room_id = request.room_id
        messages = await self.database_service.get_room_agent_messages_by_room_id(
            room_id
        )
        return RoomCenterAgentMessageResponse(
            message_list=messages, success=True, error=None, status_code=200
        )

    async def inquiry_agent_message_by_message_id(
        self, request: RoomCenterAgentMessageRequest
    ) -> RoomCenterAgentMessageResponse:
        if request.message_id is None:
            return RoomCenterAgentMessageResponse(
                message=None,
                success=False,
                error="Message id is required",
                status_code=400,
            )

        message_id = request.message_id
        message = await self.database_service.get_room_agent_message_by_message_id(
            message_id
        )
        return RoomCenterAgentMessageResponse(
            message=message, success=True, error=None, status_code=200
        )

    async def inquiry_user_message_by_message_id(
        self, request: RoomCenterUserMessageRequest
    ) -> RoomCenterUserMessageResponse:
        if request.message_id is None:
            return RoomCenterUserMessageResponse(
                message=None,
                success=False,
                error="Message id is required",
                status_code=400,
            )

        message_id = request.message_id
        message = await self.database_service.get_room_user_message_by_message_id(
            message_id
        )
        return RoomCenterUserMessageResponse(
            message=message, success=True, error=None, status_code=200
        )

    async def inquiry_agent_messages_by_related_message_id(
        self, request: RoomCenterAgentMessageRequest
    ) -> RoomCenterAgentMessageResponse:
        if request.related_message_id is None:
            return RoomCenterAgentMessageResponse(
                message_list=None,
                success=False,
                error="Related message id is required",
                status_code=400,
            )

        related_message_id = request.related_message_id
        messages = (
            await self.database_service.get_room_agent_messages_by_related_message_id(
                related_message_id
            )
        )
        return RoomCenterAgentMessageResponse(
            message_list=messages, success=True, error=None, status_code=200
        )

    async def inquiry_room_messages_by_room_id(
        self, request: RoomCenterRoomMessageRequest
    ) -> RoomCenterRoomMessageResponse:
        """
        Retrieve all messages in a room, including user messages and agent messages.
        For user messages: return message_text from message_content
        For agent messages: return text part from the latest message with role "agent" in task.history
        Sort by creation time and return
        """
        if request.room_id is None:
            return RoomCenterRoomMessageResponse(
                message_list=None,
                success=False,
                error="Room id is required",
                status_code=400,
            )

        try:
            room_id = request.room_id

            # Get user messages
            user_message_request = RoomCenterUserMessageRequest(room_id=room_id)
            user_messages_response = await self.inquiry_user_messages_by_room_id(
                user_message_request
            )

            # Get agent messages
            agent_message_request = RoomCenterAgentMessageRequest(room_id=room_id)
            agent_messages_response = await self.inquiry_agent_messages_by_room_id(
                agent_message_request
            )

            combined_messages = []

            # Process user messages
            if user_messages_response.success and user_messages_response.message_list:
                for user_msg in user_messages_response.message_list:
                    room_message = RoomMessage(
                        room_id=user_msg.room_id,
                        message_id=user_msg.message_id,
                        message_type="user",
                        message_content=user_msg.message_content,
                        message_created_at=user_msg.message_created_at,
                        user_id=user_msg.user_id,
                    )
                    combined_messages.append(room_message)

            # Process agent messages
            if agent_messages_response.success and agent_messages_response.message_list:
                for agent_msg in agent_messages_response.message_list:
                    # Extract latest agent message from task.history
                    agent_content = ""
                    if (
                        agent_msg.message_content
                        and agent_msg.message_content.message_task
                        and agent_msg.message_content.message_task.history
                    ):
                        # Find the latest message with role "agent"
                        agent_messages = [
                            msg
                            for msg in agent_msg.message_content.message_task.history
                            if msg.role == Role.agent
                        ]

                        if agent_messages:
                            # Get the latest agent message
                            latest_agent_message = agent_messages[-1]

                            # Extract text parts from ALL message parts, not just the first one
                            text_parts = []
                            if hasattr(latest_agent_message, 'parts') and latest_agent_message.parts:
                                for part in latest_agent_message.parts:
                                    if hasattr(part, 'root') and hasattr(part.root, 'text'):
                                        text_parts.append(part.root.text)
                            
                            # Combine all text parts
                            agent_content = " ".join(text_parts) if text_parts else ""

                    room_message = RoomMessage(
                        room_id=agent_msg.room_id,
                        message_id=agent_msg.message_id,
                        message_type="agent",
                        message_content=MessageContent(message_text=agent_content),
                        message_created_at=agent_msg.message_created_at,
                        agent_id=agent_msg.agent_id,
                    )
                    combined_messages.append(room_message)

            # Sort by creation time
            combined_messages.sort(key=lambda x: x.message_created_at)

            return RoomCenterRoomMessageResponse(
                room_id=room_id,
                message_list=combined_messages,
                success=True,
                error=None,
                status_code=200,
            )

        except Exception as e:
            return RoomCenterRoomMessageResponse(
                room_id=request.room_id,
                message_list=None,
                success=False,
                error=str(e),
                status_code=500,
            )
