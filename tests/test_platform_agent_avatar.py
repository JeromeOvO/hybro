import pytest


class FakeObjectStorage:
    def __init__(self) -> None:
        self.uploads: list[dict] = []
        self.public_url_keys: list[str] = []

    async def upload_file(
        self,
        *,
        file_data,
        s3_key,
        content_type,
        content_length=None,
    ):
        self.uploads.append(
            {
                "file_data": file_data,
                "s3_key": s3_key,
                "content_type": content_type,
                "content_length": content_length,
            }
        )
        return s3_key

    def get_public_url(self, s3_key: str) -> str:
        self.public_url_keys.append(s3_key)
        return f"https://assets.example/{s3_key}"


class FakeAgentRepository:
    def __init__(self) -> None:
        self.updates: list[tuple[str, dict]] = []

    async def update(self, agent_id: str, updates: dict):
        self.updates.append((agent_id, updates))
        return None


@pytest.mark.asyncio
async def test_platform_agent_avatar_manager_stores_raw_bytes_and_public_url():
    from platform_module import PlatformAgentAvatarManager

    storage = FakeObjectStorage()
    repository = FakeAgentRepository()
    manager = PlatformAgentAvatarManager(storage, repository)
    expected_url = "https://assets.example/agent-avatars/agent-1/avatar.png"

    icon_url = await manager.store_avatar(
        agent_id="agent-1",
        s3_key="agent-avatars/agent-1/avatar.png",
        content=b"png-bytes",
        content_type="image/png",
    )

    assert storage.uploads == [
        {
            "file_data": b"png-bytes",
            "s3_key": "agent-avatars/agent-1/avatar.png",
            "content_type": "image/png",
            "content_length": len(b"png-bytes"),
        }
    ]
    assert storage.public_url_keys == ["agent-avatars/agent-1/avatar.png"]
    assert repository.updates == [
        (
            "agent-1",
            {"agent_card.iconUrl": expected_url},
        )
    ]
    assert icon_url == expected_url
