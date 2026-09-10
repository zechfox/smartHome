"""REST API routes."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.auth import make_token_dependency
from app.modbus.client import ModbusError
from app.system import SmartHomeSystem


class SetStateRequest(BaseModel):
    state: Literal["on", "off"]


def create_api_router(system: SmartHomeSystem, token: str) -> APIRouter:
    router = APIRouter(prefix="/api")
    require_token = make_token_dependency(token)

    @router.get("/health")
    async def health() -> dict:
        return {
            "status": "ok",
            "devices": system.device_status(),
            "entity_count": len(system.store.all()),
        }

    @router.get("/entities", dependencies=[Depends(require_token)])
    async def list_entities(domain: str | None = None) -> list[dict]:
        entities = system.store.all()
        if domain is not None:
            entities = [entity for entity in entities if entity.domain == domain]
        return [entity.to_dict() for entity in entities]

    @router.get("/entities/{entity_id}", dependencies=[Depends(require_token)])
    async def get_entity(entity_id: str) -> dict:
        entity = system.store.get(entity_id)
        if entity is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown entity: {entity_id}"
            )
        return entity.to_dict()

    @router.post("/entities/{entity_id}/set", dependencies=[Depends(require_token)])
    async def set_entity(entity_id: str, body: SetStateRequest) -> dict:
        entity = system.store.get(entity_id)
        if entity is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown entity: {entity_id}"
            )
        if entity.domain != "switch":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"entity {entity_id} is not controllable",
            )
        try:
            updated = await system.set_switch(entity_id, body.state == "on")
        except ModbusError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=f"modbus error: {exc}"
            ) from exc
        return updated.to_dict()

    return router
