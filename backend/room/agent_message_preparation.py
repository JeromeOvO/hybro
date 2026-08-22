from __future__ import annotations

import json
from typing import Any, Protocol
from uuid import uuid4

from common.dto import RoomMessageInfo
from common.protocols import AttachmentContentReader
from common.protocols.context_memory_protocols import ContextAssemblyPort
from common.types import DataPart, Message, Part, TextPart
from common.types import MessageRole as Role
from common.utils.a2a_file_modes import agent_input_modes, mime_type_is_accepted
from common.utils.logger import get_logger
from execution.orchestration.turn_context import (
    TurnQuoteMissingError,
    format_quoted_context_header,
    load_turn_context,
)
from models.memory import RoomMemory
from models.quote import QuotedSnippet
from models.request import AgentCenterRequest, RoomCenterAgentMessageRequest
from models.response import AgentCenterResponse, RoomCenterAgentMessageResponse
from models.room import RoomUserMessage, UserAttachment
from room.a2a_file_parts import AttachmentDispatchContext, AttachmentPreflightFailure
from room.attachments import build_message_parts as platform_build_message_parts

logger = get_logger(__name__)

_PUBLIC_ATTACHMENT_PREFLIGHT_MESSAGES = {
    "agent_does_not_accept_file_type": "The agent does not accept an attached file type.",
    "agent_card_unavailable": "Agent card unavailable for attachment preflight.",
    "file_too_large": "An attached file exceeds the maximum size.",
    "message_too_large": "The attached files exceed the maximum message size.",
    "file_unavailable": "An attached file is unavailable.",
    "storage_unavailable": "Attachment content is temporarily unavailable.",
    "empty_file": "An attached file is empty.",
    "encoding_failed": "An attached file could not be encoded.",
}


class AgentUrlReader(Protocol):
    async def get_agent_url_by_agent_id(
        self, request: AgentCenterRequest
    ) -> AgentCenterResponse: ...


class AgentRoomReader(Protocol):
    async def get_agent_by_agent_id(self, agent_id: str) -> Any: ...

    async def get_room_by_room_id(self, room_id: str) -> Any: ...


class UserMessageReader(Protocol):
    async def get_room_user_message_by_message_id(
        self, message_id: str
    ) -> RoomUserMessage | None: ...

    async def get_room_user_messages_by_room_id(
        self, room_id: str
    ) -> list[RoomUserMessage]: ...


class QuoteReader(Protocol):
    async def get_quoted_snippet_by_id(self, quote_id: str) -> QuotedSnippet | None: ...


class MessageLineageReader(Protocol):
    async def get_message(self, message_id: str) -> RoomMessageInfo | None: ...


def _public_attachment_preflight_failure(
    failure: AttachmentPreflightFailure,
) -> dict[str, str]:
    return {
        "code": failure.code,
        "message": _PUBLIC_ATTACHMENT_PREFLIGHT_MESSAGES.get(
            failure.code,
            "Attachment preflight failed.",
        ),
    }


