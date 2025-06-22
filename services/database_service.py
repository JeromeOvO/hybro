from database.mongodb import mongodb 
from database.pinecone_db import pinecone_db
from services.openai_service import OpenAIService
from models.agent import Agent
from models.task import RootTask, ChildTask, TaskSession
import uuid
from typing import List, Dict, Any

class DatabaseService:
    def __init__(self):
        self.mongo = mongodb
        self.pinecone = pinecone_db
        self.ai_service = OpenAIService()
    

    async def add_agent(self, agent : Agent):
        """
        Add an agent to both MongoDB and Pinecone databases.
        Ensures consistency by rolling back if either operation fails.
        
        Args:
            agent: The agent object to add (must contain embedding field)
            
        Returns:
            str: The ID of the added agent if successful
            
        Raises:
            Exception: If any database operation fails
        """
        mongo_id = None
        
        # Generate UUID if not provided
        if not hasattr(agent, 'agent_id') or not agent.agent_id:
            agent.agent_id = str(uuid.uuid4())
        
        # Embed the agent description
        agent_embedding = await self.ai_service.get_embedding(agent.agentCard.description)

        vector_data = {
            'id': str(agent.agent_id),
            'values': agent_embedding,
            'metadata': {'type': 'agent'}
        }
        
        try:
            # Add to MongoDB
            mongo_id = await self.mongo.add_agent(agent)
            # Add to Pinecone
            self.pinecone.upsert([vector_data])
            
            return agent.agent_id
        
        except Exception as e:
            # Rollback MongoDB insertion if needed
            if mongo_id:
                try:
                    await self.mongo.delete_agent(agent.agent_id)
                except Exception as delete_error:
                    print(f"Rollback failed: {delete_error}")
                    
                # Also try to delete from Pinecone if it might have been added
                try:
                    self.pinecone.delete([str(agent.agent_id)])
                except Exception as pinecone_delete_error:
                    print(f"Pinecone rollback failed: {pinecone_delete_error}")
            
            # Re-raise the original exception
            raise Exception(f"Failed to add agent consistently to databases: {str(e)}")
    
    async def delete_agent(self, agent_id: str) -> bool:
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
            mongo_success = await self.mongo.delete_agent(agent_id)
            
            # Delete from Pinecone
            self.pinecone.delete([str(agent_id)])
            pinecone_success = True
            
            # If MongoDB delete failed but Pinecone succeeded, we have inconsistency
            if not mongo_success and pinecone_success:
                raise Exception(f"Inconsistent state: Failed to delete agent {agent_id} from MongoDB but succeeded in Pinecone")
            
            return mongo_success
            
        except Exception as e:
            # If only one operation succeeded, log the inconsistency
            if mongo_success != pinecone_success:
                print(f"Warning: Inconsistent deletion state for agent {agent_id}. MongoDB: {mongo_success}, Pinecone: {pinecone_success}")
            
            # Re-raise the exception
            raise Exception(f"Failed to delete agent consistently from databases: {str(e)}")
    
    async def update_agent(self, agent_id: str, update_data: dict) -> bool:
        """
        Update an agent in both MongoDB and Pinecone databases.
        Ensures consistency by rolling back if either operation fails.
        If the description is updated, the vector embedding is also updated.
        
        Args:
            agent_id: The ID of the agent to update
            update_data: Dictionary containing fields to update
            
        Returns:
            bool: True if update was successful
            
        Raises:
            Exception: If any database operation fails
        """
        mongo_success = False
        old_agent = None
        
        try:
            # First get the current agent to be able to rollback if needed
            old_agent = await self.mongo.get_agent(agent_id)
            if not old_agent:
                return False
            
            # Update in MongoDB
            mongo_success = await self.mongo.update_agent(agent_id, update_data)
            
            # If description was updated, update the vector in Pinecone
            if 'agentCard' in update_data and 'description' in update_data.get('agentCard', {}):
                # Get new embedding for updated description
                new_embedding = await self.ai_service.get_embedding(update_data['agentCard']['description'])
                vector_data = {
                    'id': str(agent_id),
                    'values': new_embedding,
                    'metadata': {'type': 'agent'}
                }
                # Update in Pinecone
                self.pinecone.upsert([vector_data])
            
            return mongo_success
            
        except Exception as e:
            # Rollback MongoDB update if needed
            if mongo_success and old_agent:
                try:
                    await self.mongo.update_agent(agent_id, old_agent)
                except Exception as rollback_error:
                    print(f"Rollback failed: {rollback_error}")
            
            # Re-raise the original exception
            raise Exception(f"Failed to update agent consistently in databases: {str(e)}")
    
    async def get_agent(self, agent_id: str):
        """
        Get an agent by ID from MongoDB
        
        Args:
            agent_id: The ID of the agent to retrieve
            
        Returns:
            Agent: The agent object or None if not found
        """
        return await self.mongo.get_agent(agent_id)
    
    async def get_agents(self, query=None, limit=0):
        """
        Get multiple agents matching a query
        
        Args:
            query: Optional query filter
            limit: Maximum number of results (0 for no limit)
            
        Returns:
            List[Dict]: List of agent documents
        """
        return await self.mongo.get_agents(query, limit)
    
    async def query_similar_agents(self, task_description: str, top_k: int = 5):
        """
        Find similar agents based on task description embedding and return their full information
        
        Args:
            task_description: Text to find similar agents for
            top_k: Number of results to return
            
        Returns:
            List[Agent]: List of similar agents with complete information from MongoDB
        """
        # Make sure to await the embedding generation
        embedding = await self.ai_service.get_embedding(task_description)
        
        # Then use the embedding with Pinecone - remove the incompatible parameter
        results = self.pinecone.query(
            vector=embedding,
            top_k=top_k
        )
        
        # Extract agent IDs from Pinecone results
        agent_ids = [match['id'] for match in results.get('matches', [])]
        
        if not agent_ids:
            return []
        
        # Fetch complete agent information from MongoDB
        query = {"agent_id": {"$in": agent_ids}}
        agents = await self.mongo.get_agents(query)
        
        # Sort agents in the same order as the Pinecone results
        id_to_position = {id: i for i, id in enumerate(agent_ids)}
        sorted_agents = sorted(agents, key=lambda agent: id_to_position.get(agent.get('agent_id'), float('inf')))
        
        return sorted_agents
    
    async def add_task(self, task: RootTask) -> str:
        """
        Add a root task to the database
        
        Args:
            task: The RootTask object to add
            
        Returns:
            str: The ID of the added task if successful
            
        Raises:
            Exception: If database operation fails
        """
        try:
            # Generate UUID if not provided
            if not hasattr(task, 'task_id') or not task.task_id:
                task.task_id = str(uuid.uuid4())
                
            # Add to MongoDB
            await self.mongo.add_task(task)
            return task.task_id
        
        except Exception as e:
            # Re-raise the exception
            raise Exception(f"Failed to add task to database: {str(e)}")

    async def get_task(self, task_id: str) -> RootTask:
        """
        Get a task by ID from MongoDB
        
        Args:
            task_id: The ID of the task to retrieve
            
        Returns:
            RootTask: The task object or None if not found
        """
        return await self.mongo.get_task(task_id)

    async def get_tasks(self, query=None, limit=0) -> List[RootTask]:
        """
        Get multiple tasks matching a query
        
        Args:
            query: Optional query filter
            limit: Maximum number of results (0 for no limit)
            
        Returns:
            List[RootTask]: List of task objects
        """
        return await self.mongo.get_tasks(query, limit)

    async def update_task(self, task_id: str, update_data: Dict[str, Any]) -> bool:
        """
        Update a task in the database
        
        Args:
            task_id: The ID of the task to update
            update_data: Dictionary containing fields to update
            
        Returns:
            bool: True if update was successful
            
        Raises:
            Exception: If database operation fails
        """
        try:
            return await self.mongo.update_task(task_id, update_data)
        except Exception as e:
            raise Exception(f"Failed to update task in database: {str(e)}")
        
    async def update_task_history(self, task_id: str, history: List[Dict[str, Any]]) -> bool:
        """
        Update the history of a task
        """
        return await self.mongo.update_task_history(task_id, history)

    async def delete_task(self, task_id: str) -> bool:
        """
        Delete a task from the database
        
        Args:
            task_id: The ID of the task to delete
            
        Returns:
            bool: True if deletion was successful
            
        Raises:
            Exception: If database operation fails
        """
        try:
            return await self.mongo.delete_task(task_id)
        except Exception as e:
            raise Exception(f"Failed to delete task from database: {str(e)}")
    
    async def add_child_task(self, root_task_id: str, child_task: ChildTask) -> str:
        """
        Add a child task and maintain consistency with parent task
        
        Args:
            root_task_id: ID of the parent root task
            child_task: The ChildTask object to add
            
        Returns:
            str: The ID of the created child task
            
        Raises:
            Exception: If database operation fails
        """
        try:
            # Generate UUID if not provided
            if not hasattr(child_task, 'task_id') or not child_task.task_id:
                child_task.task_id = str(uuid.uuid4())
            
            # Set parent ID
            child_task.parent_id = root_task_id
            
            # First add to child_tasks collection - 只传递child_task参数
            child_task_id = await self.mongo.add_child_task(child_task)
            
            # Then update the parent task's subtasks list
            subtask_reference = {
                "task_id": child_task.task_id,
                "description": child_task.description
            }
            
            # Add reference to parent's subtasks array
            await self.mongo.tasks_collection.update_one(
                {"task_id": root_task_id},
                {"$push": {"subtasks": subtask_reference}}
            )
            
            return child_task.task_id
        
        except Exception as e:
            # Try to clean up if first operation succeeded but second failed
            try:
                await self.mongo.delete_child_task(child_task.task_id)
            except Exception:
                pass
            
            # Re-raise the exception
            raise Exception(f"Failed to add child task to database: {str(e)}")

    async def get_child_task(self, child_task_id: str) -> ChildTask:
        """
        Get a specific child task
        
        Args:
            child_task_id: ID of the child task to retrieve
            
        Returns:
            ChildTask: The child task object or None if not found
        """
        child_task = await self.mongo.get_child_task(child_task_id)
        if not child_task:
            return None
        
        return child_task

    async def get_child_tasks_by_parent(self, root_task_id: str) -> List[ChildTask]:
        """
        Get all child tasks for a parent task
        
        Args:
            root_task_id: ID of the parent task
            
        Returns:
            List[ChildTask]: List of child task objects
        """
        child_tasks = await self.mongo.get_child_tasks_by_parent(root_task_id)
        return [ChildTask(**task) for task in child_tasks]

    async def update_child_task(self, child_task_id: str, update_data: Dict[str, Any]) -> bool:
        """
        Update a child task and maintain consistency with parent task if needed
        
        Args:
            child_task_id: ID of the child task to update
            update_data: Dictionary containing fields to update
            
        Returns:
            bool: True if update was successful
            
        Raises:
            Exception: If database operation fails
        """
        try:
            # If update_data is a Pydantic model, convert to dict
            if hasattr(update_data, 'dict'):
                update_data = update_data.dict(exclude_unset=True)
            
            # First, get the child task to find its parent_id
            child_task = await self.get_child_task(child_task_id)
            if not child_task:
                return False
            
            # Update the child task in its collection
            success = await self.mongo.update_child_task(child_task_id, update_data)
            
            # If description is updated, also update the reference in parent's subtasks array
            if 'description' in update_data and success:
                await self.mongo.tasks_collection.update_one(
                    {"task_id": child_task.parent_id, "subtasks.task_id": child_task_id},
                    {"$set": {"subtasks.$.description": update_data['description']}}
                )
            
            return success
        except Exception as e:
            raise Exception(f"Failed to update child task in database: {str(e)}")

    async def delete_child_task(self, child_task_id: str) -> bool:
        """
        Delete a child task and maintain consistency with parent task
        
        Args:
            child_task_id: ID of the child task to delete
            
        Returns:
            bool: True if deletion was successful
            
        Raises:
            Exception: If database operation fails
        """
        try:
            # First, get the child task to find its parent_id
            child_task = await self.get_child_task(child_task_id)
            if not child_task:
                return False
            
            # First remove the reference from parent's subtasks array
            await self.mongo.tasks_collection.update_one(
                {"task_id": child_task.parent_id},
                {"$pull": {"subtasks": {"task_id": child_task_id}}}
            )
            
            # Then delete the child task from its collection
            return await self.mongo.delete_child_task(child_task_id)
        except Exception as e:
            raise Exception(f"Failed to delete child task from database: {str(e)}")
    

    async def add_task_session(self, task_session: TaskSession) -> str:
        """
        Add a task session to the database
        
        Args:
            task_session: TaskSession object to add
            
        Returns:
            str: ID of the added task session
        """
        return await self.mongo.add_task_session(task_session)
    
    async def get_task_session(self, session_id: str) -> TaskSession:
        """
        Get a task session by ID
        
        Args:
            session_id: ID of the task session to retrieve

        Returns:
            TaskSession: The task session object or None if not found
        """
        return await self.mongo.get_task_session(session_id)
    
    async def update_task_session(self, session_id: str, update_data: Dict[str, Any]) -> bool:
        """
        Update a task session
        
        Args:
            session_id: ID of the task session to update
            update_data: New data to update
            
        Returns:
            bool: True if update was successful
        """
        return await self.mongo.update_task_session(session_id, update_data)
    
    async def delete_task_session(self, session_id: str) -> bool:
        """
        Delete a task session
        
        Args:
            session_id: ID of the task session to delete
            
        Returns:
            bool: True if deletion was successful
        """
        return await self.mongo.delete_task_session(session_id)

    async def add_root_task_to_session(self, session_id: str, root_task_id: str) -> bool:
        """
        Add a root task to a task session
        
        Args:
            session_id: ID of the task session to add the root task to
            root_task_id: ID of the root task to add

        Returns:
            bool: True if addition was successful
        """
        return await self.mongo.add_root_task_to_session(session_id, root_task_id)
    
    async def get_root_tasks_by_session(self, session_id: str) -> List[RootTask]:
        """
        Get all root tasks for a task session      

        Args:
            session_id: ID of the task session to get root tasks from

        Returns:
            List[RootTask]: List of root task objects
        """
        return await self.mongo.get_root_tasks_by_session(session_id)
    
    async def get_task_session_by_user_name(self, user_name: str) -> List[TaskSession]:
        """
        Get all task sessions for a user
        """
        return await self.mongo.get_task_session_by_user_name(user_name)
    