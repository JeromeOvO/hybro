"""SDK-free internal task and agent card schemas.

A2A SDK models are intentionally converted at adapter boundaries so common
modules can remain stable even when the external SDK surface changes.
"""

from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal, Self
from uuid import uuid4

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    TypeAdapter,
    field_serializer,
    field_validator,
    model_validator,
)


class FileContent(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    mimeType: str | None = Field(
        default=None,
        validation_alias=AliasChoices("mimeType", "mime_type"),
        serialization_alias="mimeType",
    )
    bytes: str | None = None
    uri: str | None = None

    def model_dump(self, *args, **kwargs):
        kwargs.setdefault("by_alias", False)
        return super().model_dump(*args, **kwargs)

    @property
    def mime_type(self) -> str | None:
        return self.mimeType

    @mime_type.setter
    def mime_type(self, value: str | None) -> None:
        self.mimeType = value

    @model_validator(mode="after")
    def check_content(self) -> Self:
        if not (self.bytes or self.uri):
            raise ValueError("Either 'bytes' or 'uri' must be present in the file data")
        if self.bytes and self.uri:
            raise ValueError(
                "Only one of 'bytes' or 'uri' can be present in the file data"
            )
        return self


class TextPart(BaseModel):
    kind: Literal["text"] = "text"
    text: str
    metadata: dict[str, Any] | None = None


class FilePart(BaseModel):
    kind: Literal["file"] = "file"
    file: FileContent
    metadata: dict[str, Any] | None = None


class DataPart(BaseModel):
    kind: Literal["data"] = "data"
    data: dict[str, Any]
    metadata: dict[str, Any] | None = None


class TaskState(str, Enum):
    submitted = "submitted"
    working = "working"
    input_required = "input-required"
    auth_required = "auth-required"
    policy_required = "policy-required"
    completed = "completed"
    failed = "failed"
    canceled = "canceled"
    rejected = "rejected"
    expired = "expired"


PartUnion = Annotated[TextPart | FilePart | DataPart, Field(discriminator="kind")]


class Part(RootModel[PartUnion]):
    """Wrapper that preserves .root access pattern used throughout the codebase."""

    pass


class MessageRole(str, Enum):
    USER = "user"
    AGENT = "agent"


class Message(BaseModel):
    role: MessageRole
    kind: str = "message"
    message_id: str | None = Field(default=None, alias="messageId")
    context_id: str | None = Field(default=None, alias="contextId")
    parts: list[Part]
    metadata: dict[str, Any] | None = None

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)


class TaskStatus(BaseModel):
    state: TaskState
    message: Message | None = None
    timestamp: datetime | None = Field(default_factory=datetime.now)

    @field_serializer("timestamp")
    def serialize_dt(self, dt: datetime | None, _info):
        return dt.isoformat() if dt else None


class Artifact(BaseModel):
    artifact_id: str | None = Field(default=None, alias="artifactId")
    name: str | None = None
    description: str | None = None
    parts: list[Part]
    metadata: dict[str, Any] | None = None
    index: int = 0
    append: bool | None = None
    lastChunk: bool | None = None

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)


class Task(BaseModel):
    id: str
    kind: str = "task"
    sessionId: str | None = None
    context_id: str | None = Field(default=None, alias="contextId")
    status: TaskStatus
    artifacts: list[Artifact] | None = None
    history: list[Message] | None = None
    metadata: dict[str, Any] | None = None

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    @model_validator(mode="before")
    @classmethod
    def _coerce_from_sdk(cls, data):
        """Accept SDK Task objects or dicts transparently."""
        if isinstance(data, dict):
            return data
        if type(data).__module__.startswith("common.types"):
            return data
        if hasattr(data, "model_dump"):
            return data.model_dump(mode="json")
        return data


class TaskStatusUpdateEvent(BaseModel):
    id: str = Field(
        validation_alias=AliasChoices("id", "taskId"), serialization_alias="taskId"
    )
    kind: str = "status-update"
    context_id: str | None = Field(default=None, alias="contextId")
    status: TaskStatus
    final: bool = False
    metadata: dict[str, Any] | None = None

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    @property
    def task_id(self) -> str:
        return self.id


