"""HTTP routes for the local, manual-share import workflow."""

import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from paiya.grants.errors import GrantError
from paiya.manual.service import (
    ManualImportConflict,
    ManualImportError,
    ManualImportService,
)
from paiya.settings import get_database_engine

router = APIRouter(prefix="/v1")

# JSON can expand control characters to six bytes each. Keep enough headroom for
# a maximum-sized HTTP text input while still bounding streamed request memory.
MAX_IMPORT_TEXT_BYTES = 1_048_576
MAX_IMPORT_BODY_BYTES = (MAX_IMPORT_TEXT_BYTES * 6) + 16_384


class _ImportRequestError(ValueError):
    def __init__(self, status_code: int, code: str, message: str, field: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.field = field


def _error(
    status_code: int,
    code: str,
    message: str,
    *,
    field: str | None = None,
) -> JSONResponse:
    detail: dict[str, str] = {"code": code, "message": message}
    if field is not None:
        detail["field"] = field
    return JSONResponse(
        status_code=status_code,
        content={"detail": detail},
        headers={"Cache-Control": "no-store"},
    )


def _grant_unavailable() -> JSONResponse:
    # Deliberately identical for an unknown group and a group without an active grant.
    return _error(404, "import_grant_unavailable", "ไม่พบกลุ่มหรือสิทธิ์นำเข้าที่ใช้งานได้")


def _manual_error(error: ManualImportError) -> JSONResponse:
    if error.code == "preview_changed":
        return _error(
            409,
            "preview_digest_mismatch",
            "ข้อมูลไม่ตรงกับตัวอย่าง กรุณาดูตัวอย่างใหม่ก่อนยืนยัน",
            field="preview_digest",
        )
    if error.code == "invalid_idempotency_key":
        return _error(
            422,
            error.code,
            "คีย์การยืนยันไม่ถูกต้อง",
            field="Idempotency-Key",
        )
    if error.code == "invalid_permalink":
        return _error(
            422,
            error.code,
            "กรุณาใช้ลิงก์ตรงของโพสต์ในกลุ่ม",
            field="permalink",
        )
    if error.code == "grant_scope_mismatch":
        return _error(
            422,
            error.code,
            "ลิงก์โพสต์ไม่ตรงกับกลุ่มที่เลือก",
            field="permalink",
        )
    if error.code in {"empty_text", "invalid_text"}:
        return _error(422, error.code, "กรุณาวางข้อความประกาศ", field="text")
    if error.code == "text_too_large":
        return _error(
            413,
            "payload_too_large",
            "ข้อความเกินขนาดที่กำหนด กรุณาลดขนาดแล้วลองใหม่",
            field="text",
        )
    if error.code == "ambiguous_item_key":
        return _error(
            422,
            error.code,
            "ระบุรายการจากข้อความนี้ไม่ได้ กรุณาตรวจข้อความ",
            field="text",
        )
    if error.code == "idempotency_incomplete":
        return _error(500, "import_result_unavailable", "เปิดผลการนำเข้าที่บันทึกไว้ไม่ได้")
    return _error(422, error.code, "ตรวจข้อมูลนำเข้าอีกครั้ง", field="body")


async def _read_json_body(request: Request) -> dict[str, Any]:
    media_type = request.headers.get("content-type", "").split(";", maxsplit=1)[0].strip()
    if media_type.casefold() != "application/json":
        raise _ImportRequestError(
            415,
            "unsupported_media_type",
            "คำขอต้องส่งข้อมูล JSON",
            "body",
        )

    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except ValueError as error:
            raise _ImportRequestError(422, "invalid_request", "ขนาดคำขอไม่ถูกต้อง", "body") from error
        if declared_length < 0:
            raise _ImportRequestError(422, "invalid_request", "ขนาดคำขอไม่ถูกต้อง", "body")
        if declared_length > MAX_IMPORT_BODY_BYTES:
            raise _ImportRequestError(
                413,
                "payload_too_large",
                "คำขอเกินขนาดที่กำหนด กรุณาลดขนาดแล้วลองใหม่",
                "body",
            )

    payload = bytearray()
    async for chunk in request.stream():
        if len(payload) + len(chunk) > MAX_IMPORT_BODY_BYTES:
            raise _ImportRequestError(
                413,
                "payload_too_large",
                "คำขอเกินขนาดที่กำหนด กรุณาลดขนาดแล้วลองใหม่",
                "body",
            )
        payload.extend(chunk)

    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _ImportRequestError(422, "invalid_request", "ส่งข้อมูล JSON ที่ถูกต้อง", "body") from error
    if not isinstance(decoded, dict):
        raise _ImportRequestError(422, "invalid_request", "ส่งข้อมูล JSON แบบ object", "body")
    return decoded


def _input_fields(body: dict[str, Any]) -> tuple[str, str]:
    permalink = body.get("permalink")
    text_value = body.get("text", body.get("post_text"))
    if "text" in body and "post_text" in body and body["text"] != body["post_text"]:
        raise _ImportRequestError(422, "invalid_request", "ข้อความไม่ตรงกัน", "text")
    if not isinstance(permalink, str):
        raise _ImportRequestError(422, "invalid_permalink", "กรุณาใช้ลิงก์ตรงของโพสต์ในกลุ่ม", "permalink")
    if not isinstance(text_value, str):
        raise _ImportRequestError(422, "empty_text", "กรุณาวางข้อความประกาศ", "text")
    try:
        text_size = len(text_value.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise _ImportRequestError(422, "invalid_text", "กรุณาตรวจข้อความประกาศ", "text") from error
    if text_size > MAX_IMPORT_TEXT_BYTES:
        raise _ImportRequestError(
            413,
            "payload_too_large",
            "ข้อความเกินขนาดที่กำหนด กรุณาลดขนาดแล้วลองใหม่",
            "text",
        )
    return permalink, text_value


def _validate_idempotency_key(value: str | None) -> str:
    if value is None:
        raise _ImportRequestError(
            422,
            "invalid_idempotency_key",
            "ต้องมีคีย์การยืนยัน",
            "Idempotency-Key",
        )
    key = value.strip()
    if not key or len(key) > 200 or any(not (0x21 <= ord(char) <= 0x7E) for char in key):
        raise _ImportRequestError(
            422,
            "invalid_idempotency_key",
            "คีย์การยืนยันไม่ถูกต้อง",
            "Idempotency-Key",
        )
    return key


def _preview_response(preview: dict[str, Any]) -> dict[str, object]:
    items = preview["items"]
    evidence = preview["evidence"]
    unresolved_item_keys = preview["unresolved_item_keys"]
    unresolved_fields: list[dict[str, str]] = []
    unresolved_key_set = set(unresolved_item_keys)
    for item in items:
        source_item_key = item["source_item_key"]
        if source_item_key in unresolved_key_set:
            unresolved_fields.append({"field": "item_identity", "source_item_key": source_item_key})
        if not any(fact["kind"] == "ask" for fact in item["price_facts"]):
            unresolved_fields.append({"field": "ask_price", "source_item_key": source_item_key})
    return {
        "source_kind": preview["source_kind"],
        "permalink": preview["permalink"],
        "parser_version": preview["parser_version"],
        "preview_digest": preview["preview_digest"],
        "items": items,
        "unassigned_price_facts": preview["unassigned_price_facts"],
        "unresolved_item_keys": unresolved_item_keys,
        "unresolved_fields": unresolved_fields,
        "evidence": evidence,
    }


@router.post("/imports/preview")
async def preview_import(request: Request, group_id: UUID = Query()) -> JSONResponse:
    """Resolve access before reading the body, then return a local parse preview."""
    service = ManualImportService(get_database_engine())
    try:
        grant_id = await run_in_threadpool(service.resolve_group_grant, group_id)
        body = await _read_json_body(request)
        permalink, text = _input_fields(body)
        preview = await run_in_threadpool(service.preview, text, permalink, grant_id)
    except GrantError:
        return _grant_unavailable()
    except _ImportRequestError as error:
        return _error(
            error.status_code,
            error.code,
            error.message,
            field=error.field,
        )
    except ManualImportError as error:
        return _manual_error(error)
    return JSONResponse(
        status_code=200,
        content=_preview_response(preview),
        headers={"Cache-Control": "no-store"},
    )


@router.post("/imports")
async def commit_import(request: Request, group_id: UUID = Query()) -> JSONResponse:
    """Recheck current group access before reading and transactionally commit."""
    service = ManualImportService(get_database_engine())
    try:
        grant_id = await run_in_threadpool(service.resolve_group_grant, group_id)
        idempotency_key = _validate_idempotency_key(request.headers.get("idempotency-key"))
        body = await _read_json_body(request)
        try:
            permalink, text = _input_fields(body)
            digest = body.get("preview_digest")
            if not isinstance(digest, str):
                raise _ImportRequestError(
                    422,
                    "invalid_preview_digest",
                    "ตัวอย่างนำเข้าไม่ถูกต้อง กรุณาดูตัวอย่างใหม่",
                    "preview_digest",
                )
            preview = await run_in_threadpool(service.preview, text, permalink, grant_id)
        except (_ImportRequestError, ManualImportError) as error:
            has_key = await run_in_threadpool(
                service.idempotency_key_exists,
                idempotency_key,
                grant_id,
            )
            if has_key:
                raise ManualImportConflict() from error
            raise
        request_preview = {**preview, "preview_digest": digest}
        result, is_retry = await run_in_threadpool(
            service.commit_with_status,
            request_preview,
            idempotency_key,
            grant_id,
        )
    except GrantError:
        return _grant_unavailable()
    except _ImportRequestError as error:
        return _error(
            error.status_code,
            error.code,
            error.message,
            field=error.field,
        )
    except ManualImportConflict:
        return _error(
            409,
            "idempotency_conflict",
            "คีย์การยืนยันนี้ใช้กับข้อมูลอื่นแล้ว กรุณาเริ่มรายการใหม่",
            field="Idempotency-Key",
        )
    except ManualImportError as error:
        return _manual_error(error)

    status_code = 200 if is_retry else 201
    return JSONResponse(
        status_code=status_code,
        content=result,
        headers={"Cache-Control": "no-store"},
    )
