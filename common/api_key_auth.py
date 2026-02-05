"""
API Key Authentication for Discovery API

Validates X-API-Key header for external API access.
Keys are hashed with SHA-256 and validated against MongoDB.
"""

import hashlib

from fastapi import HTTPException, Request, status
from loguru import logger

from database.mongodb import mongodb
from models.api_key import APIKey


def hash_api_key(api_key: str) -> str:
    """
    Hash an API key using SHA-256.
    
    Args:
        api_key: The plaintext API key
        
    Returns:
        str: The SHA-256 hash (hex digest)
    """
    return hashlib.sha256(api_key.encode()).hexdigest()


async def validate_api_key(api_key: str) -> APIKey:
    """
    Validate an API key against MongoDB.
    
    Args:
        api_key: The plaintext API key from the request
        
    Returns:
        APIKey: The validated API key model
        
    Raises:
        HTTPException: If the key is invalid, inactive, or not found
    """
    # Hash the provided key
    key_hash = hash_api_key(api_key)
    
    # Look up in MongoDB
    api_key_doc = await mongodb.get_api_key_by_hash(key_hash)
    
    if not api_key_doc:
        logger.warning("API key validation failed: key not found")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "invalid_key",
                "message": "Invalid API key",
            },
        )
    
    if not api_key_doc.is_active:
        logger.warning(f"API key validation failed: key {api_key_doc.key_id} is inactive")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "key_inactive",
                "message": "API key is inactive",
            },
        )
    
    # Update usage statistics 
    try:
        await mongodb.update_api_key_usage(key_hash)
    except Exception as e:
        # Log but don't fail the request
        logger.warning(f"Failed to update API key usage: {e}")
    
    return api_key_doc


async def get_api_key(request: Request) -> APIKey:
    """
    FastAPI dependency to extract and validate API key from request headers.
    
    Extracts the X-API-Key header, hashes it, and validates against MongoDB.
    
    Usage:
        @router.post("/endpoint")
        async def endpoint(api_key: APIKey = Depends(get_api_key)):
            # api_key is the validated APIKey model
            ...
    
    Args:
        request: The FastAPI Request object
        
    Returns:
        APIKey: The validated API key model
        
    Raises:
        HTTPException: If the key is missing, invalid, or inactive
    """
    # Extract API key from header
    api_key = request.headers.get("X-API-Key")
    
    if not api_key:
        logger.warning("API key validation failed: X-API-Key header missing")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "missing_key",
                "message": "X-API-Key header is required",
            },
        )
    
    # Validate the key
    return await validate_api_key(api_key)