class TaskArtifactUpdateEvent(BaseModel):
    id: str = Field(
        validation_alias=AliasChoices("id", "taskId"), serialization_alias="taskId"
    )
    kind: str = "artifact-update"
    context_id: str | None = Field(default=None, alias="contextId")
    artifact: Artifact
    append: bool = False
    last_chunk: bool = Field(default=False, alias="lastChunk")
    metadata: dict[str, Any] | None = None

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    @field_validator("append", "last_chunk", mode="before")
    @classmethod
    def _default_nullable_flags(cls, value):
        return False if value is None else value

    @property
    def task_id(self) -> str:
        return self.id


class AuthenticationInfo(BaseModel):
    model_config = ConfigDict(extra="allow")

    schemes: list[str]
    credentials: str | None = None


class PushNotificationConfig(BaseModel):
    url: str
    token: str | None = None
    authentication: AuthenticationInfo | None = None


class TaskIdParams(BaseModel):
    id: str
    metadata: dict[str, Any] | None = None


class TaskQueryParams(TaskIdParams):
    historyLength: int | None = None


class TaskSendParams(BaseModel):
    id: str
    sessionId: str = Field(default_factory=lambda: uuid4().hex)
    message: Message
    acceptedOutputModes: list[str] | None = None
    pushNotification: PushNotificationConfig | None = None
    historyLength: int | None = None
    metadata: dict[str, Any] | None = None


class TaskPushNotificationConfig(BaseModel):
    id: str
    pushNotificationConfig: PushNotificationConfig


## RPC Messages


