"""Image generation agent using the OpenAI Images API.

Generates images directly from text prompts. No LLM agent loop needed for
maximum reliability - the image generation model is called directly.
"""

import base64
import io
import logging
import os
import re
from pathlib import Path

from uuid import uuid4

from in_memory_cache import InMemoryCache
from openai import OpenAI
from pydantic import BaseModel

try:
    from load_repo_env import load_repo_env
except ImportError:  # Host run: module lives in default_agents/
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from load_repo_env import load_repo_env

load_repo_env(start=Path(__file__))

logger = logging.getLogger(__name__)

# Pattern to extract the actual user request from backend-wrapped messages.
# The backend wraps prompts in conversation context like:
#   [Current request]\nUser: <actual prompt>\n\nYou are Image Generator Agent...
_CURRENT_REQUEST_PATTERN = re.compile(
    r'\[Current request\]\s*User:\s*(.+?)(?:\n\nYou are|\Z)',
    re.DOTALL,
)


def _generate_image_bytes(prompt: str, ref_image_bytes: bytes | None) -> bytes:
    """Generate (or edit) an image with the OpenAI Images API and return PNG bytes."""
    client = OpenAI()  # reads OPENAI_API_KEY from env
    model = os.getenv("IMAGE_MODEL", "gpt-image-1")
    size = os.getenv("IMAGE_SIZE", "1024x1024")

    if ref_image_bytes:
        ref = io.BytesIO(ref_image_bytes)
        ref.name = "reference.png"
        response = client.images.edit(
            model=model,
            image=ref,
            prompt=prompt,
            size=size,
        )
    else:
        response = client.images.generate(
            model=model,
            prompt=prompt,
            size=size,
        )

    b64 = response.data[0].b64_json
    if not b64:
        raise RuntimeError("Image generation returned no image data")
    return base64.b64decode(b64)


class Imagedata(BaseModel):
    """Represents image data.

    Attributes:
      id: Unique identifier for the image.
      name: Name of the image.
      mime_type: MIME type of the image.
      bytes: Base64 encoded image data.
      error: Error message if there was an issue with the image.
    """

    id: str | None = None
    name: str | None = None
    mime_type: str | None = None
    bytes: str | None = None
    error: str | None = None


class ImageGenerationAgent:
    """Agent that generates images based on user prompts."""

    SUPPORTED_CONTENT_TYPES = ['text', 'text/plain', 'image/png']

    def _extract_clean_prompt(self, query: str) -> str:
        """Extract the actual image request from a backend-wrapped message."""
        match = _CURRENT_REQUEST_PATTERN.search(query)
        if match:
            return match.group(1).strip()
        # Fallback: truncate at system instructions if present
        you_are_idx = query.find('You are')
        if you_are_idx > 0:
            cleaned = query[:you_are_idx].strip()
            if cleaned:
                logger.info('Prompt extracted via fallback (truncated at system instructions)')
                return cleaned
        # Strip leading "User:" prefix if present
        if query.lstrip().startswith('User:'):
            return query.lstrip().removeprefix('User:').strip()
        return query

    def extract_artifact_file_id(self, query):
        try:
            pattern = r'(?:id|artifact-file-id)\s+([0-9a-f]{32})'
            match = re.search(pattern, query)

            if match:
                return match.group(1)
            return None
        except Exception:
            return None

    def _get_ref_image_bytes(self, cache, session_id, artifact_file_id):
        """Get reference image bytes from cache for image editing."""
        if not artifact_file_id:
            return None
        try:
            session_image_data = cache.get(session_id)
            if session_image_data is None:
                return None
            ref_image_data = session_image_data.get(artifact_file_id)
            if ref_image_data is None:
                logger.warning(
                    'Reference image %s not found in session %s',
                    artifact_file_id,
                    session_id,
                )
                return None
            logger.info('Found reference image %s in session', artifact_file_id)
            return base64.b64decode(ref_image_data.bytes)
        except Exception:
            logger.exception('Error retrieving reference image')
            return None

    def invoke(self, query, session_id) -> str:
        """Generate an image and return the cached image key."""
        artifact_file_id = self.extract_artifact_file_id(query)
        clean_prompt = self._extract_clean_prompt(query)

        logger.info(f'Generating image for prompt: {clean_prompt[:100]}')
        print(f'Generating image for prompt: {clean_prompt[:100]}')

        cache = InMemoryCache()
        ref_image_bytes = self._get_ref_image_bytes(
            cache, session_id, artifact_file_id
        )

        image_bytes = _generate_image_bytes(clean_prompt, ref_image_bytes)

        data = Imagedata(
            bytes=base64.b64encode(image_bytes).decode('utf-8'),
            mime_type='image/png',
            name='generated_image.png',
            id=uuid4().hex,
        )
        session_data = cache.get(session_id)
        if session_data is None:
            session_data = {}
        session_data[data.id] = data
        cache.set(session_id, session_data, ttl=3600)

        return data.id

    def get_image_data(self, session_id: str, image_key: str) -> Imagedata:
        """Return Imagedata given a key. This is a helper method from the agent."""
        cache = InMemoryCache()
        session_data = cache.get(session_id)
        try:
            if session_data is None:
                raise KeyError(f'No session data for {session_id}')
            return session_data[image_key]
        except (KeyError, TypeError):
            logger.error('Error generating image')
            return Imagedata(error='Error generating image, please try again.')
