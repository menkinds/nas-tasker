import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .dispatch import Dispatcher, current_call


def build_api_router(dispatcher: Dispatcher) -> APIRouter:
    router = APIRouter()

    @router.post("/api/{tool}")
    async def call_tool(tool: str, request: Request) -> JSONResponse:
        raw = await request.body()
        try:
            args = json.loads(raw) if raw else {}
        except ValueError:
            args = None  # the dispatcher rejects non-objects with INVALID_INPUT
        status, payload = await dispatcher.call(tool, args, current_call.get())
        return JSONResponse(payload, status_code=status, headers={"cache-control": "no-store"})

    return router
