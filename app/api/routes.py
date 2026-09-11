"""REST API routes."""

from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, ValidationError, model_validator

from app.auth import make_token_dependency
from app.modbus.client import ModbusError
from app.system import SmartHomeSystem


class SetStateRequest(BaseModel):
    state: Literal["on", "off"]
    duration: Optional[float] = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _duration_requires_on(self) -> SetStateRequest:
        if self.duration is not None and self.state != "on":
            raise ValueError("duration is only valid when state is 'on'")
        return self


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
    async def list_entities(domain: Optional[str] = None) -> list[dict]:
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

    @router.get("/devices", dependencies=[Depends(require_token)])
    async def list_devices() -> list[dict[str, Any]]:
        return system.list_devices()

    @router.patch("/devices/{device_name}", dependencies=[Depends(require_token)])
    async def patch_device(device_name: str, body: dict[str, Any]) -> dict[str, Any]:
        if not body:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="empty body"
            )
        try:
            return await system.update_device(device_name, body)
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"unknown device: {device_name}",
            ) from exc
        except (ValueError, ValidationError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc

    @router.patch("/entities/{entity_id}", dependencies=[Depends(require_token)])
    async def patch_entity(entity_id: str, body: dict[str, Any]) -> dict[str, Any]:
        if not body:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="empty body"
            )
        try:
            return (await system.update_entity(entity_id, body)).to_dict()
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"unknown entity: {entity_id}",
            ) from exc
        except (ValueError, ValidationError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc

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
            updated = await system.set_switch(
                entity_id, body.state == "on", body.duration
            )
        except ModbusError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=f"modbus error: {exc}"
            ) from exc
        return updated.to_dict()

    return router
