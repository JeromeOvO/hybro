#!/usr/bin/env python3
"""
API Key Generator Script

Generates secure API keys for the Discovery API.
Keys are generated with format: hybro_ + 32 random characters
The plaintext key is displayed once - user must save it.
Only the SHA-256 hash is stored in MongoDB.

Usage:
    python scripts/generate_api_key.py --user-id USER_ID --name "Key Name"
    
    # Or interactively:
    python scripts/generate_api_key.py
"""

import argparse
import asyncio
import hashlib
import secrets
import sys
import uuid
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Try to import dotenv, but make it optional
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # dotenv not installed, but that's okay if env vars are set another way
    pass

from database.mongodb import mongodb
from models.api_key import APIKey


def generate_api_key() -> str:
    """
    Generate a secure API key with format: hybro_ + 32 random characters.
    
    Returns:
        str: The plaintext API key (display once, never store)
    """
    # Generate 32 random characters (URL-safe base64)
    random_part = secrets.token_urlsafe(24)[:32]  # 24 bytes = 32 chars in base64
    return f"hybro_{random_part}"


def hash_api_key(api_key: str) -> str:
    """
    Hash an API key using SHA-256.
    
    Args:
        api_key: The plaintext API key
        
    Returns:
        str: The SHA-256 hash (hex digest)
    """
    return hashlib.sha256(api_key.encode()).hexdigest()


async def create_api_key(user_id: str, name: str) -> tuple[str, APIKey]:
    """
    Create a new API key and store it in MongoDB.
    
    Args:
        user_id: Owner of the key
        name: Friendly name for the key
        
    Returns:
        tuple: (plaintext_key, APIKey model)
    """
    # Connect to MongoDB
    await mongodb.connect()
    
    try:
        # Generate the key
        plaintext_key = generate_api_key()
        key_hash = hash_api_key(plaintext_key)
        key_id = str(uuid.uuid4())
        
        # Create the APIKey model
        api_key = APIKey(
            key_id=key_id,
            key_hash=key_hash,
            user_id=user_id,
            name=name,
        )
        
        # Store in MongoDB
        await mongodb.add_api_key(api_key)
        
        return plaintext_key, api_key
        
    finally:
        await mongodb.close_database_connection()


async def list_api_keys(user_id: str) -> list[APIKey]:
    """
    List all API keys for a user.
    
    Args:
        user_id: The user ID
        
    Returns:
        List of APIKey instances (without hashes visible)
    """
    await mongodb.connect()
    
    try:
        keys = await mongodb.get_api_keys_by_user(user_id)
        return keys
    finally:
        await mongodb.close_database_connection()


def main():
    parser = argparse.ArgumentParser(
        description="Generate API keys for the Discovery API"
    )
    parser.add_argument(
        "--user-id",
        type=str,
        help="User ID or organization ID that owns this key",
    )
    parser.add_argument(
        "--name",
        type=str,
        help="Friendly name for the key (e.g., 'Production', 'Development')",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all keys for a user (requires --user-id)",
    )
    
    args = parser.parse_args()
    
    if args.list:
        if not args.user_id:
            print("Error: --user-id is required when using --list")
            sys.exit(1)
        
        keys = asyncio.run(list_api_keys(args.user_id))
        print(f"\nAPI Keys for user: {args.user_id}")
        print("-" * 60)
        for key in keys:
            status = "active" if key.is_active else "inactive"
            print(f"  ID: {key.key_id}")
            print(f"  Name: {key.name}")
            print(f"  Status: {status}")
            print(f"  Created: {key.created_at}")
            print(f"  Last Used: {key.last_used_at or 'Never'}")
            print(f"  Usage Count: {key.usage_count}")
            print("-" * 60)
        return
    
    # Interactive mode if arguments not provided
    user_id = args.user_id
    name = args.name
    
    if not user_id:
        user_id = input("Enter user ID or organization ID: ").strip()
        if not user_id:
            print("Error: User ID is required")
            sys.exit(1)
    
    if not name:
        name = input("Enter a name for this key (e.g., 'Production'): ").strip()
        if not name:
            name = "Default Key"
    
    # Generate the key
    print("\nGenerating API key...")
    plaintext_key, api_key = asyncio.run(create_api_key(user_id, name))
    
    print("\n" + "=" * 60)
    print("API KEY GENERATED SUCCESSFULLY")
    print("=" * 60)
    print("\n⚠️  IMPORTANT: Save this key now! It will not be shown again.\n")
    print(f"API Key: {plaintext_key}")
    print(f"\nKey ID: {api_key.key_id}")
    print(f"Name: {api_key.name}")
    print(f"User ID: {api_key.user_id}")
    print(f"Created: {api_key.created_at}")
    print("\n" + "=" * 60)
    print("\nUsage example:")
    print(f'  curl -H "X-API-Key: {plaintext_key}" ...')
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()

