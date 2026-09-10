"""WebSocket endpoint streaming live state changes."""

from __future__ import annotations

import asyncio
import contextlib
import secrets

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.system import SmartHomeSystem


async def _wait_for_disconnect(websocket: WebSocket) -> None:
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return


def create_ws_router(system: SmartHomeSystem, token: str) -> APIRouter:
    router = APIRouter()

    @router.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        provided = websocket.query_params.get("token", "")
        if not token or not secrets.compare_digest(provided, token):
            await websocket.close(code=1008)
            return

        await websocket.accept()
        queue = system.store.subscribe()
        get_task = asyncio.create_task(queue.get())
        disconnect_task = asyncio.create_task(_wait_for_disconnect(websocket))
        shutdown_task = asyncio.create_task(system.shutdown_event.wait())
        try:
            await websocket.send_json(
                {
                    "type": "snapshot",
                    "entities": [entity.to_dict() for entity in system.store.all()],
                }
            )
            while True:
                done, _ = await asyncio.wait(
                    {get_task, disconnect_task, shutdown_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if shutdown_task in done:
                    await websocket.close(code=1001)
                    break
                if disconnect_task in done:
                    break
                payload = get_task.result()
                await websocket.send_json(payload)
                get_task = asyncio.create_task(queue.get())
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            system.store.unsubscribe(queue)
            for task in (get_task, disconnect_task, shutdown_task):
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.gather(
                    get_task, disconnect_task, shutdown_task, return_exceptions=True
                )

    return router
