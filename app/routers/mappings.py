"""Mapping lifecycle: propose (analyst/owner) → confirm or reject (owner only). Every change is a row in
ops.mapping_versions; nothing is updated in place except the status of the version being decided."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ValidationError
from sqlalchemy import Connection, Engine, text

from app.auth import Principal, require
from app.config import Settings, get_settings
from app.db import get_engine
from pipeline.mapper.contract import ColumnMapping, MappingProposal
from pipeline.mapper.heuristic import propose_all
from pipeline.profile import discover_sources, profile_source_cached

router = APIRouter(tags=["mappings"])

AnyRole = Depends(require("viewer", "analyst", "owner"))
Proposer = Depends(require("analyst", "owner"))
Owner = Depends(require("owner"))


class MappingVersion(BaseModel):
    mapping_version_id: int
    source: str
    version: int
    status: str
    proposed_by: str
    header: list[str]
    header_hash: str
    columns: list[ColumnMapping]
    unmapped_required: list[str]
    proposed_at: datetime
    confirmed_by: str | None
    confirmed_at: datetime | None
    rejected_by: str | None
    rejected_at: datetime | None


class HumanMapping(BaseModel):
    header: list[str]
    columns: list[ColumnMapping]


SELECT = ("SELECT mapping_version_id, source, version, status, proposed_by, header, header_hash, mapping, "
          "proposed_at, confirmed_by, confirmed_at, rejected_by, rejected_at FROM ops.mapping_versions")


def _row_to_model(row: Any) -> MappingVersion:
    proposal = MappingProposal(source=row.source, header=row.header, proposed_by=row.proposed_by,
                               columns=row.mapping["columns"])
    return MappingVersion(
        mapping_version_id=row.mapping_version_id, source=row.source, version=row.version, status=row.status,
        proposed_by=row.proposed_by, header=row.header, header_hash=row.header_hash.strip(),
        columns=proposal.columns, unmapped_required=proposal.unmapped_required, proposed_at=row.proposed_at,
        confirmed_by=row.confirmed_by, confirmed_at=row.confirmed_at, rejected_by=row.rejected_by,
        rejected_at=row.rejected_at)


def _store(conn: Connection, proposal: MappingProposal) -> MappingVersion:
    """Insert a proposal as the next version for its source, unless the same mapping is already live."""
    conn.execute(text("SELECT pg_advisory_xact_lock(hashtext(:s))"), {"s": proposal.source})
    mapping = {"columns": [c.model_dump() for c in proposal.columns]}
    existing = conn.execute(text(
        f"{SELECT} WHERE source = :s AND header_hash = :h AND status IN ('proposed', 'confirmed') "
        "AND proposed_by = :by AND mapping = CAST(:m AS jsonb) ORDER BY version DESC LIMIT 1"),
        {"s": proposal.source, "h": proposal.header_hash, "by": proposal.proposed_by,
         "m": json.dumps(mapping)}).first()
    if existing:
        return _row_to_model(existing)
    row = conn.execute(text(
        "INSERT INTO ops.mapping_versions (source, version, mapping, proposed_by, header, header_hash) "
        "SELECT :s, COALESCE(MAX(version), 0) + 1, CAST(:m AS jsonb), :by, CAST(:hdr AS jsonb), :h "
        "FROM ops.mapping_versions WHERE source = :s RETURNING mapping_version_id"),
        {"s": proposal.source, "m": json.dumps(mapping), "by": proposal.proposed_by,
         "hdr": json.dumps(proposal.header), "h": proposal.header_hash}).scalar_one()
    return _reload(conn, row)


def _known_source(settings: Settings, source: str) -> list:
    files = discover_sources(settings.sources_dir).get(source) if settings.sources_dir.is_dir() else None
    if not files:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown source '{source}'")
    return files


@router.post("/sources/{source}/mappings/propose", response_model=list[MappingVersion])
def propose(source: str, _: Annotated[Principal, Proposer],
            settings: Annotated[Settings, Depends(get_settings)],
            engine: Annotated[Engine, Depends(get_engine)]) -> list[MappingVersion]:
    """Profile the source and store a heuristic proposal for each header variant found in its files."""
    files = _known_source(settings, source)
    profile = profile_source_cached(source, files, settings.data_dir / "master")
    with engine.begin() as conn:
        return [_store(conn, p) for p in propose_all(profile)]


@router.post("/sources/{source}/mappings", response_model=MappingVersion, status_code=status.HTTP_201_CREATED)
def submit(source: str, body: HumanMapping, _: Annotated[Principal, Proposer],
           settings: Annotated[Settings, Depends(get_settings)],
           engine: Annotated[Engine, Depends(get_engine)]) -> MappingVersion:
    """A person's corrected mapping. Stored as a proposal; it still needs an owner to confirm it."""
    _known_source(settings, source)
    try:
        proposal = MappingProposal(source=source, header=body.header, columns=body.columns,
                                   proposed_by="human")
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.errors(include_url=False)) from exc
    with engine.begin() as conn:
        return _store(conn, proposal)


