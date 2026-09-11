"""Single-attempt OpenAI image generation/editing inside the gateway."""

import asyncio
from collections.abc import AsyncIterator

import httpx
from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    DefaultAsyncHttpxClient,
)
from openai.types import Image as OpenAIImage
from openai.types import ImagesResponse

from llm_gateway.image_types import (
    MAX_IMAGE_RESPONSE_JSON_BYTES,
    GatewayImage,
    GatewayImageRequest,
    GatewayImageResult,
    ImageContractError,
)

MAX_IMAGE_ERROR_BYTES = 64 * 1024


class OpenAIImageProvider:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ImageContractError("Image provider API key is required")
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url or "https://api.openai.com/v1",
            max_retries=0,
            http_client=DefaultAsyncHttpxClient(
                transport=transport,
                trust_env=False,
                follow_redirects=False,
                event_hooks={"response": [bound_image_response]},
            ),
        )

    async def aclose(self) -> None:
        await self._client.close()

    async def generate_image_once(
        self, request: GatewayImageRequest, *, model: str
    ) -> GatewayImageResult:
        if model != "gpt-image-1":
            raise ImageContractError("Unsupported image model")
        options = dict(
            model=model,
            prompt=request.prompt,
            n=1,
            size=request.size,
            quality=request.quality,
            output_format="png",
            stream=False,
            timeout=request.timeout_seconds,
            extra_headers={"Accept-Encoding": "identity"},
        )
        failure = None
        try:
            async with asyncio.timeout(request.timeout_seconds):
                if request.reference_images:
                    files = [
                        (
                            f"reference-{index}.{image.mime_type.split('/')[1]}",
                            image.decoded_bytes(),
                            image.mime_type,
                        )
                        for index, image in enumerate(request.reference_images)
                    ]
                    response = await self._client.images.edit(image=files, **options)
                else:
                    response = await self._client.images.generate(**options)
                return _image_result(response, request.client_request_id)
        except APITimeoutError:
            failure = TimeoutError("Provider request timed out")
        except APIConnectionError as exc:
            if isinstance(exc.__cause__, ImageResponseBoundaryError):
                failure = ImageResponseBoundaryError(exc.__cause__.status_code)
            else:
                failure = ConnectionError("Provider connection failed")
        if failure is not None:
            raise failure
        raise AssertionError("unreachable")


def _image_result(
    response: ImagesResponse, correlation: str | None
) -> GatewayImageResult:
    if (
        not isinstance(response, ImagesResponse)
        or not isinstance(response.data, list)
        or len(response.data) != 1
        or response.output_format not in (None, "png")
    ):
        raise ImageContractError("Provider must return exactly one PNG image")
    image = response.data[0]
    if (
        not isinstance(image, OpenAIImage)
        or image.url is not None
        or not isinstance(image.b64_json, str)
    ):
        raise ImageContractError("Provider must return base64 image content")
    return GatewayImageResult(
        image=GatewayImage(mime_type="image/png", data_base64=image.b64_json),
        client_request_id=correlation,
    )


class ImageResponseBoundaryError(ImageContractError):
    def __init__(self, status_code: int) -> None:
        super().__init__("Invalid or oversized image response")
        self.status_code = status_code


class _BoundedImageBody(httpx.AsyncByteStream):
    def __init__(self, stream: httpx.AsyncByteStream, limit: int, status: int) -> None:
        self.stream = stream
        self.limit = limit
        self.status = status

    async def __aiter__(self) -> AsyncIterator[bytes]:
        size = 0
        try:
            async for chunk in self.stream:
                size += len(chunk)
                if size > self.limit:
                    raise ImageResponseBoundaryError(self.status)
                yield chunk
        finally:
            await self.aclose()

    async def aclose(self) -> None:
        await self.stream.aclose()


async def bound_image_response(response: httpx.Response) -> None:
    if not response.request.url.path.endswith(("/images/generations", "/images/edits")):
        return
    limit = (
        MAX_IMAGE_RESPONSE_JSON_BYTES if response.is_success else MAX_IMAGE_ERROR_BYTES
    )
    encoding = response.headers.get("content-encoding", "identity").strip().lower()
    length = response.headers.get("content-length")
    # HTTPX aread() reuses buffered content without iterating response.stream.
    # Reject consumed injected responses rather than claiming a streaming bound.
    invalid = (
        encoding != "identity" or response.is_redirect or response.is_stream_consumed
    )
    if length is not None:
        try:
            invalid |= not 0 <= int(length) <= limit
        except ValueError:
            invalid = True
    if invalid:
        await response.aclose()
        raise ImageResponseBoundaryError(response.status_code)
    # Response.stream is HTTPX's public byte-stream interface. Wrap raw bytes
    # before HTTPX decompression or the SDK's eager error-body read. No private
    # response fields, SDK monkey patches, or second HTTP client are involved.
    response.stream = _BoundedImageBody(response.stream, limit, response.status_code)
