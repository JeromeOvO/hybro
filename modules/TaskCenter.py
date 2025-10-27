from models.request import TaskCenterRequest
from models.response import TaskCenterResponse
from services.agent_service import agent_service
from services.database_service import db_service
from services.openai_service import openai_service
from services.task_service import task_service


class TaskCenter:
    """
    TaskCenter - Task Lifecycle Management and Controller Service

    TaskCenter serves as the central hub for task lifecycle management, session handling,
    and task-related operations in the multi-agent system. It provides comprehensive
    task management capabilities and acts as a controller layer for external interface requests.

    Key Responsibilities:
    1. Task Session Management:
       - Creates and manages user task sessions
       - Handles session lifecycle and persistence
       - Manages session metadata and user context
       - Ensures session isolation and security

    2. Base Task Operations:
       - Creates and manages base tasks within sessions
       - Handles base task lifecycle and state management
       - Manages task metadata and user associations
       - Ensures task data integrity and consistency

    3. Meta Task Operations:
       - Creates and manages meta tasks linked to base tasks
       - Handles meta task lifecycle and execution order
       - Manages meta task-agent associations
       - Ensures proper task hierarchy and dependencies

    4. Task Query and Retrieval:
       - Provides comprehensive task query capabilities
       - Supports task retrieval by various criteria
       - Manages task relationships and hierarchies
       - Ensures efficient task data access

    5. External Interface Controller:
       - Provides RESTful API endpoints for task operations
       - Handles task request validation and response formatting
       - Manages cross-service communication for task coordination

    6. Task State Management:
       - Manages task state transitions and updates
       - Handles task message history and communication
       - Ensures task data consistency and persistence
       - Provides task progress tracking and monitoring

    Service Dependencies:
    - TaskService: For core task business logic and operations
    - DatabaseService: For task data persistence and retrieval
    - AgentService: For agent-related task operations
    - OpenAIService: For AI-powered task operations

    Usage:
    This center is typically used by external clients, other centers (like OrchestrationCenter),
    and internal services that need to manage task lifecycle and operations.
    The center ensures proper task management and data consistency across the system.
    """

    def __init__(self):
        """
        Initialize TaskCenter with required service dependencies.

        Sets up the task service for core operations, database service for persistence,
        agent service for agent-related operations, and OpenAI service for AI operations.
        """
        self.openai_service = openai_service  # Use singleton
        self.database_service = db_service  # Use singleton
        self.agent_service = agent_service  # Use singleton
        self.task_service = task_service  # Use singleton

    # Task Sessions
    async def create_new_session(
        self, request: TaskCenterRequest
    ) -> TaskCenterResponse:
        """
        Create a new task session for the user.

        This method handles the complete session creation process:
        1. Validates the session creation request
        2. Generates unique session identifiers
        3. Creates session metadata and user associations
        4. Initializes session state and configuration
        5. Returns session creation results

        Args:
            request: TaskCenterRequest containing:
                - user_name: The username for the session
                - session_parameters: Optional session configuration

        Returns:
            TaskCenterResponse containing:
                - session_id: The created session ID
                - user_name: The associated username
                - success: Boolean indicating success/failure
                - status_code: HTTP status code
                - error: Error message if applicable
        """
        return await self.task_service.create_new_session(request)

    async def create_new_base_task(
        self, request: TaskCenterRequest
    ) -> TaskCenterResponse:
        """
        Create a new base task within a session.

        This method handles the complete base task creation process:
        1. Validates the task creation request and session
        2. Generates unique task identifiers
        3. Creates task metadata and session associations
        4. Initializes task state and configuration
        5. Returns task creation results

        Args:
            request: TaskCenterRequest containing:
                - session_id: The session ID for the task
                - user_name: The username for the task
                - task: The task object to create
                - task_parameters: Optional task configuration

        Returns:
            TaskCenterResponse containing:
                - task_id: The created task ID
                - session_id: The associated session ID
                - user_name: The associated username
                - success: Boolean indicating success/failure
                - status_code: HTTP status code
                - error: Error message if applicable
        """
        return await self.task_service.create_new_base_task(request)

    async def create_new_meta_task(
        self, request: TaskCenterRequest
    ) -> TaskCenterResponse:
        """
        Create a new meta task linked to a parent task.

        This method handles the complete meta task creation process:
        1. Validates the meta task creation request
        2. Generates unique meta task identifiers
        3. Creates meta task metadata and parent associations
        4. Initializes meta task state and execution order
        5. Returns meta task creation results

        Args:
            request: TaskCenterRequest containing:
                - parent_task_id: The parent task ID
                - user_name: The username for the task
                - task: The task object to create
                - user_input: The task description/input
                - execution_order: Optional execution order

        Returns:
            TaskCenterResponse containing:
                - task_id: The created meta task ID
                - parent_task_id: The associated parent task ID
                - user_name: The associated username
                - success: Boolean indicating success/failure
                - status_code: HTTP status code
                - error: Error message if applicable
        """
        return await self.task_service.create_new_meta_task(request)

    async def query_meta_task_by_task_id(
        self, request: TaskCenterRequest
    ) -> TaskCenterResponse:
        """
        Query a meta task by its task ID.

        This method retrieves detailed meta task information:
        1. Validates the task ID and request
        2. Retrieves meta task data from the database
        3. Validates task existence and accessibility
        4. Returns comprehensive task information

        Args:
            request: TaskCenterRequest containing:
                - task_id: The meta task ID to query
                - query_parameters: Optional query parameters

        Returns:
            TaskCenterResponse containing:
                - meta_task: The retrieved meta task object
                - success: Boolean indicating success/failure
                - status_code: HTTP status code
                - error: Error message if applicable
        """
        return await self.task_service.query_meta_task_by_task_id(request)

    async def query_meta_tasks_by_parent_task_id(
        self, request: TaskCenterRequest
    ) -> TaskCenterResponse:
        """
        Query meta tasks by their parent task ID.

        This method retrieves all meta tasks associated with a parent task:
        1. Validates the parent task ID and request
        2. Retrieves all related meta tasks from the database
        3. Validates task relationships and hierarchy
        4. Returns comprehensive task list information

        Args:
            request: TaskCenterRequest containing:
                - parent_task_id: The parent task ID to query
                - query_parameters: Optional query parameters

        Returns:
            TaskCenterResponse containing:
                - meta_tasks: List of retrieved meta task objects
                - success: Boolean indicating success/failure
                - status_code: HTTP status code
                - error: Error message if applicable
        """
        return await self.task_service.query_meta_tasks_by_parent_task_id(request)

    async def query_base_task_by_task_id(
        self, request: TaskCenterRequest
    ) -> TaskCenterResponse:
        """
        Query a base task by its task ID.

        This method retrieves detailed base task information:
        1. Validates the task ID and request
        2. Retrieves base task data from the database
        3. Validates task existence and accessibility
        4. Returns comprehensive task information

        Args:
            request: TaskCenterRequest containing:
                - task_id: The base task ID to query
                - query_parameters: Optional query parameters

        Returns:
            TaskCenterResponse containing:
                - base_task: The retrieved base task object
                - success: Boolean indicating success/failure
                - status_code: HTTP status code
                - error: Error message if applicable
        """
        return await self.task_service.query_base_task_by_task_id(request)

    async def query_all_sessions(
        self, request: TaskCenterRequest
    ) -> TaskCenterResponse:
        """
        Query all sessions.
        """
        return await self.task_service.query_all_sessions(request)

    async def query_base_tasks_by_session_id(
        self, request: TaskCenterRequest
    ) -> TaskCenterResponse:
        """
        Query a base task by its session ID.
        """
        return await self.task_service.query_base_tasks_by_session_id(request)

    async def delete_meta_task_by_task_id(
        self, request: TaskCenterRequest
    ) -> TaskCenterResponse:
        """
        Delete a meta task by its task ID.

        This method handles the complete meta task deletion process:
        1. Validates the task ID and deletion request
        2. Checks task existence and accessibility
        3. Performs cleanup operations and data removal
        4. Updates related task relationships
        5. Returns deletion results

        Args:
            request: TaskCenterRequest containing:
                - task_id: The meta task ID to delete
                - deletion_parameters: Optional deletion parameters

        Returns:
            TaskCenterResponse containing:
                - task_id: The deleted task ID
                - success: Boolean indicating success/failure
                - status_code: HTTP status code
                - error: Error message if applicable
        """
        return await self.task_service.delete_meta_task_by_task_id(request)

    async def update_meta_task_by_task_id(
        self, request: TaskCenterRequest
    ) -> TaskCenterResponse:
        """
        Update a meta task by its task ID.

        This method handles the complete meta task update process:
        1. Validates the task ID and update request
        2. Checks task existence and accessibility
        3. Performs update operations and data modification
        4. Validates update consistency and integrity
        5. Returns update results

        Args:
            request: TaskCenterRequest containing:
                - task_id: The meta task ID to update
                - meta_task: The updated meta task object
                - update_parameters: Optional update parameters

        Returns:
            TaskCenterResponse containing:
                - task_id: The updated task ID
                - success: Boolean indicating success/failure
                - status_code: HTTP status code
                - error: Error message if applicable
        """
        return await self.task_service.update_meta_task_by_task_id(request)

    async def add_message_to_meta_task(
        self, request: TaskCenterRequest
    ) -> TaskCenterResponse:
        """
        Add a message to a meta task.

        This method handles message addition to meta tasks:
        1. Validates the task ID and message request
        2. Checks task existence and accessibility
        3. Adds message to task history
        4. Updates task state and metadata
        5. Returns message addition results

        Args:
            request: TaskCenterRequest containing:
                - task_id: The meta task ID
                - message: The message to add
                - message_parameters: Optional message parameters

        Returns:
            TaskCenterResponse containing:
                - task_id: The task ID
                - success: Boolean indicating success/failure
                - status_code: HTTP status code
                - error: Error message if applicable
        """
        return await self.task_service.add_message_to_meta_task(request)

    async def update_agent_id_of_meta_task(
        self, request: TaskCenterRequest
    ) -> TaskCenterResponse:
        """
        Update the agent ID associated with a meta task.

        This method handles agent assignment updates:
        1. Validates the task ID and agent assignment request
        2. Checks task existence and current agent assignment
        3. Updates agent association and metadata
        4. Validates agent availability and compatibility
        5. Returns agent assignment results

        Args:
            request: TaskCenterRequest containing:
                - task_id: The meta task ID
                - agent_id: The new agent ID to assign
                - assignment_parameters: Optional assignment parameters

        Returns:
            TaskCenterResponse containing:
                - task_id: The task ID
                - agent_id: The assigned agent ID
                - success: Boolean indicating success/failure
                - status_code: HTTP status code
                - error: Error message if applicable
        """
        return await self.task_service.update_agent_id_of_meta_task(request)

    async def update_task_of_meta_task(
        self, request: TaskCenterRequest
    ) -> TaskCenterResponse:
        """
        Update the task details of a meta task.

        This method handles task detail updates:
        1. Validates the task ID and update request
        2. Checks task existence and accessibility
        3. Updates task details and metadata
        4. Validates update consistency and integrity
        5. Returns task update results

        Args:
            request: TaskCenterRequest containing:
                - task_id: The meta task ID
                - task: The updated task object
                - update_parameters: Optional update parameters

        Returns:
            TaskCenterResponse containing:
                - task_id: The task ID
                - success: Boolean indicating success/failure
                - status_code: HTTP status code
                - error: Error message if applicable
        """
        return await self.task_service.update_task_of_meta_task(request)


task_center = TaskCenter()