class JSONRPCMessage(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: int | str | None = Field(default_factory=lambda: uuid4().hex)


class JSONRPCRequest(JSONRPCMessage):
    method: str
    params: dict[str, Any] | None = None


class JSONRPCError(BaseModel):
    code: int
    message: str
    data: Any | None = None


class JSONRPCResponse(JSONRPCMessage):
    result: Any | None = None
    error: JSONRPCError | None = None


class SendTaskRequest(JSONRPCRequest):
    method: Literal["tasks/send"] = "tasks/send"
    params: TaskSendParams = Field(
        default_factory=lambda: TaskSendParams(
            id="", message=Message(role=MessageRole.USER, parts=[])
        )
    )


class SendTaskResponse(JSONRPCResponse):
    result: Task | None = None


class SendTaskStreamingRequest(JSONRPCRequest):
    method: Literal["tasks/sendSubscribe"] = "tasks/sendSubscribe"
    params: TaskSendParams = Field(
        default_factory=lambda: TaskSendParams(
            id="", message=Message(role=MessageRole.USER, parts=[])
        )
    )


class SendTaskStreamingResponse(JSONRPCResponse):
    result: TaskStatusUpdateEvent | TaskArtifactUpdateEvent | None = None


class GetTaskRequest(JSONRPCRequest):
    method: Literal["tasks/get"] = "tasks/get"
    params: TaskQueryParams = Field(default_factory=lambda: TaskQueryParams(id=""))


class GetTaskResponse(JSONRPCResponse):
    result: Task | None = None


class CancelTaskRequest(JSONRPCRequest):
    method: Literal["tasks/cancel",] = "tasks/cancel"
    params: TaskIdParams = Field(default_factory=lambda: TaskIdParams(id=""))


class CancelTaskResponse(JSONRPCResponse):
    result: Task | None = None


class SetTaskPushNotificationRequest(JSONRPCRequest):
    method: Literal["tasks/pushNotification/set",] = "tasks/pushNotification/set"
    params: TaskPushNotificationConfig = Field(
        default_factory=lambda: TaskPushNotificationConfig(
            id="", pushNotificationConfig=PushNotificationConfig(url="")
        )
    )


class SetTaskPushNotificationResponse(JSONRPCResponse):
    result: TaskPushNotificationConfig | None = None


class GetTaskPushNotificationRequest(JSONRPCRequest):
    method: Literal["tasks/pushNotification/get",] = "tasks/pushNotification/get"
    params: TaskIdParams = Field(default_factory=lambda: TaskIdParams(id=""))


class GetTaskPushNotificationResponse(JSONRPCResponse):
    result: TaskPushNotificationConfig | None = None


class TaskResubscriptionRequest(JSONRPCRequest):
    method: Literal["tasks/resubscribe",] = "tasks/resubscribe"
    params: TaskIdParams = Field(default_factory=lambda: TaskIdParams(id=""))


A2ARequest = TypeAdapter(
    Annotated[
        SendTaskRequest
        | GetTaskRequest
        | CancelTaskRequest
        | SetTaskPushNotificationRequest
        | GetTaskPushNotificationRequest
        | TaskResubscriptionRequest
        | SendTaskStreamingRequest,
        Field(discriminator="method"),
    ]
)

## Error types


class JSONParseError(JSONRPCError):
    code: int = -32700
    message: str = "Invalid JSON payload"
    data: Any | None = None


class InvalidRequestError(JSONRPCError):
    code: int = -32600
    message: str = "Request payload validation error"
    data: Any | None = None


class MethodNotFoundError(JSONRPCError):
    code: int = -32601
    message: str = "Method not found"
    data: None = None


class InvalidParamsError(JSONRPCError):
    code: int = -32602
    message: str = "Invalid parameters"
    data: Any | None = None


class InternalError(JSONRPCError):
    code: int = -32603
    message: str = "Internal error"
    data: Any | None = None


class TaskNotFoundError(JSONRPCError):
    code: int = -32001
    message: str = "Task not found"
    data: None = None


class TaskNotCancelableError(JSONRPCError):
    code: int = -32002
    message: str = "Task cannot be canceled"
    data: None = None


class PushNotificationNotSupportedError(JSONRPCError):
    code: int = -32003
    message: str = "Push Notification is not supported"
    data: None = None


class UnsupportedOperationError(JSONRPCError):
    code: int = -32004
    message: str = "This operation is not supported"
    data: None = None


class ContentTypeNotSupportedError(JSONRPCError):
    code: int = -32005
    message: str = "Incompatible content types"
    data: None = None


class AgentProvider(BaseModel):
    organization: str
    url: str | None = None


class AgentCapabilities(BaseModel):
    model_config = ConfigDict(extra="ignore")

    streaming: bool | None = False
    pushNotifications: bool | None = False
    stateTransitionHistory: bool | None = False
    extensions: list[dict[str, Any]] | None = None

    @property
    def push_notifications(self) -> bool | None:
        return self.pushNotifications

    @push_notifications.setter
    def push_notifications(self, value: bool | None) -> None:
        self.pushNotifications = value

    @property
    def state_transition_history(self) -> bool | None:
        return self.stateTransitionHistory

    @state_transition_history.setter
    def state_transition_history(self, value: bool | None) -> None:
        self.stateTransitionHistory = value


class AgentAuthentication(BaseModel):
    schemes: list[str]
    credentials: str | None = None


class AgentSkill(BaseModel):
    id: str
    name: str
    description: str | None = None
    tags: list[str] | None = None
    examples: list[str] | None = None
    inputModes: list[str] | None = None
    outputModes: list[str] | None = None


class AgentCard(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    name: str
    description: str | None = None
    url: str
    provider: AgentProvider | None = None
    version: str
    documentationUrl: str | None = None
    iconUrl: str | None = None
    capabilities: AgentCapabilities
    authentication: AgentAuthentication | None = None
    defaultInputModes: list[str] = ["text"]
    defaultOutputModes: list[str] = ["text"]
    skills: list[AgentSkill]
    protocolVersion: str | None = None
    preferredTransport: str | None = None
    additionalInterfaces: list[dict[str, Any]] | None = None
    security: list[dict[str, list[str]]] | None = None
    securitySchemes: dict[str, Any] | None = None
    signatures: list[dict[str, Any]] | None = None
    supports_authenticated_extended_card: bool | None = Field(
        default=None, alias="supportsAuthenticatedExtendedCard"
    )

    def model_dump(self, *args, **kwargs):
        kwargs.setdefault("by_alias", True)
        return super().model_dump(*args, **kwargs)

    @property
    def documentation_url(self) -> str | None:
        return self.documentationUrl

    @documentation_url.setter
    def documentation_url(self, value: str | None) -> None:
        self.documentationUrl = value

    @property
    def default_input_modes(self) -> list[str]:
        return self.defaultInputModes

    @default_input_modes.setter
    def default_input_modes(self, value: list[str]) -> None:
        self.defaultInputModes = value

    @property
    def default_output_modes(self) -> list[str]:
        return self.defaultOutputModes

    @default_output_modes.setter
    def default_output_modes(self, value: list[str]) -> None:
        self.defaultOutputModes = value


class A2AClientError(Exception):
    pass


class A2AClientHTTPError(A2AClientError):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"HTTP Error {status_code}: {message}")


class A2AClientJSONError(A2AClientError):
    def __init__(self, message: str):
        self.message = message
        super().__init__(f"JSON Error: {message}")


class MissingAPIKeyError(Exception):
    """Exception for missing API key."""

    pass
