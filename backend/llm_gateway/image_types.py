"""Immutable JSON/base64 image contracts, independent of HTTP and Provider SDKs."""

from __future__ import annotations

import base64
import binascii
import io
import json
import warnings
from contextlib import closing
from typing import Literal, Self

import imagesize
from PIL import Image
from pydantic import ConfigDict, Field, model_validator

from llm_gateway.turn_types import GatewayContract

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_BASE64_BYTES = 12 * 1024 * 1024
MAX_REFERENCE_BYTES = 16 * 1024 * 1024
MAX_REFERENCE_BASE64_BYTES = 24 * 1024 * 1024
MAX_IMAGE_REQUEST_JSON_BYTES = 25 * 1024 * 1024
MAX_IMAGE_RESPONSE_JSON_BYTES = 13 * 1024 * 1024
MAX_REFERENCE_IMAGES = 4
MAX_IMAGE_PIXELS = 16_777_216
MAX_IMAGE_DIMENSION = 8192

ImageMime = Literal["image/png", "image/jpeg", "image/webp"]
_FORMATS = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}


class ImageContractError(ValueError):
    """Fixed-message validation error; never includes input or Provider content."""


class GatewayImage(GatewayContract):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    mime_type: ImageMime
    data_base64: str = Field(
        min_length=4, max_length=MAX_IMAGE_BASE64_BYTES, repr=False
    )

    def decoded_bytes(self) -> bytes:
        """Strictly decode and fully validate one image, releasing pixel buffers."""
        try:
            decoded_size = len(self.data_base64) // 4 * 3 - (
                len(self.data_base64) - len(self.data_base64.rstrip("="))
            )
            if decoded_size > MAX_IMAGE_BYTES:
                raise ValueError
            data = base64.b64decode(self.data_base64, validate=True)
            if not data or len(data) > MAX_IMAGE_BYTES:
                raise ValueError
            # Reject noncanonical padding/trailing bits as well as whitespace/URLs.
            if base64.b64encode(data).decode("ascii") != self.data_base64:
                raise ValueError
            dimensions = (
                _webp_preflight(data) if self.mime_type == "image/webp" else None
            )
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                _check_image(data, self.mime_type, dimensions, verify=True)
                _check_image(data, self.mime_type, dimensions, verify=False)
            return data
        except (
            ValueError,
            OSError,
            SyntaxError,
            EOFError,
            binascii.Error,
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
        ):
            pass
        raise ImageContractError("Invalid or oversized image content")


def _check_image(
    data: bytes, mime: ImageMime, dimensions: tuple[int, int] | None, *, verify: bool
) -> None:
    # Pillow's image context manager only closes file pointers, not pixel cores.
    # Explicit close and this short local lifetime release the core/native decoder
    # before opening the next image (including the verification/load reopen).
    with io.BytesIO(data) as source:
        with closing(Image.open(source, formats=(_FORMATS[mime],))) as image:
            _validate_image_header(image, mime, dimensions)
            if verify:
                image.verify()
            else:
                image.load()


def _webp_preflight(data: bytes) -> tuple[int, int]:
    # Pillow's WebP open creates native canvas buffers before returning dimensions.
    # imagesize 2.0.1 reads VP8/VP8L/VP8X dimensions from the first 64 bytes without
    # native decoding. This is resource admission only, never format validation.
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise ImageContractError("Invalid WebP header")
    with io.BytesIO(data[:64]) as header:
        dimensions = imagesize.get(header, exif_rotation=False)
    _validate_dimensions(dimensions)
    return dimensions


def _validate_dimensions(dimensions: tuple[int, int]) -> None:
    width, height = dimensions
    if (
        type(width) is not int
        or type(height) is not int
        or not 0 < width <= MAX_IMAGE_DIMENSION
        or not 0 < height <= MAX_IMAGE_DIMENSION
        or width * height > MAX_IMAGE_PIXELS
    ):
        raise ImageContractError("Invalid image dimensions")


def _validate_image_header(
    image: Image.Image, mime: ImageMime, dimensions: tuple[int, int] | None
) -> None:
    _validate_dimensions(image.size)
    if (
        image.format != _FORMATS[mime]
        or (dimensions is not None and image.size != dimensions)
        or getattr(image, "n_frames", 1) != 1
    ):
        raise ImageContractError("Invalid image format or dimensions")


def _json_size(value: GatewayContract, limit: int) -> None:
    size = 0
    for chunk in json.JSONEncoder(ensure_ascii=False).iterencode(
        value.model_dump(mode="json")
    ):
        size += len(chunk.encode("utf-8"))
        if size > limit:
            raise ImageContractError("Image JSON exceeds size limit")


class GatewayImageRequest(GatewayContract):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    prompt: str = Field(min_length=1, max_length=32_000, repr=False)
    reference_images: tuple[GatewayImage, ...] = Field(
        default=(), max_length=MAX_REFERENCE_IMAGES
    )
    size: Literal["1024x1024", "1536x1024", "1024x1536", "auto"] = "1024x1024"
    quality: Literal["low", "medium", "high", "auto"] = "auto"
    timeout_seconds: float = Field(default=300, gt=0, le=300, allow_inf_nan=False)
    client_request_id: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        if not self.prompt.strip():
            raise ImageContractError("Image prompt must not be blank")
        if (
            sum(len(image.data_base64) for image in self.reference_images)
            > MAX_REFERENCE_BASE64_BYTES
        ):
            raise ImageContractError("Reference base64 exceeds aggregate limit")
        _json_size(self, MAX_IMAGE_REQUEST_JSON_BYTES)
        decoded_size = 0
        for image in self.reference_images:
            decoded_size += len(image.decoded_bytes())
            if decoded_size > MAX_REFERENCE_BYTES:
                raise ImageContractError("Reference bytes exceed aggregate limit")
        return self

    @classmethod
    def from_json(cls, payload: bytes) -> Self:
        # Future HTTP ingress must enforce this while reading, before buffering.
        if len(payload) > MAX_IMAGE_REQUEST_JSON_BYTES:
            raise ImageContractError("Image JSON exceeds size limit")
        return cls.model_validate_json(payload)


class GatewayImageResult(GatewayContract):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    image: GatewayImage
    client_request_id: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        # One output image: per-image ceilings are also aggregate output ceilings.
        _json_size(self, MAX_IMAGE_RESPONSE_JSON_BYTES)
        self.image.decoded_bytes()
        return self
