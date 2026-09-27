"""
google_drive.py
===============
Wrapper de Google Drive para Mark-XLVIII.

Scope elegido: drive.file  → solo acceso a archivos creados por la app.
Menor superficie, sin verificación obligatoria de Google.
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from .google_auth import build_service_for

DRIVE_DIR = Path(__file__).resolve().parent.parent / "config" / "drive_cache"
DRIVE_DIR.mkdir(parents=True, exist_ok=True)


def _get_drive_service():
    return build_service_for("drive", account="primary")


# ─────────────────────────────────────────────────────────────────────────────
# Listado y búsqueda
# ─────────────────────────────────────────────────────────────────────────────

def list_files(
    *,
    max_results: int = 25,
    query: str | None = None,
    folder_id: str | None = None,
) -> list[dict[str, Any]]:
    """
    Lista archivos. `query` usa sintaxis Drive; `folder_id` filtra por carpeta.
    Ejemplo `query="name contains 'presupuesto'"`.
    """
    svc = _get_drive_service()
    q_parts = []
    if query:
        q_parts.append(query)
    if folder_id:
        q_parts.append(f"'{folder_id}' in parents")
    q_str = " and ".join(f"({p})" for p in q_parts) if q_parts else None

    kwargs: dict[str, Any] = {
        "pageSize": max_results,
        "fields": "files(id,name,mimeType,size,modifiedTime,webViewLink,parents)",
        "spaces": "drive",
    }
    if q_str:
        kwargs["q"] = q_str

    return svc.files().list(**kwargs).execute().get("files", [])


def search_files(text: str, *, max_results: int = 25) -> list[dict[str, Any]]:
    """Atajo: búsqueda por nombre que contenga `text`.

    En la sintaxis de Drive las comillas simples se escapan con `\\'`
    (backslash + comilla), no duplicándolas. Ver:
    https://developers.google.com/drive/api/guides/search-files
    """
    safe = text.replace("\\", "\\\\").replace("'", "\\'")
    return list_files(query=f"name contains '{safe}'", max_results=max_results)


# ─────────────────────────────────────────────────────────────────────────────
# Subida / descarga
# ─────────────────────────────────────────────────────────────────────────────

def upload_file(
    local_path: str | Path,
    *,
    mime_type: str | None = None,
    folder_id: str | None = None,
    filename: str | None = None,
) -> dict[str, Any]:
    """Sube un archivo. Devuelve metadata del archivo creado."""
    from googleapiclient.http import MediaFileUpload

    local_path = Path(local_path)
    if not local_path.exists():
        raise FileNotFoundError(local_path)

    svc = _get_drive_service()
    media = MediaFileUpload(
        str(local_path),
        mimetype=mime_type,
        resumable=False,
    )
    body: dict[str, Any] = {
        "name": filename or local_path.name,
    }
    if folder_id:
        body["parents"] = [folder_id]
    return svc.files().create(
        body=body, media_body=media, fields="id,name,mimeType,webViewLink"
    ).execute()


def download_file(file_id: str, *, dest_dir: str | Path = DRIVE_DIR) -> Path:
    """Descarga el archivo al directorio destino. Devuelve la ruta final."""
    import re as _re
    svc = _get_drive_service()
    meta = svc.files().get(fileId=file_id, fields="name,mimeType").execute()
    safe_name = _re.sub(r"[^\w\-_. ]", "_", meta["name"])
    dest = Path(dest_dir) / safe_name
    request = svc.files().get_media(fileId=file_id)
    with open(dest, "wb") as f:
        from googleapiclient.http import MediaIoBaseDownload
        downloader = MediaIoBaseDownload(f, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    return dest


# ─────────────────────────────────────────────────────────────────────────────
# Organización
# ─────────────────────────────────────────────────────────────────────────────

def create_folder(name: str, *, parent_id: str | None = None) -> dict[str, Any]:
    svc = _get_drive_service()
    body: dict[str, Any] = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if parent_id:
        body["parents"] = [parent_id]
    return svc.files().create(body=body, fields="id,name,webViewLink").execute()


def share_file(file_id: str, *, email: str, role: str = "reader") -> dict:
    """
    Comparte un archivo. `role` ∈ reader|commenter|writer|owner.
    """
    svc = _get_drive_service()
    return svc.permissions().create(
        fileId=file_id,
        body={"type": "user", "role": role, "emailAddress": email},
        fields="id",
        sendNotificationEmail=False,
    ).execute()


def delete_file(file_id: str) -> None:
    svc = _get_drive_service()
    svc.files().delete(fileId=file_id).execute()


def trash_file(file_id: str) -> dict:
    svc = _get_drive_service()
    return svc.files().update(
        fileId=file_id, body={"trashed": True}, fields="id,trashed"
    ).execute()