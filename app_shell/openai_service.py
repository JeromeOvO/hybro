import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

from common.dto import (
    AgentRoutingCandidate,
    ChatContextGenerationInput,
    ExplicitAgentMention,
    ParsedUserMessageRequest,
    RoomMemoryGenerationInput,
    RoomMessageSummary,
)
from common.types import MessageRole as Role
from common.utils.logger import get_logger
from llm_gateway.config import LLMGatewayConfig
from llm_gateway.errors import LLMModelRoutingError, LLMServiceNotBoundError
from llm_gateway.services import (
    AgentSelectionLLMService,
    DebateLLMService,
    DiscoveryLLMService,
    MessageParserLLMService,
    RoomMemoryLLMService,
    SummaryLLMService,
)
from models.agent import Agent
from models.memory import ContextData, MemoryContent
from models.room import RoomAgentMessage

logger = get_logger(__name__)


class OpenAIService:
    def __init__(self):
        self._client = None
        self._discovery_query_expansion_threshold = 5
        self._debate_rounds = 2
        self._supervisor_json_timeout_seconds = 30.0
        self._supervisor_text_timeout_seconds = 90.0
        self._supervisor_stream_timeout_seconds = 90.0
        self._agent_selection_service = None
        self._debate_service = None
        self._discovery_service = None
        self._message_parser_service = None
        self._room_memory_llm_service = None
        self._summary_service = None

    @property
    def is_bound(self) -> bool:
        return self._client is not None

    @property
    def client(self) -> Any:
        if self._client is None:
            raise LLMServiceNotBoundError("OpenAIService LLM services are not bound")
        return self._client

    @client.setter
    def client(self, value: Any) -> None:
        self._client = value

    def bind_llm_gateway(
        self,
        llm_provider: Any,
        llm_gateway_config: LLMGatewayConfig,
        *,
        discovery_query_expansion_threshold: int = 5,
        debate_rounds: int = 2,
    ) -> None:
        self.client = _GatewayOpenAICompatClient(llm_provider)
        self._discovery_query_expansion_threshold = discovery_query_expansion_threshold
        self._debate_rounds = debate_rounds
        self._supervisor_json_timeout_seconds = (
            llm_gateway_config.supervisor_json_timeout_seconds
        )
        self._supervisor_text_timeout_seconds = (
            llm_gateway_config.supervisor_text_timeout_seconds
        )
        self._supervisor_stream_timeout_seconds = (
            llm_gateway_config.supervisor_stream_timeout_seconds
        )
        self._agent_selection_service = AgentSelectionLLMService(llm_provider)
        self._debate_service = DebateLLMService(llm_provider)
        self._discovery_service = DiscoveryLLMService(
            llm_provider,
            max_expansion_words=discovery_query_expansion_threshold,
        )
        self._message_parser_service = MessageParserLLMService(llm_provider)
        self._room_memory_llm_service = RoomMemoryLLMService(llm_provider)
        self._summary_service = SummaryLLMService(llm_provider)

    def bind_debate_service(self, service: Any) -> None:
        self._debate_service = service

    async def get_embedding(
        self, text: str, target_dim: int | None = None
    ) -> list[float]:
        """Get embedding for text

        Args:
            text: The text to embed
            target_dim: Optional target dimension to resize the embedding to

        Returns:
            List of embedding values (original or resized)
        """
        response = await self.client.embeddings.create(
            input=text,
            model="embedding_model",
        )

        embedding = response.data[0].embedding

        return embedding

    async def expand_query_for_discovery(self, query: str) -> str:
        discovery_service = getattr(self, "_discovery_service", None)
        if discovery_service is None:
            raise LLMServiceNotBoundError("DiscoveryLLMService is not bound")
        return await discovery_service.expand_query_for_discovery(query)

    async def select_best_agent_for_task(
        self, meta_task_description: str, agents: list[Agent]
    ) -> str:
        """
        Use LLM to select the best agent for a child task from candidate agents

        Args:
            meta_task_description: Description of the meta task
            agents: List of candidate agents with their details

        Returns:
            ID of the selected agent
        """
        agent_selection_service = getattr(self, "_agent_selection_service", None)
        if agent_selection_service is None:
            raise LLMServiceNotBoundError("AgentSelectionLLMService is not bound")
        return await agent_selection_service.select_best_agent_for_task(
            meta_task_description,
            [_agent_to_routing_candidate(agent) for agent in agents],
        )

    async def short_debate_with_openai(
        self, original_userinput: str, other_agent_answer: str
    ) -> str:
        """
        Let OpenAI (Lead_ai) generate an updated response based on other agent's answer.
        """
        debate_service = getattr(self, "_debate_service", None)
        if debate_service is None:
            raise LLMServiceNotBoundError("DebateLLMService is not bound")
        return await debate_service.short_debate_with_openai(
            original_userinput,
            other_agent_answer,
        )

    async def long_debate_with_openai(
        self, original_userinput: str, other_agent_answer: str
    ) -> str:
        """
        Let OpenAI (Lead_ai) generate an updated response based on other agent's answer.
        """
        debate_service = getattr(self, "_debate_service", None)
        if debate_service is None:
            raise LLMServiceNotBoundError("DebateLLMService is not bound")
        return await debate_service.long_debate_with_openai(
            original_userinput,
            other_agent_answer,
        )

    async def summarize_agent_responses_stream(
        self,
        agent_responses: list[dict[str, str]],
        mode: str = "non_debate",
        user_question: str | None = None,
    ) -> AsyncIterator[str]:
        """Stream summary tokens through the focused summary service."""
        summary_service = getattr(self, "_summary_service", None)
        if summary_service is None:
            raise LLMServiceNotBoundError("SummaryLLMService is not bound")
        try:
            async for chunk in summary_service.summarize_agent_responses_stream(
                _room_message_summaries_from_dicts(agent_responses),
                mode=mode,
                user_question=user_question,
            ):
                yield chunk
        except Exception as e:
            logger.error(
                "Error in summarize_agent_responses_stream (mode=%s): %s",
                mode,
                e,
            )
            yield f"Error: {str(e)}"

    async def summarize_agent_responses(
        self,
        agent_responses: list[dict[str, str]],
        mode: str = "non_debate",
        user_question: str | None = None,
    ) -> str:
        """
        Summarize the answers from multiple AI agents into a single summary using Lead_ai.

        Args:
            agent_responses: List of dicts with 'agent_name' and 'message' keys
                Example: [{"agent_name": "Research Agent", "message": "..."}, ...]
            mode: Summary mode - "debate" or "non_debate"
                - "debate": Compares viewpoints, highlights agreements/disagreements
                - "non_debate": Combines contributions into a unified response
            user_question: The original user question/request, used to calibrate
                the summary style (e.g. introductions vs. task responses)

        Returns:
            Summary text string
        """
        parts: list[str] = []
        async for token in self.summarize_agent_responses_stream(
            agent_responses, mode=mode, user_question=user_question
        ):
            parts.append(token)
        return "".join(parts)

    # Backwards-compatible aliases
    async def summarize_debate_answer(
        self, agent_responses: list[dict[str, str]]
    ) -> str:
        """Alias for summarize_agent_responses with mode='debate'."""
        return await self.summarize_agent_responses(agent_responses, mode="debate")

    async def summarize_non_debate_answer(
        self, agent_responses: list[dict[str, str]]
    ) -> str:
        """Alias for summarize_agent_responses with mode='non_debate'."""
        return await self.summarize_agent_responses(agent_responses, mode="non_debate")

    async def generate_chat_context(
        self, user_input: str, agent_response: str, context_data: ContextData
    ) -> str:
        room_memory_service = getattr(self, "_room_memory_llm_service", None)
        if room_memory_service is None:
            raise LLMServiceNotBoundError("RoomMemoryLLMService is not bound")
        existing_context = (
            context_data.context_content
            if context_data and context_data.context_content
            else None
        )
        try:
            context_summary = await room_memory_service.generate_chat_context(
                ChatContextGenerationInput(
                    user_input=user_input,
                    agent_response=agent_response,
                    existing_context=existing_context,
                )
            )
            if not context_summary:
                context_summary = f"User discussed: {user_input}. Agent provided: {agent_response[:200]}..."
            return context_summary
        except LLMModelRoutingError:
            raise
        except Exception as e:
            print(f"Error in generate_chat_context: {str(e)}")
            fallback = f"{existing_context or ''}\n\nLatest: User: {user_input} | Agent: {agent_response[:200]}..."
            return fallback.strip()

    async def generate_room_memory_content(
        self, messages: list[RoomAgentMessage], room_memory_content: MemoryContent
    ) -> str:
        room_memory_service = getattr(self, "_room_memory_llm_service", None)
        if room_memory_service is None:
            raise LLMServiceNotBoundError("RoomMemoryLLMService is not bound")

        message_summaries: list[RoomMessageSummary] = []
        for msg in messages:
            try:
                agent_content = ""
                if (
                    msg.message_content
                    and msg.message_content.message_task
                    and msg.message_content.message_task.history
                ):
                    # Get the latest agent message from history
                    agent_messages = [
                        m
                        for m in msg.message_content.message_task.history
                        if m.role == Role.AGENT
                    ]

                    if agent_messages:
                        latest_message = agent_messages[-1]
                        if latest_message.parts and len(latest_message.parts) > 0:
                            # Extract text from the first part
                            agent_content = latest_message.parts[0].root.text

                if agent_content:
                    agent_id = getattr(msg, "agent_id", "unknown-agent")
                    agent_name = getattr(msg, "agent_name", None) or str(agent_id)
                    message_summaries.append(
                        RoomMessageSummary(
                            agent_id=agent_id,
                            agent_name=agent_name,
                            message=agent_content[:500],
                        )
                    )
            except Exception as e:
                print(f"Error processing message {msg.message_id}: {str(e)}")
                continue

        existing_memory = (
            room_memory_content.memory_text
            if room_memory_content and room_memory_content.memory_text
            else None
        )
        try:
            memory_content = await room_memory_service.generate_room_memory_content(
                RoomMemoryGenerationInput(
                    messages=message_summaries,
                    existing_memory=existing_memory,
                )
            )
            if not memory_content:
                fallback_content = f"{existing_memory or ''}\n\nUpdated with {len(message_summaries)} new agent messages."
                return fallback_content.strip()
            return memory_content
        except LLMModelRoutingError:
            raise
        except Exception as e:
            print(f"Error in generate_room_memory_content: {str(e)}")
            fallback = f"{existing_memory or ''}\n\nProcessed {len(message_summaries)} agent messages."
            return fallback.strip()

    async def parse_user_message_by_llm(
        self,
        message_text: str,
        selected_agent_set: dict = None,
        is_debate_mode: bool = False,
        auto_assign_agents: bool = False,
        agents: list[Agent] = None,
        conversation_context: str | None = None,
        explicit_mentions: list[dict] | None = None,
    ) -> dict:
        """Compatibility wrapper around ``MessageParserLLMService``."""
        parser = getattr(self, "_message_parser_service", None)
        if parser is None:
            raise LLMServiceNotBoundError("MessageParserLLMService is not bound")

        request = ParsedUserMessageRequest(
            message_text=message_text,
            selected_agents=selected_agent_set or {},
            is_debate_mode=is_debate_mode,
            auto_assign_agents=auto_assign_agents,
            agents=[
                _agent_to_routing_candidate(agent)
                for agent in (agents or [])
            ],
            conversation_context=conversation_context,
            explicit_mentions=[
                ExplicitAgentMention(
                    agent_id=str(mention.get("agent_id", "")),
                    agent_name=str(mention.get("agent_name", "")),
                    mention_text=mention.get("mention_text"),
                )
                for mention in (explicit_mentions or [])
                if isinstance(mention, dict)
            ],
            debate_rounds=getattr(self, "_debate_rounds", 2) or 2,
        )
        return await parser.parse_user_message(request)

    # =========================================================================
    # Supervisor LLM Methods
    # =========================================================================

    async def call_supervisor_llm_json(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
    ) -> dict:
        """Call the Supervisor LLM and return JSON response.

        Uses a fast model (gpt-4o-mini) for low latency.

        Args:
            system_prompt: The system prompt for the LLM
            user_prompt: The user prompt for the LLM
            model: Optional model override

        Returns:
            Parsed JSON response as dict

        Raises:
            ValueError: If response is empty or invalid JSON
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        llm_model = model or "supervisor_model"

        response = await self.client.chat.completions.create(
            model=llm_model,
            messages=messages,
            response_format={"type": "json_object"},
            timeout=getattr(self, "_supervisor_json_timeout_seconds", 30.0),
        )

        content = response.choices[0].message.content
        if not content:
            raise ValueError("Empty response from Supervisor LLM")

        return json.loads(content)

    async def call_supervisor_llm_text(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
    ) -> str:
        """Call the Supervisor LLM and return text response (for synthesis).

        Args:
            system_prompt: The system prompt for the LLM
            user_prompt: The user prompt for the LLM
            model: Optional model override

        Returns:
            Text response string

        Raises:
            ValueError: If response is empty
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        llm_model = model or "supervisor_model"

        response = await self.client.chat.completions.create(
            model=llm_model,
            messages=messages,
            timeout=getattr(self, "_supervisor_text_timeout_seconds", 90.0),
        )

        content = response.choices[0].message.content
        if not content:
            raise ValueError("Empty response from Supervisor LLM")

        return content

    async def call_supervisor_llm_text_stream(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
    ) -> AsyncIterator[str]:
        """Stream Supervisor LLM text deltas (for synthesis)."""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        llm_model = model or "supervisor_model"
        stream = await self.client.chat.completions.create(
            model=llm_model,
            messages=messages,
            timeout=getattr(self, "_supervisor_stream_timeout_seconds", 90.0),
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta


def _room_message_summaries_from_dicts(
    agent_responses: list[dict[str, str] | RoomMessageSummary],
) -> list[RoomMessageSummary]:
    summaries: list[RoomMessageSummary] = []
    for item in agent_responses:
        if isinstance(item, RoomMessageSummary):
            summaries.append(item)
            continue
        summaries.append(
            RoomMessageSummary(
                agent_id=item.get("agent_id"),
                agent_name=item.get("agent_name") or "Unknown Agent",
                message=item.get("message") or "",
            )
        )
    return summaries


def _agent_to_routing_candidate(agent: Agent) -> AgentRoutingCandidate:
    card = agent.agent_card
    capabilities = card.capabilities if isinstance(card.capabilities, dict) else {}
    skills = []
    if isinstance(card.skills, list):
        for skill in card.skills:
            if isinstance(skill, dict):
                skills.append(str(skill.get("name") or skill.get("id") or "Unknown"))
            else:
                skills.append(str(getattr(skill, "name", None) or skill))
    return AgentRoutingCandidate(
        agent_id=str(agent.agent_id),
        name=str(card.name),
        description=str(card.description or ""),
        capabilities=capabilities,
        skills=skills,
    )


openai_service = OpenAIService()


class _GatewayOpenAICompatClient:
    def __init__(self, llm_provider: Any) -> None:
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(
                create=_GatewayOpenAIChatCompletions(llm_provider).create
            )
        )
        self.embeddings = SimpleNamespace(
            create=_GatewayOpenAIEmbeddings(llm_provider).create
        )
        self.responses = SimpleNamespace(create=_GatewayOpenAIResponses(llm_provider).create)


