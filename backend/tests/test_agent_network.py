from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from agent.facade import AgentFacade
from agent.network import AgentNetworkService
from agent.protocols import AgentGroupStoreCompatibility
from common.errors import AppError, ConflictError, NotFoundError, ValidationError
from models.agent_group import AgentGroup
from tests.test_agent_facade import Resolver, _doc
from tests.test_agent_repository import _repo


def _network(
    docs: list[dict],
    groups: list[AgentGroup] | None = None,
    *,
    requesting_user_id: str = "owner",
):
    repository, _ = _repo(docs)
    registry = AgentFacade(
        repository=repository,
        card_resolver=Resolver(),
        id_factory=lambda: "unused",
        now=lambda: datetime.now(UTC),
    )
    groups_by_id = {group.group_id: group for group in groups or []}
    group_store = AsyncMock(spec=AgentGroupStoreCompatibility)
    group_store.get_agent_group_by_id.side_effect = groups_by_id.get
    return AgentNetworkService(
        registry, group_store, requesting_user_id=requesting_user_id
    )


@pytest.mark.asyncio
async def test_discovery_includes_all_visible_active_agents_without_truncation():
    docs = [_doc(f"private-{index}", "Private", public=False) for index in range(1001)]
    docs.extend(
        [
            _doc("public", "Public"),
            _doc("inactive", "Inactive", active=False),
            _doc("foreign", "Foreign private", public=False, provider_id="other"),
        ]
    )
    network = _network(docs)
    expected_ids = {f"private-{index}" for index in range(1001)} | {"public"}

    discovered = await network.discover()
    all_agents = await network.discover("all_agents")

    assert {agent.agent_id for agent in discovered.agents} == expected_ids
    assert {agent.agent_id for agent in all_agents.agents} == expected_ids


@pytest.mark.asyncio
async def test_saved_group_scopes_active_members_and_empty_group_never_expands():
    network = _network(
        [
            _doc("member", "Private member", public=False),
            _doc("inactive", "Inactive member", active=False),
            _doc("outside", "Outside"),
            _doc("foreign", "Foreign private", public=False, provider_id="other"),
        ],
        [
            AgentGroup(
                group_id="saved",
                name="Saved",
                type="user",
                owner_id="owner",
                agents=["member", "inactive", "removed", "foreign"],
            ),
            AgentGroup(group_id="empty", name="Empty", type="user", owner_id="owner"),
        ],
    )

    discovered = await network.discover("saved")
    empty = await network.discover("empty")

    assert [agent.agent_id for agent in discovered.agents] == ["member"]
    assert empty.agents == []
    assert (await network.resolve_target("member", "saved")).agent_id == "member"
    with pytest.raises(ConflictError):
        await network.resolve_target("outside", "saved")
    with pytest.raises(ConflictError):
        await network.resolve_target("member", "empty")


@pytest.mark.asyncio
async def test_target_resolution_distinguishes_missing_and_inactive_agents():
    network = _network(
        [
            _doc("private", "Private", public=False),
            _doc("inactive", "Inactive", active=False),
        ]
    )

    assert (await network.resolve_target("private")).agent_id == "private"
    assert (await network.resolve_target("private", "all_agents")).agent_id == "private"
    with pytest.raises(NotFoundError) as missing:
        await network.resolve_target("missing")
    assert missing.value.details["entity_id"] == "missing"
    with pytest.raises(ConflictError):
        await network.resolve_target("inactive")


@pytest.mark.asyncio
@pytest.mark.parametrize("active", [True, False])
async def test_foreign_private_target_is_indistinguishable_from_missing(active):
    network = _network(
        [_doc("foreign", "Secret", provider_id="other", public=False, active=active)]
    )

    assert (await network.discover()).agents == []
    with pytest.raises(NotFoundError) as denied:
        await network.resolve_target("foreign")
    assert denied.value.details == {"entity_type": "Agent", "entity_id": "foreign"}


@pytest.mark.asyncio
async def test_foreign_group_is_forbidden_even_with_a_public_member():
    network = _network(
        [_doc("public", "Public")],
        [
            AgentGroup(
                group_id="foreign",
                name="Foreign team",
                type="user",
                owner_id="other",
                agents=["public"],
            )
        ],
    )

    with pytest.raises(AppError) as discovery:
        await network.discover("foreign")
    assert discovery.value.code == "FORBIDDEN"
    with pytest.raises(AppError) as send:
        await network.resolve_target("public", "foreign")
    assert send.value.code == "FORBIDDEN"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("group_id", "error"),
    [("missing", NotFoundError), ("room_team", ValidationError)],
)
async def test_network_rejects_missing_and_room_scoped_groups(group_id, error):
    network = _network([_doc("agent", "Agent")])

    with pytest.raises(error):
        await network.discover(group_id)
    with pytest.raises(error):
        await network.resolve_target("agent", group_id)


@pytest.mark.asyncio
async def test_discovery_preserves_registered_card_except_credentials():
    doc = _doc("agent", "Agent")
    doc["agent_card"].update(
        {
            "authentication": {"schemes": ["Bearer"], "credentials": "secret"},
            "security": [{"bearer": []}],
            "securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}},
            "supportedInterfaces": [
                {"url": "https://agent.example/a2a", "protocolBinding": "JSONRPC"}
            ],
            "extensions": {"custom": {"supported": True}},
        }
    )
    network = _network([doc])

    discovered = await network.discover()

    expected_card = {
        **doc["agent_card"],
        "authentication": {"schemes": ["Bearer"]},
    }
    assert discovered.agents[0].agent_card == expected_card
    target = await network.resolve_target("agent")
    assert target.raw_card["authentication"]["credentials"] == "secret"
