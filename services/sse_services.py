import asyncio
import json
from datetime import datetime
from typing import Dict, Set, Optional, Any
from uuid import uuid4
from common.utils.logger import get_logger


logger = get_logger(__name__)

class SSEConnection:
    def __init__(self, room_id: str):
        self.room_id = room_id
        self.connection_id = str(uuid4())
        self.queue: asyncio.Queue = asyncio.Queue()
        self.connected_at = datetime.now()
        self.is_active = True  
    
    async def send_message(self, message_type: str, data: Any):
        """send message to connection"""
        if not self.is_active:
            return False
        
        try:
            message = {
                "type": message_type,
                "timestamp": datetime.now().isoformat(),
                "room_id": self.room_id,
                "data": data
            }
            await self.queue.put(json.dumps(message))
            return True
        except Exception as e:
            logger.error(f"Failed to send message to connection {self.connection_id}: {e}")
            self.is_active = False
            return False

    async def get_message(self, timeout: float = 30.0) -> Optional[str]:
        """get message from queue"""
        try:
            message = await asyncio.wait_for(self.queue.get(), timeout=timeout)
            return message
        except asyncio.TimeoutError:
            # send heartbeat
            heartbeat = {
                "type": "heartbeat",
                "timestamp": datetime.now().isoformat(),
                "room_id": self.room_id
            }
            await self.queue.put(json.dumps(heartbeat))
            return json.dumps(heartbeat)

    def close(self):
        """close the connection"""
        self.is_active = False

class SSEManager:
    def __init__(self):
        # room_id -> {connection_id: connection}
        self.room_connections: Dict[str, Dict[str, SSEConnection]] = {}
        self.lock = asyncio.Lock()
    
    async def add_connection(self, room_id: str) -> SSEConnection:
        """add connection"""
        async with self.lock:
            if room_id not in self.room_connections:
                self.room_connections[room_id] = {}
            
            connection = SSEConnection(room_id)
            self.room_connections[room_id][connection.connection_id] = connection
            
            logger.info(f"SSE connection {connection.connection_id} added to room {room_id}")
            return connection

    async def remove_connection(self, room_id: str, connection_id: str):
        """remove connection"""
        async with self.lock:
            if room_id in self.room_connections and connection_id in self.room_connections[room_id]:
                connection = self.room_connections[room_id][connection_id]
                connection.close()
                del self.room_connections[room_id][connection_id]
                
                if not self.room_connections[room_id]:
                    del self.room_connections[room_id]
                
                logger.info(f"SSE connection {connection_id} removed from room {room_id}")

    async def broadcast_to_room(self, room_id: str, message_type: str, data: Any):
        """broadcast message to room"""
        async with self.lock:
            if room_id not in self.room_connections:
                logger.debug(f"No connections for room {room_id}")
                return

            disconnected_connections = []
            
            for connection_id, connection in self.room_connections[room_id].items():
                success = await connection.send_message(message_type, data)
                if not success:
                    disconnected_connections.append(connection_id)

            # clean up disconnected connections
            for connection_id in disconnected_connections:
                if connection_id in self.room_connections[room_id]:
                    del self.room_connections[room_id][connection_id]

            active_connections = len(self.room_connections[room_id])
            logger.info(f"Broadcasted {message_type} to {active_connections} connections in room {room_id}")

    async def send_user_message(self, room_id: str, message_id: str, user_id: str, content: str):
        """send user message"""
        data = {
            "message_id": message_id,
            "user_id": user_id,
            "content": content,
            "timestamp": datetime.now().isoformat()
        }
        await self.broadcast_to_room(room_id, "user_message", data)

    async def send_agent_response(self, room_id: str, message_id: str, agent_id: str, content: str, related_message_id: str = None):
        """send agent response"""
        data = {
            "message_id": message_id,
            "agent_id": agent_id,
            "content": content,
            "related_message_id": related_message_id,
            "timestamp": datetime.now().isoformat()
        }
        await self.broadcast_to_room(room_id, "agent_response", data)

    async def send_agent_token(self, room_id: str, message_id: str, agent_id: str, token: str):
        """
        Send incremental token from agent streaming response.
        
        This is for real-time token-by-token streaming from agents.
        Tokens are sent as they arrive from the agent, enabling
        real-time display in the frontend.
        
        Args:
            room_id: The room ID
            message_id: The message being generated
            agent_id: The agent sending the token
            token: The incremental text token (word, character, etc.)
        """
        data = {
            "message_id": message_id,
            "agent_id": agent_id,
            "token": token,
            "timestamp": datetime.now().isoformat()
        }
        await self.broadcast_to_room(room_id, "agent_token", data)

    async def send_error(self, room_id: str, error: str, message_id: str = None):
        """
        Send error event to room.
        
        Args:
            room_id: The room ID
            error: Error message
            message_id: Optional message ID related to the error
        """
        data = {
            "error": error,
            "message_id": message_id,
            "timestamp": datetime.now().isoformat()
        }
        await self.broadcast_to_room(room_id, "error", data)

    async def send_artifact_update(
        self, 
        room_id: str, 
        message_id: str, 
        agent_id: str, 
        artifact: Any,
        append: bool = False,
        last_chunk: bool = False
    ):
        """
        Send artifact update event from A2A agent streaming.
        
        This is used when agents stream artifacts (files, data, documents)
        incrementally during task execution. Following A2A protocol section 7.2.3.
        
        Args:
            room_id: The room ID
            message_id: The message being generated
            agent_id: The agent sending the artifact
            artifact: The artifact data (dict from A2A TaskArtifactUpdateEvent)
            append: Whether to append to existing artifact
            last_chunk: Whether this is the final chunk
        """
        data = {
            "message_id": message_id,
            "agent_id": agent_id,
            "artifact": artifact,
            "append": append,
            "last_chunk": last_chunk,
            "timestamp": datetime.now().isoformat()
        }
        await self.broadcast_to_room(room_id, "artifact_update", data)

    async def send_processing_status(self, room_id: str, status: str, message_id: str = None, details: str = None):
        """send processing status"""
        data = {
            "status": status,  # "processing", "completed", "failed"
            "message_id": message_id,
            "details": details,
            "timestamp": datetime.now().isoformat()
        }
        await self.broadcast_to_room(room_id, "processing_status", data)

    def get_room_status(self, room_id: str) -> dict:
        """get room status"""
        if room_id not in self.room_connections:
            return {
                "room_id": room_id,
                "active_connections": 0,
                "status": "no_connections"
            }
        
        return {
            "room_id": room_id,
            "active_connections": len(self.room_connections[room_id]),
            "status": "active"
        }

# global SSE manager instance
sse_manager = SSEManager()