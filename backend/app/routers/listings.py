import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import RequestContext
from ..config import get_settings
from ..database.connection import get_session
from ..database.models import Property, PropertyMedia, PropertyPlaybook
from ..dependencies import DashboardContext
from ..media_storage import delete_stored_file, describe_media_storage, media_content_type, store_uploaded_file
from ..playbooks import get_property_playbook, upsert_property_playbook
from ..schemas import (
    PropertyBulkDeleteIn,
    PropertyDeleteSummaryOut,
    PropertyIn,
    PropertyMediaIn,
    PropertyMediaOut,
    PropertyPlaybookIn,
    PropertyPlaybookOut,
    PropertyOut,
)
from ..services import (
    delete_properties,
    delete_property,
    delete_property_media,
    list_property_media,
    upsert_property,
    upsert_property_media,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/properties", response_model=list[PropertyOut])
def list_properties(session: Session = Depends(get_session), context: RequestContext = DashboardContext) -> list[Property]:
    return list(
        session.scalars(
            select(Property).order_by(Property.property_id)
        ).all()
    )


@router.post("/api/properties", response_model=PropertyOut)
def create_or_update_property(
    payload: PropertyIn,
    session: Session = Depends(get_session),
    _context: RequestContext = DashboardContext,
) -> Property:
    property_ = upsert_property(session, payload)
    session.commit()
    session.refresh(property_)
    return property_


@router.post("/api/properties/bulk-delete", response_model=PropertyDeleteSummaryOut)
def bulk_delete_properties_route(
    payload: PropertyBulkDeleteIn,
    session: Session = Depends(get_session),
    _context: RequestContext = DashboardContext,
) -> dict[str, object]:
    try:
        summary = delete_properties(session, payload.property_ids)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    session.commit()
    return summary


@router.delete("/api/properties/{property_id}", response_model=PropertyDeleteSummaryOut)
def delete_property_route(
    property_id: str,
    session: Session = Depends(get_session),
    _context: RequestContext = DashboardContext,
) -> dict[str, object]:
    try:
        summary = delete_property(session, property_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    session.commit()
    return summary


@router.get("/api/properties/{property_id}/playbook", response_model=PropertyPlaybookOut)
def get_property_playbook_route(
    property_id: str,
    session: Session = Depends(get_session),
    context: RequestContext = DashboardContext,
) -> PropertyPlaybook | dict[str, Any]:
    property_ = session.scalar(select(Property).where(Property.property_id == property_id))
    if not property_:
        raise HTTPException(status_code=404, detail="Property not found")
    playbook = get_property_playbook(session, property_id)
    if not playbook:
        return {
            "id": None,
            "property_id": property_id,
            "initial_reply_blocks": [],
            "enabled": False,
            "created_at": None,
            "updated_at": None,
        }
    return playbook


@router.put("/api/properties/{property_id}/playbook", response_model=PropertyPlaybookOut)
def put_property_playbook_route(
    property_id: str,
    payload: PropertyPlaybookIn,
    session: Session = Depends(get_session),
    _context: RequestContext = DashboardContext,
) -> PropertyPlaybook:
    try:
        playbook = upsert_property_playbook(session, property_id, payload)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    session.commit()
    session.refresh(playbook)
    return playbook


@router.get("/api/properties/{property_id}/media", response_model=list[PropertyMediaOut])
def list_property_media_route(
    property_id: str,
    include_disabled: bool = Query(False),
    session: Session = Depends(get_session),
    context: RequestContext = DashboardContext,
) -> list[PropertyMedia]:
    property_ = session.scalar(select(Property).where(Property.property_id == property_id))
    if not property_:
        raise HTTPException(status_code=404, detail="Property not found")
    return list_property_media(session, property_id, include_disabled=include_disabled)


@router.post("/api/properties/{property_id}/media", response_model=PropertyMediaOut)
def create_or_update_property_media(
    property_id: str,
    payload: PropertyMediaIn,
    session: Session = Depends(get_session),
    _context: RequestContext = DashboardContext,
) -> PropertyMedia:
    try:
        media = upsert_property_media(session, property_id, payload)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    session.commit()
    session.refresh(media)
    return media


@router.post("/api/properties/{property_id}/media/upload", response_model=PropertyMediaOut)
def upload_property_media_route(
    property_id: str,
    media_type: str = Form("photo"),
    caption: str = Form(""),
    sort_order: int = Form(0),
    enabled: bool = Form(True),
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    context: RequestContext = DashboardContext,
) -> PropertyMedia:
    property_ = session.scalar(select(Property).where(Property.property_id == property_id))
    if not property_:
        raise HTTPException(status_code=404, detail="Property not found")

    filename = Path(file.filename or "property-media").name
    stored_path: str | None = None
    try:
        stored = store_uploaded_file(
            file.file,
            property_id=property_id,
            filename=filename,
            max_bytes=get_settings().media_max_upload_bytes,
        )
        stored_path = stored.file_path
        media = upsert_property_media(
            session,
            property_id,
            PropertyMediaIn(
                media_type=media_type,
                file_path=stored.file_path,
                caption=caption,
                sort_order=sort_order,
                enabled=enabled,
            ),
        )
    except ValueError as error:
        logger.info(
            "Property media upload rejected property_id=%s filename=%s error=%s",
            property_id,
            filename,
            error,
        )
        if stored_path:
            delete_stored_file(stored_path)
        raise HTTPException(status_code=400, detail=str(error)) from error
    finally:
        file.file.close()

    session.commit()
    session.refresh(media)
    return media


@router.delete("/api/property-media/{media_id}", response_model=PropertyMediaOut)
def delete_property_media_route(
    media_id: int,
    session: Session = Depends(get_session),
    context: RequestContext = DashboardContext,
) -> PropertyMedia:
    media = session.scalar(select(PropertyMedia).where(PropertyMedia.id == media_id))
    if not media:
        raise HTTPException(status_code=404, detail="Property media not found")

    try:
        delete_stored_file(media.file_path)
    except ValueError:
        logger.info("Media path is outside managed runtime storage; deleting database row only media_id=%s", media.id)

    media = delete_property_media(session, media_id)
    session.commit()
    return media


@router.get("/api/property-media/{media_id}/content")
def serve_property_media(
    media_id: int,
    session: Session = Depends(get_session),
    _context: RequestContext = DashboardContext,
) -> FileResponse:
    media = session.get(PropertyMedia, media_id)
    if not media:
        raise HTTPException(status_code=404, detail="Property media not found")
    descriptor = describe_media_storage(media)
    if not descriptor.local_file_exists:
        raise HTTPException(status_code=404, detail="Media file not found")
    try:
        media_path = Path(media.file_path).expanduser().resolve()
        media_path.relative_to(get_settings().media_root.expanduser().resolve())
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Media file is outside managed storage") from error
    return FileResponse(media_path, media_type=media_content_type(str(media_path)), filename=media_path.name)
