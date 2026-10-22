"""Employee documents: CVs, contracts, offer letters, payslips, reviews.

HR-admin only. These files are among the most sensitive records the platform holds (pay,
contract terms, personal history), so:

  * the file and its name are encrypted at rest (data/security/crypto.py);
  * only admins may list, upload, download or delete — a manager gets PermissionError;
  * every action writes an audit row with the document id, type and size, but never the
    filename (it usually contains the person's name);
  * only a fixed set of file types and sizes is accepted.
"""
from __future__ import annotations

import hashlib
import mimetypes
import sqlite3
from datetime import datetime, timezone
from typing import Any

from . import cv_parse
from .audit import write_audit
from .auth import Actor
from .security import crypto

DOC_TYPES = ("cv", "contract", "offer", "payslip", "review", "other")
MAX_BYTES = 8 * 1024 * 1024
_ALLOWED = {".pdf": "application/pdf", ".docx":
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".txt": "text/plain", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


class DocumentError(ValueError):
    """Bad upload (type, size, unknown employee). Maps to HTTP 400."""


def _require_admin(actor: Actor, conn: sqlite3.Connection, action: str, token: str) -> None:
    if not actor.is_admin:
        write_audit(conn, actor_email=actor.email, action=action,
                    filters={"token": token, "denied": True}, result_count=0)
        raise PermissionError("documents are restricted to HR admins")


def _extension(filename: str) -> str:
    dot = filename.rfind(".")
    return filename[dot:].lower() if dot >= 0 else ""


def list_documents(conn: sqlite3.Connection, token: str, *, actor: Actor) -> list[dict[str, Any]]:
    _require_admin(actor, conn, "documents_list", token)
    rows = conn.execute(
        "SELECT id, doc_type, filename_enc, mime, size_bytes, uploaded_by, uploaded_at "
        "FROM employee_documents WHERE employee_token = ? ORDER BY uploaded_at DESC, id DESC",
        (token,)).fetchall()
    out = [{"id": r["id"], "doc_type": r["doc_type"], "filename": crypto.decrypt(r["filename_enc"]),
            "mime": r["mime"], "size_bytes": r["size_bytes"], "uploaded_by": r["uploaded_by"],
            "uploaded_at": r["uploaded_at"]} for r in rows]
    write_audit(conn, actor_email=actor.email, action="documents_list",
                filters={"token": token}, result_count=len(out))
    return out


def add_document(conn: sqlite3.Connection, token: str, *, actor: Actor, filename: str,
                 raw: bytes, doc_type: str = "auto") -> dict[str, Any]:
    """Store one document for an employee. `doc_type` "auto" classifies it from its text."""
    _require_admin(actor, conn, "document_upload", token)
    if conn.execute("SELECT 1 FROM employees WHERE token = ?", (token,)).fetchone() is None:
        raise DocumentError("unknown employee")
    ext = _extension(filename)
    if ext not in _ALLOWED:
        raise DocumentError("unsupported file type (PDF, Word .docx, text, PNG or JPEG)")
    if not raw:
        raise DocumentError("empty file")
    if len(raw) > MAX_BYTES:
        raise DocumentError("file too large (max 8 MB)")
    if doc_type == "auto":
        try:
            doc_type = cv_parse.detect_doc_type(filename, cv_parse.extract_text(filename, raw))
        except cv_parse.UnsupportedDocument:
            doc_type = "other"     # images and unreadable files keep a neutral type
    if doc_type not in DOC_TYPES:
        raise DocumentError(f"doc_type must be one of {', '.join(DOC_TYPES)} or auto")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cur = conn.execute(
        "INSERT INTO employee_documents (employee_token, doc_type, filename_enc, mime, "
        "size_bytes, sha256, content_enc, uploaded_by, uploaded_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (token, doc_type, crypto.encrypt(filename[:255]),
         _ALLOWED[ext] or mimetypes.guess_type(filename)[0] or "application/octet-stream",
         len(raw), hashlib.sha256(raw).hexdigest(), crypto.encrypt_bytes(raw),
         actor.email, now))
    conn.commit()
    doc_id = cur.lastrowid
    write_audit(conn, actor_email=actor.email, action="document_upload",
                filters={"token": token, "doc_id": doc_id, "doc_type": doc_type,
                         "size_bytes": len(raw)}, result_count=1)
    return {"id": doc_id, "doc_type": doc_type, "filename": filename[:255], "mime": _ALLOWED[ext],
            "size_bytes": len(raw), "uploaded_by": actor.email, "uploaded_at": now}


def get_document(conn: sqlite3.Connection, token: str, doc_id: int, *,
                 actor: Actor) -> tuple[dict[str, Any], bytes] | None:
    """(metadata, file bytes), or None if the document does not belong to this employee."""
    _require_admin(actor, conn, "document_download", token)
    r = conn.execute("SELECT id, doc_type, filename_enc, mime, content_enc FROM employee_documents "
                     "WHERE id = ? AND employee_token = ?", (doc_id, token)).fetchone()
    write_audit(conn, actor_email=actor.email, action="document_download",
                filters={"token": token, "doc_id": doc_id}, result_count=0 if r is None else 1)
    if r is None:
        return None
    meta = {"id": r["id"], "doc_type": r["doc_type"], "filename": crypto.decrypt(r["filename_enc"]),
            "mime": r["mime"]}
    return meta, crypto.decrypt_bytes(r["content_enc"])


def delete_document(conn: sqlite3.Connection, token: str, doc_id: int, *, actor: Actor) -> bool:
    _require_admin(actor, conn, "document_delete", token)
    cur = conn.execute("DELETE FROM employee_documents WHERE id = ? AND employee_token = ?",
                       (doc_id, token))
    conn.commit()
    write_audit(conn, actor_email=actor.email, action="document_delete",
                filters={"token": token, "doc_id": doc_id}, result_count=cur.rowcount)
    return cur.rowcount > 0
