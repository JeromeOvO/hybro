from fastapi import APIRouter

from api_gateway.registry import include_owned_router


def compat_router(source: APIRouter, owner: str) -> APIRouter:
    router = APIRouter()
    include_owned_router(router, source, owner=owner)
    return router