class _GatewayOpenAIChatCompletions:
    def __init__(self, llm_provider: Any) -> None:
        self._llm_provider = llm_provider

    async def create(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        stream: bool = False,
        response_format: dict[str, Any] | None = None,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> Any:
        kwargs.pop("timeout_seconds", None)
        if stream:
            stream_chunks = (
                self._llm_provider.generate_stream_with_provider(
                    messages,
                    model=model,
                    provider="openai",
                    timeout_seconds=timeout,
                    **kwargs,
                )
                if _requires_openai_provider_hint(model)
                else self._llm_provider.generate_stream(
                    messages,
                    model=model,
                    timeout_seconds=timeout,
                    **kwargs,
                )
            )
            return _OpenAICompatStream(stream_chunks)
        if response_format and response_format.get("type") == "json_object":
            if _requires_openai_provider_hint(model):
                response = await self._llm_provider.generate_structured_with_provider(
                    messages,
                    model=model,
                    provider="openai",
                    schema=None,
                    json_mode=True,
                    timeout_seconds=timeout,
                    **kwargs,
                )
            else:
                response = await self._llm_provider.generate_structured(
                    messages,
                    model=model,
                    schema=None,
                    json_mode=True,
                    timeout_seconds=timeout,
                    **kwargs,
                )
            content = json.dumps(response.data)
            return _chat_completion(content, response.model)
        if _requires_openai_provider_hint(model):
            response = await self._llm_provider.generate_with_provider(
                messages,
                model=model,
                provider="openai",
                timeout_seconds=timeout,
                **kwargs,
            )
        else:
            response = await self._llm_provider.generate(
                messages,
                model=model,
                timeout_seconds=timeout,
                **kwargs,
            )
        return _chat_completion(response.content, response.model)


class _GatewayOpenAIEmbeddings:
    def __init__(self, llm_provider: Any) -> None:
        self._llm_provider = llm_provider

    async def create(self, *, input: str | list[str], model: str, **kwargs: Any) -> Any:
        texts = [input] if isinstance(input, str) else input
        embeddings = await self._llm_provider.embed_batch(texts, model=model)
        return SimpleNamespace(
            data=[
                SimpleNamespace(embedding=embedding)
                for embedding in embeddings
            ]
        )


class _GatewayOpenAIResponses:
    def __init__(self, llm_provider: Any) -> None:
        self._llm_provider = llm_provider

    async def create(
        self,
        *,
        model: str,
        input: list[dict[str, Any]],
        **kwargs: Any,
    ) -> Any:
        kwargs.pop("reasoning", None)
        if _requires_openai_provider_hint(model):
            response = await self._llm_provider.generate_with_provider(
                input,
                model=model,
                provider="openai",
                **kwargs,
            )
        else:
            response = await self._llm_provider.generate(input, model=model, **kwargs)
        return SimpleNamespace(output_text=response.content)


class _OpenAICompatStream:
    def __init__(self, chunks: AsyncIterator[str]) -> None:
        self._chunks = chunks

    def __aiter__(self) -> "_OpenAICompatStream":
        return self

    async def __anext__(self) -> Any:
        chunk = await self._chunks.__anext__()
        return SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content=chunk))]
        )


def _chat_completion(content: str, model: str) -> Any:
    return SimpleNamespace(
        model=model,
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
    )


def _requires_openai_provider_hint(model: str) -> bool:
    return model not in {
        "lead_ai_model",
        "classifier_ai_model",
        "supervisor_model",
        "context_memory_legacy_json_model",
    }
