"""Read-only lineage: what a source column feeds, and where a gold column comes from (docs/plan.md A8)."""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import Engine

from app.auth import Principal, require
from app.db import get_engine
from pipeline import lineage

router = APIRouter(tags=["lineage"])
AnyRole = Depends(require("viewer", "analyst", "owner"))


class LineageRowOut(BaseModel):
    mapping_version_id: int
    mapping_version: int
    mapping_status: str
    source: str
    source_col: str
    silver_col: str
    gold_table: str
    gold_col: str
    transforms: list[str]


class ImpactOut(BaseModel):
    source: str
    source_col: str
    summary: str
    gold_columns: list[str]
    views: list[str]
    lineage: list[LineageRowOut]


@router.get("/lineage/impact", response_model=ImpactOut)
def impact(_: Annotated[Principal, AnyRole], engine: Annotated[Engine, Depends(get_engine)],
           source: Annotated[str, Query()], source_col: Annotated[str, Query()],
           include_superseded: Annotated[bool, Query()] = False) -> ImpactOut:
    """If this source column breaks, which gold columns and compiled views are affected."""
    with engine.connect() as conn:
        result = lineage.impact(conn, source, source_col, include_superseded)
    if not result.lineage:
        raise HTTPException(status.HTTP_404_NOT_FOUND, result.summary)
    return ImpactOut(source=source, source_col=source_col, summary=result.summary,
                     gold_columns=result.gold_columns, views=result.views,
                     lineage=[LineageRowOut(**asdict(r)) for r in result.lineage])


@router.get("/lineage/upstream", response_model=list[LineageRowOut])
def upstream(_: Annotated[Principal, AnyRole], engine: Annotated[Engine, Depends(get_engine)],
             gold_table: Annotated[str, Query()], gold_col: Annotated[str, Query()],
             include_superseded: Annotated[bool, Query()] = False) -> list[LineageRowOut]:
    """Every confirmed source column that feeds this gold column."""
    with engine.connect() as conn:
        rows = lineage.upstream(conn, gold_table, gold_col, include_superseded)
    return [LineageRowOut(**asdict(r)) for r in rows]
