import asyncio

import httpx

from a2a.client import Client, ClientConfig, create_client
from a2a.helpers import get_stream_response_text, new_text_message
from a2a.types import Role, SendMessageRequest, StreamResponse


def print_welcome_message() -> None:
    print("Welcome to the generic A2A client!")
    print("Please enter your query (type 'exit' to quit):")


def get_user_query() -> str:
    return input("\n> ")


async def interact_with_server(client: Client) -> None:
    while True:
        user_input = get_user_query()
        if user_input.lower() == "exit":
            print("bye!~")
            break

        request = SendMessageRequest(
            message=new_text_message(text=user_input, role=Role.ROLE_USER),
        )

        try:
            async for chunk in client.send_message(request):
                print(get_response_text(chunk), end="", flush=True)
                await asyncio.sleep(0.1)
        except Exception as e:
            print(f"An error occurred: {e}")


def get_response_text(chunk: StreamResponse) -> str:
    """Text carried by one stream frame.

    Status updates echo the text already delivered as an artifact, so they are
    skipped to avoid printing the answer twice.
    """
    if chunk.WhichOneof("payload") == "status_update":
        return ""
    return get_stream_response_text(chunk, delimiter="")


async def main() -> None:
    print_welcome_message()
    async with httpx.AsyncClient() as httpx_client:
        client = await create_client(
            "http://localhost:10001",
            ClientConfig(streaming=True, httpx_client=httpx_client),
        )
        try:
            await interact_with_server(client)
        finally:
            await client.close()


if __name__ == "__main__":
    asyncio.run(main())