@router.get("/sources/{source}/mappings", response_model=list[MappingVersion])
def list_versions(source: str, _: Annotated[Principal, AnyRole],
                  engine: Annotated[Engine, Depends(get_engine)]) -> list[MappingVersion]:
    with engine.connect() as conn:
        rows = conn.execute(text(f"{SELECT} WHERE source = :s ORDER BY version"), {"s": source}).all()
    return [_row_to_model(r) for r in rows]


def _lock_proposed(conn: Connection, mapping_version_id: int) -> MappingVersion:
    row = conn.execute(text(f"{SELECT} WHERE mapping_version_id = :id FOR UPDATE"),
                       {"id": mapping_version_id}).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "mapping version not found")
    if row.status != "proposed":
        raise HTTPException(status.HTTP_409_CONFLICT, f"mapping version is already {row.status}")
    return _row_to_model(row)


def _reload(conn: Connection, mapping_version_id: int) -> MappingVersion:
    return _row_to_model(conn.execute(text(f"{SELECT} WHERE mapping_version_id = :id"),
                                      {"id": mapping_version_id}).one())


@router.post("/mappings/{mapping_version_id}/confirm", response_model=MappingVersion)
def confirm(mapping_version_id: int, principal: Annotated[Principal, Owner],
            engine: Annotated[Engine, Depends(get_engine)]) -> MappingVersion:
    """Owner confirms a proposal. The previous confirmed mapping for the same header becomes superseded."""
    with engine.begin() as conn:
        current = _lock_proposed(conn, mapping_version_id)
        if current.unmapped_required:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                                f"required canonical columns are unmapped: {current.unmapped_required}")
        conn.execute(text("UPDATE ops.mapping_versions SET status = 'superseded' "
                          "WHERE source = :s AND header_hash = :h AND status = 'confirmed'"),
                     {"s": current.source, "h": current.header_hash})
        conn.execute(text("UPDATE ops.mapping_versions SET status = 'confirmed', confirmed_by = :who, "
                          "confirmed_at = now() WHERE mapping_version_id = :id"),
                     {"who": principal.role, "id": mapping_version_id})
        return _reload(conn, mapping_version_id)


@router.post("/mappings/{mapping_version_id}/reject", response_model=MappingVersion)
def reject(mapping_version_id: int, principal: Annotated[Principal, Owner],
           engine: Annotated[Engine, Depends(get_engine)]) -> MappingVersion:
    with engine.begin() as conn:
        _lock_proposed(conn, mapping_version_id)
        conn.execute(text("UPDATE ops.mapping_versions SET status = 'rejected', rejected_by = :who, "
                          "rejected_at = now() WHERE mapping_version_id = :id"),
                     {"who": principal.role, "id": mapping_version_id})
        return _reload(conn, mapping_version_id)
