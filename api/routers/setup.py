from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body
from api import runtime_bridge as rt

router = APIRouter(tags=["setup"])

@router.get("/setup/config")
def setup_config() -> dict[str, Any]:
    return rt.setup_config()


@router.post("/setup/config")
def setup_save_config(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return rt.setup_save_config(body)


@router.post("/setup/risk-config")
def setup_risk_config(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return rt.setup_risk_config(body)


@router.get("/setup/services/status")
def setup_services_status() -> dict[str, Any]:
    return rt.setup_services_status()


@router.get("/setup/longport/diagnostics")
def setup_longport_diagnostics(probe: bool = False) -> dict[str, Any]:
    return rt.setup_longport_diagnostics(probe=probe)


@router.post("/setup/services/start")
def setup_start_services(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return rt.setup_start_services(body)


@router.post("/setup/services/stop")
def setup_stop_services(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return rt.setup_stop_services(body)


@router.post("/setup/services/stop-all")
def setup_stop_all_services(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return rt.setup_stop_all_services(body)

