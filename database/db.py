from fastapi import FastAPI
from motor.motor_asyncio import (
    AsyncIOMotorClient,
    AsyncIOMotorDatabase,
)

from config.settings import settings

client: AsyncIOMotorClient = None
database: AsyncIOMotorDatabase = None

async def init_db(app: FastAPI):
    """Initialize module-level connection"""
    global client, database
    client = AsyncIOMotorClient(
    host=settings.mongodb_host,
    port=settings.mongodb_port,
    username=settings.mongodb_username,
    password=settings.mongodb_password,
    authSource="admin",  # database to check credentials against
    maxPoolSize=100,
    minPoolSize=5,
    serverSelectionTimeoutMS=3000,
)
    database = client[settings.mongodb_db_name]

async def close_db(app: FastAPI):
    """Close module-level connection"""
    global client
    if client:
        client.close()

async def get_db() -> AsyncIOMotorDatabase:
    """Return module-level database"""
    return database