class AgentMessagePreparationService:
    def __init__(
        self,
        *,
        agent_url_reader: AgentUrlReader,
        agent_room_reader: AgentRoomReader,
        user_message_reader: UserMessageReader,
        quote_reader: QuoteReader,
        message_lineage_reader: MessageLineageReader,
        attachment_content_reader: AttachmentContentReader,
        max_raw_bytes: int,
        max_encoded_bytes: int,
        context_assembly: ContextAssemblyPort | None = None,
    ) -> None:
        self._agent_url_reader = agent_url_reader
        self._agent_room_reader = agent_room_reader
        self._user_message_reader = user_message_reader
        self._quote_reader = quote_reader
        self._message_lineage_reader = message_lineage_reader
        self._attachment_content_reader = attachment_content_reader
        self._max_raw_bytes = max_raw_bytes
        self._max_encoded_bytes = max_encoded_bytes
        self._context_assembly = context_assembly

    def bind_context_assembly(self, context_assembly: ContextAssemblyPort) -> None:
        self._context_assembly = context_assembly

    async def _build_message_parts(
        self,
        text: str,
        attachments: list[UserAttachment] | None,
        agent_card: Any,
        context: AttachmentDispatchContext | None = None,
    ) -> list[Part] | AttachmentPreflightFailure:
        return await platform_build_message_parts(
            text=text,
            attachments=attachments,
            agent_card=agent_card,
            content_reader=self._attachment_content_reader,
            max_raw_bytes=self._max_raw_bytes,
            max_encoded_bytes=self._max_encoded_bytes,
            context=context,
        )

    def _require_context_assembly(self) -> ContextAssemblyPort:
        if self._context_assembly is None:
            raise RuntimeError("Agent message context assembly has not been bound")
        return self._context_assembly

    @staticmethod
    def _assembled_context_text(assembled: Any) -> str:
        metadata = getattr(assembled, "metadata", {}) or {}
        return metadata.get("context", "")

    def _build_agent_execution_context_from_memory(
        self,
        *,
        room_memory: Any,
        current_task: str,
        agent_name: str | None,
        room_awareness: str | None,
        quoted_text: str | None,
        agent_task: str | None,
    ) -> str:
        assembled = self._require_context_assembly().assemble_agent_execution_context_from_memory(
            room_memory,
            current_task,
            agent_name=agent_name,
            room_awareness=room_awareness,
            quoted_text=quoted_text,
            agent_task=agent_task,
            include_system_instruction=True,
        )
        return self._assembled_context_text(assembled)

    async def _build_room_awareness(  # noqa: C901
        self,
        room_id: str,
        current_agent_id: str,
        task_description: str | None = None,
        agent_profiles: list[tuple[str, str, str]] | None = None,
    ) -> str | None:
        """
        Build room awareness context for an agent.

        This gives the agent awareness of other agents in the room and their roles,
        enabling better collaboration in multi-agent scenarios.

        Per design doc section 7.4 and 15: This should only be called for Supervisor-
        orchestrated multi-agent tasks. Direct chat (single agent working alone) should
        NOT receive room awareness to avoid misleading the agent about teammates.

        Args:
            room_id: The room ID
            current_agent_id: The ID of the agent receiving the context
            task_description: Specific task description for this agent. If None,
                              this indicates a direct-chat scenario and awareness
                              will be skipped.
            agent_profiles: Optional pre-built list of (agent_id, name, description)
                            tuples to avoid redundant DB lookups. If not provided,
                            will fetch from database.

        Returns:
            Room awareness context string, or None if not applicable
        """
        # Skip for direct chat — only 1 agent is working, awareness is misleading.
        # task_description=None is set precisely for direct-chat scenarios in both
        # queue and Supervisor paths.
        if task_description is None:
            return None

        try:
            # If agent_profiles provided with descriptions, use them directly (avoids DB calls)
            if agent_profiles is not None:
                # Check if any peer agent has a description - if all are empty,
                # fall through to DB path for richer output
                has_descriptions = any(
                    description
                    for agent_id, name, description in agent_profiles
                    if agent_id != current_agent_id
                )

                if has_descriptions:
                    other_agents: list[str] = []
                    for agent_id, name, description in agent_profiles:
                        if agent_id != current_agent_id:
                            if description:
                                other_agents.append(f"- {name}: {description}")
                            else:
                                other_agents.append(f"- {name}")

                    if not other_agents:
                        return None

                    parts = ["[Room Context]"]
                    parts.append("You are working in a team with these other agents:")
                    parts.extend(other_agents)
                    parts.append(
                        f"\nYour specific role in this task: {task_description}"
                    )
                    return "\n".join(parts)

                # Fall through to DB path if no descriptions available

            # Fallback: fetch from database (for backward compatibility)
            room = await self._agent_room_reader.get_room_by_room_id(room_id)
            if not room or not room.room_agent_set:
                return None

            # Only inject room awareness for Supervisor-enabled rooms.
            # Legacy multi-agent rooms opted out of this feature.
            room_extend_info = room.extend_info or {}
            if not room_extend_info.get("use_supervisor", False):
                return None

            # Skip room awareness for single-agent rooms
            if len(room.room_agent_set) <= 1:
                return None

            # Build list of other agents in the room
            other_agents: list[str] = []
            for agent_id, agent_name in room.room_agent_set.items():
                if agent_id != current_agent_id:
                    # Try to get agent description for richer context
                    agent = await self._agent_room_reader.get_agent_by_agent_id(
                        agent_id
                    )
                    if agent and agent.agent_card and agent.agent_card.description:
                        other_agents.append(
                            f"- {agent_name}: {agent.agent_card.description}"
                        )
                    else:
                        other_agents.append(f"- {agent_name}")

            if not other_agents:
                return None

            # Build the room awareness context
            parts = ["[Room Context]"]
            parts.append("You are working in a team with these other agents:")
            parts.extend(other_agents)
            parts.append(f"\nYour specific role in this task: {task_description}")

            return "\n".join(parts)

        except Exception as exc:
            logger.warning(
                "room_awareness_build_failed",
                extra={"error_type": type(exc).__name__},
            )
            return None

    async def process_agent_message(  # noqa: C901
        self,
        request: RoomCenterAgentMessageRequest,
        room_memory: RoomMemory | None = None,
        quoted_text: str | None = None,
        orchestration_user_message_id: str | None = None,
    ) -> RoomCenterAgentMessageResponse:
        """
        Process an agent message by building budget-aware context.

        Uses canonical top-level RoomMemory history through ContextAssemblyPort.
        If assembly fails, the original outbound message is retained and the
        failure is logged; deleted nested history is never used as a fallback.

        Args:
            request: The agent message request
            room_memory: Full RoomMemory object (preferred) or None
            quoted_text: Legacy: text the user highlighted (used when no turn context)
            orchestration_user_message_id: Root user message id for this turn (QUOTE_REPLY)

        Returns:
            Response with the prepared A2A message including context
        """
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
        query_agent_url_response = (
            await self._agent_url_reader.get_agent_url_by_agent_id(
                AgentCenterRequest(agent_id=agent_id)
            )
        )
        if query_agent_url_response.agent_url is None:
            return RoomCenterAgentMessageResponse(
                message_id=None,
                message=None,
                success=False,
                error="Agent url is not found",
                status_code=400,
            )

        agent_msg = request.message
        dispatch_task_text = (
            request.dispatch_task.strip()
            if isinstance(request.dispatch_task, str)
            else None
        )
        if dispatch_task_text:
            agent_message = Message(
                message_id=uuid4().hex,
                role=Role.USER,
                parts=[Part(root=TextPart(text=dispatch_task_text))],
            )
        else:
            task = (
                agent_msg.message_content.message_task
                if agent_msg.message_content
                else None
            )
            if task and isinstance(task, dict):
                from common.types import Task as TaskModel

                task = TaskModel.model_validate(task)
                agent_msg.message_content.message_task = task
            if task and task.history:
                agent_message = task.history[0].model_copy(deep=True)
            else:
                return RoomCenterAgentMessageResponse(
                    message_id=None,
                    message=None,
                    success=False,
                    error="No task content found",
                    status_code=400,
                )

        # Get agent info for context personalization
        agent = await self._agent_room_reader.get_agent_by_agent_id(agent_id)
        agent_name = agent.agent_card.name if agent else None

        resolved_resource_payloads = request.resolved_resource_payloads
        if resolved_resource_payloads is None:
            resolved_resource_payloads = request.dispatch_resource_payloads
        if resolved_resource_payloads is None:
            resolved_payload_refs = (
                message.extend_info.get("resolved_dispatch_payload_refs")
                if isinstance(message.extend_info, dict)
                else None
            )
            resolved_resource_payloads = (
                resolved_payload_refs.get("resource_payloads")
                if isinstance(resolved_payload_refs, dict)
                else None
            )
        if resolved_resource_payloads is None:
            resolved_resource_payloads = (
                message.extend_info.get("resolved_dispatch_resource_payloads")
                if isinstance(message.extend_info, dict)
                else None
            )

        # Turn context (QUOTE_REPLY): user prompt + quote snapshot + separate agent task
        turn_ctx = None
        if orchestration_user_message_id:
            um = await self._user_message_reader.get_room_user_message_by_message_id(
                orchestration_user_message_id
            )
            if um:
                try:
                    turn_ctx = await load_turn_context(self._quote_reader, um)
                except TurnQuoteMissingError as e:
                    return RoomCenterAgentMessageResponse(
                        message_id=message.message_id,
                        message=message,
                        success=False,
                        error=str(e),
                        status_code=400,
                    )

        original_text = agent_message.parts[0].root.text or ""
        if dispatch_task_text:
            current_task_for_cas = dispatch_task_text
            agent_task_for_cas = None
        else:
            current_task_for_cas = turn_ctx.message_text if turn_ctx else original_text
            agent_task_for_cas = original_text if turn_ctx else None

        room_awareness_task_description = (
            dispatch_task_text if dispatch_task_text else message.task_content
        )
        quoted_for_cas: str | None = None
        if turn_ctx and turn_ctx.quoted_text:
            hdr = format_quoted_context_header(turn_ctx)
            quoted_for_cas = f"{hdr}\n---\n{turn_ctx.quoted_text}\n---"
        elif quoted_text:
            quoted_for_cas = quoted_text

        # Build room awareness context (other agents in the team)
        # Only for Supervisor-orchestrated multi-agent tasks (task_content != None)
        # Extract pre-built agent_profiles from extend_info to avoid redundant DB lookups
        agent_profiles = None
        if message.extend_info and isinstance(message.extend_info, dict):
            agent_profiles = message.extend_info.get("agent_profiles")

        room_awareness = await self._build_room_awareness(
            room_id=message.room_id,
            current_agent_id=agent_id,
            task_description=room_awareness_task_description,
            agent_profiles=agent_profiles,
        )

        # Build context only from canonical RoomMemory assembly when memory exists.
        try:
            if agent_message and agent_message.parts and len(agent_message.parts) > 0:
                if room_memory is not None:
                    # Canonical-only, budget-aware context via ContextAssemblyPort.
                    context = self._build_agent_execution_context_from_memory(
                        room_memory=room_memory,
                        current_task=current_task_for_cas,
                        agent_name=agent_name,
                        room_awareness=room_awareness,
                        quoted_text=quoted_for_cas,
                        agent_task=agent_task_for_cas,
                    )
                else:
                    # No context available
                    quoted_section = ""
                    if quoted_for_cas:
                        if "\n---\n" in quoted_for_cas:
                            quoted_section = f"[Quoted context]\n{quoted_for_cas}\n\n"
                        else:
                            quoted_section = (
                                f"[Quoted context]\n"
                                f"The user is referencing the following specific content:\n"
                                f'"{quoted_for_cas}"\n\n'
                            )
                    room_awareness_section = ""
                    if room_awareness:
                        room_awareness_section = f"{room_awareness}\n\n"
                    task_section = ""
                    if agent_task_for_cas:
                        task_section = f"\n\n[Task]\n{agent_task_for_cas}"
                    context = (
                        f"{quoted_section}{room_awareness_section}"
                        f"[Current request]\nUser: {current_task_for_cas}{task_section}"
                    )
                    if agent_name:
                        context += (
                            f"\n\nYou are {agent_name}. "
                            "Please respond to the request above."
                        )

                agent_message.parts[0].root.text = context
        except Exception as exc:
            # Log but continue with original message if context building fails
            logger.warning(
                "agent_message_context_build_failed",
                extra={"error_type": type(exc).__name__},
            )

        if isinstance(resolved_resource_payloads, list):
            target_agent_card = getattr(agent, "agent_card", None)
            accepted_modes = agent_input_modes(target_agent_card)
            for payload in resolved_resource_payloads:
                if not isinstance(payload, dict):
                    continue
                text = payload.get("text")
                data = payload.get("data")
                file_payload = payload.get("file")
                ref_id = payload.get("ref_id")
                mime_type = str(payload.get("mime_type") or "").split(";", 1)[0]
                label = ref_id if isinstance(ref_id, str) and ref_id else "resource"
                metadata = {
                    **(
                        payload.get("metadata")
                        if isinstance(payload.get("metadata"), dict)
                        else {}
                    ),
                    "ref_id": label,
                    "resource_kind": payload.get("kind") or "context",
                }
                if mime_type:
                    metadata["mime_type"] = mime_type

                if isinstance(data, dict):
                    if mime_type_is_accepted(
                        mime_type or "application/json", accepted_modes
                    ):
                        agent_message.parts.append(
                            Part(root=DataPart(data=data, metadata=metadata))
                        )
                    else:
                        agent_message.parts.append(
                            Part(
                                root=TextPart(
                                    text=(
                                        f"[Selected resource: {label}]\n"
                                        + json.dumps(data, default=str)
                                    ),
                                    metadata=metadata,
                                )
                            )
                        )
                    continue

                if isinstance(file_payload, dict):
                    if mime_type_is_accepted(
                        mime_type or "application/octet-stream", accepted_modes
                    ):
                        try:
                            agent_message.parts.append(
                                Part.model_validate(
                                    {
                                        "kind": "file",
                                        "file": file_payload,
                                        "metadata": metadata,
                                    }
                                )
                            )
                        except Exception:
                            logger.warning(
                                "Skipping invalid selected artifact file part %s",
                                label,
                                exc_info=True,
                            )
                    continue

                if not isinstance(text, str) or not text.strip():
                    continue
                if mime_type == "application/json" and mime_type_is_accepted(
                    mime_type, accepted_modes
                ):
                    try:
                        parsed_data = json.loads(text)
                    except (TypeError, ValueError):
                        parsed_data = None
                    if isinstance(parsed_data, dict):
                        agent_message.parts.append(
                            Part(
                                root=DataPart(
                                    data=parsed_data,
                                    metadata=metadata,
                                )
                            )
                        )
                        continue
                agent_message.parts.append(
                    Part(
                        root=TextPart(
                            text=f"[Selected resource: {label}]\n{text}",
                            metadata=metadata,
                        )
                    )
                )

        # Append file parts from user attachments after explicit preflight checks.
        # Trace back through agent message chain to find the originating user message.
        # In chained mention flows, later agents have related_message_id pointing to
        # a previous agent message, not the user message directly.
        # Use a visited set for cycle detection instead of a fixed hop cap so
        # arbitrarily long chains still resolve correctly.
        user_msg = None
        trace_id = message.related_message_id
        visited: set[str] = set()
        while trace_id and trace_id not in visited:
            visited.add(trace_id)
            message_info = None
            if self._message_lineage_reader is not None:
                message_info = await self._message_lineage_reader.get_message(trace_id)
            if message_info is not None and message_info.message_type == "user":
                user_msg = message_info
                break
            trace_id = message_info.parent_message_id if message_info else None

        user_attachments = []
        if user_msg and isinstance(user_msg.content, dict):
            for attachment in user_msg.content.get("attachments") or []:
                if not isinstance(attachment, dict | UserAttachment):
                    continue
                user_attachments.append(UserAttachment.model_validate(attachment))
        forwarding_policy = request.attachment_forwarding_policy
        if forwarding_policy is None:
            forwarding_policy = (
                message.extend_info.get("attachment_forwarding_policy")
                if isinstance(message.extend_info, dict)
                else None
            )
        dispatch_payload_refs = (
            message.extend_info.get("dispatch_payload_refs")
            if isinstance(message.extend_info, dict)
            else None
        )
        resolved_dispatch_payload_refs = (
            message.extend_info.get("resolved_dispatch_payload_refs")
            if isinstance(message.extend_info, dict)
            else None
        )
        if forwarding_policy == "explicit_refs_only":
            raw_attachment_refs = request.explicit_attachment_refs
            if (
                raw_attachment_refs is None
                and request.selected_attachment_refs is not None
            ):
                raw_attachment_refs = list(request.selected_attachment_refs)
            if raw_attachment_refs is None:
                ref_payload = (
                    resolved_dispatch_payload_refs
                    if isinstance(resolved_dispatch_payload_refs, dict)
                    else dispatch_payload_refs
                )
                raw_attachment_refs = (
                    ref_payload.get("attachment_refs")
                    if isinstance(ref_payload, dict)
                    else None
                )
            selected_ref_set: set[str] = set()
            for raw_ref in raw_attachment_refs or []:
                if isinstance(raw_ref, str) and raw_ref:
                    selected_ref_set.add(raw_ref)
                    continue
                if isinstance(raw_ref, dict) and raw_ref.get("ref_id"):
                    selected_ref_set.add(str(raw_ref["ref_id"]))

            def is_selected_attachment(attachment: UserAttachment) -> bool:
                return (
                    attachment.file_id in selected_ref_set
                    or f"file:{attachment.file_id}" in selected_ref_set
                )

            selected_file_ids = {
                attachment.file_id
                for attachment in user_attachments
                if is_selected_attachment(attachment)
            }
            unresolved_ref_ids = {
                ref.removeprefix("file:")
                for ref in selected_ref_set
                if ref.removeprefix("file:") not in selected_file_ids
            }
            get_room_user_messages = getattr(
                self._user_message_reader,
                "get_room_user_messages_by_room_id",
                None,
            )
            if unresolved_ref_ids and get_room_user_messages is not None:
                try:
                    room_user_messages = await get_room_user_messages(message.room_id)
                except Exception:
                    logger.warning(
                        "Failed to load prior room attachments for explicit dispatch",
                        extra={
                            "room_id": message.room_id,
                            "agent_message_id": message.message_id,
                        },
                        exc_info=True,
                    )
                    room_user_messages = []
                for room_user_message in reversed(room_user_messages):
                    message_content = getattr(
                        room_user_message,
                        "message_content",
                        None,
                    )
                    prior_attachments = getattr(
                        message_content,
                        "attachments",
                        None,
                    )
                    for prior_attachment in prior_attachments or []:
                        try:
                            attachment = (
                                prior_attachment
                                if isinstance(prior_attachment, UserAttachment)
                                else UserAttachment.model_validate(prior_attachment)
                            )
                        except Exception:
                            continue
                        if attachment.file_id not in unresolved_ref_ids:
                            continue
                        user_attachments.append(attachment)
                        unresolved_ref_ids.remove(attachment.file_id)
                    if not unresolved_ref_ids:
                        break
            user_attachments = [
                attachment
                for attachment in user_attachments
                if is_selected_attachment(attachment)
            ]
        if user_attachments:
            agent_card_obj = getattr(agent, "agent_card", None)
            if agent_card_obj is None:
                agent_obj = await self._agent_room_reader.get_agent_by_agent_id(
                    agent_id
                )
                agent_card_obj = agent_obj.agent_card if agent_obj else None
            if agent_card_obj is None:
                failure = AttachmentPreflightFailure(
                    code="agent_card_unavailable",
                    message="Agent card unavailable for attachment preflight.",
                    file_names=tuple(
                        attachment.file_name for attachment in user_attachments
                    ),
                )
                if message.extend_info is None or not isinstance(
                    message.extend_info, dict
                ):
                    message.extend_info = {}
                public_failure = _public_attachment_preflight_failure(failure)
                message.extend_info["attachment_preflight_failure"] = public_failure
                return RoomCenterAgentMessageResponse(
                    message_id=message.message_id,
                    message=message,
                    a2a_message=None,
                    success=False,
                    error=public_failure["message"],
                    status_code=422,
                )

            if forwarding_policy == "compatible_only":
                accepted_modes = agent_input_modes(agent_card_obj)
                supported_attachments: list[UserAttachment] = []
                for attachment in user_attachments:
                    if mime_type_is_accepted(attachment.mime_type, accepted_modes):
                        supported_attachments.append(attachment)
                user_attachments = supported_attachments

            file_parts = await self._build_message_parts(
                "",
                user_attachments,
                agent_card_obj,
                context=AttachmentDispatchContext(
                    room_id=message.room_id,
                    message_id=message.message_id,
                    agent_id=agent_id,
                ),
            )
            if isinstance(file_parts, AttachmentPreflightFailure):
                if message.extend_info is None or not isinstance(
                    message.extend_info, dict
                ):
                    message.extend_info = {}
                public_failure = _public_attachment_preflight_failure(file_parts)
                message.extend_info["attachment_preflight_failure"] = public_failure
                return RoomCenterAgentMessageResponse(
                    message_id=message.message_id,
                    message=message,
                    a2a_message=None,
                    success=False,
                    error=public_failure["message"],
                    status_code=422,
                )
            for p in file_parts:
                if not isinstance(p.root, TextPart):
                    agent_message.parts.append(p)

        # Return the prepared message without sending
        # The execution path will handle the actual sending with streaming support
        return RoomCenterAgentMessageResponse(
            message_id=message.message_id,
            message=message,
            a2a_message=agent_message,  # Return the prepared A2A message
            success=True,
            error=None,
            status_code=200,
        )
