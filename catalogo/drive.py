"""Lectura de Google Drive con una cuenta de servicio (solo lectura).

Se comparten con el correo de la cuenta de servicio:
  - la carpeta de reportes de stock,
  - el maestro (Google Sheet o .xlsx),
  - la carpeta de fotos.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass

from .config import EXTENSIONES_IMAGEN
from .imagenes import ArchivoImagen
from .reporte import fecha_desde_nombre
from .util import stem

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
MIME_SHEET = "application/vnd.google-apps.spreadsheet"
MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CAMPOS = "nextPageToken, files(id, name, mimeType, modifiedTime, md5Checksum, size)"


class ErrorDrive(Exception):
    pass


@dataclass(frozen=True)
class ArchivoDrive:
    id: str
    nombre: str
    mime: str
    modificado: str
    md5: str = ""


def conectar(info_cuenta: dict):
    """`info_cuenta` es el JSON de la cuenta de servicio (por ejemplo st.secrets['gcp_service_account'])."""
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        cred = service_account.Credentials.from_service_account_info(dict(info_cuenta), scopes=SCOPES)
        return build("drive", "v3", credentials=cred, cache_discovery=False)
    except Exception as e:
        t = str(e).lower()
        if "pem" in t or "private key" in t or "deserialize" in t or "asn1" in t:
            raise ErrorDrive(
                "La clave privada de la cuenta de servicio no se puede leer. Vuelve a copiar el contenido completo del archivo JSON "
                "(usa la plantilla gcp_service_account_json de DESPLIEGUE.md)."
            ) from e
        raise ErrorDrive(f"No se pudo iniciar sesión en Google Drive con la cuenta de servicio ({e}).") from e


def listar_carpeta(servicio, carpeta_id: str) -> list[ArchivoDrive]:
    out: list[ArchivoDrive] = []
    token = None
    try:
        while True:
            r = servicio.files().list(
                q=f"'{carpeta_id}' in parents and trashed = false",
                fields=CAMPOS, pageSize=1000, pageToken=token,
                supportsAllDrives=True, includeItemsFromAllDrives=True,
            ).execute()
            for f in r.get("files", []):
                out.append(ArchivoDrive(f["id"], f["name"], f.get("mimeType", ""), f.get("modifiedTime", ""), f.get("md5Checksum", "")))
            token = r.get("nextPageToken")
            if not token:
                return out
    except Exception as e:
        raise ErrorDrive(f"No se pudo leer la carpeta de Drive ({e}). ¿Está compartida con la cuenta de servicio?") from e


def descargar(servicio, archivo: ArchivoDrive) -> bytes:
    from googleapiclient.http import MediaIoBaseDownload

    try:
        if archivo.mime == MIME_SHEET:
            req = servicio.files().export_media(fileId=archivo.id, mimeType=MIME_XLSX)
        else:
            req = servicio.files().get_media(fileId=archivo.id, supportsAllDrives=True)
        buf = io.BytesIO()
        dl = MediaIoBaseDownload(buf, req)
        listo = False
        while not listo:
            _, listo = dl.next_chunk()
        return buf.getvalue()
    except Exception as e:
        raise ErrorDrive(f"No se pudo descargar «{archivo.nombre}» ({e}).") from e


def ultimo_reporte(archivos: list[ArchivoDrive]) -> ArchivoDrive:
    """El reporte más reciente: por la fecha del nombre y, si no la tiene, por la fecha de modificación."""
    cand = [a for a in archivos if re.search(r"\.xlsx?$", a.nombre, re.I) or a.mime == MIME_SHEET]
    if not cand:
        raise ErrorDrive("No hay reportes de stock (.xls o .xlsx) en la carpeta de Drive.")

    def orden(a: ArchivoDrive):
        f = fecha_desde_nombre(a.nombre)
        return (f.isoformat() if f else "", a.modificado)

    return max(cand, key=orden)


class FuenteDrive:
    """Fuente de imágenes desde una carpeta de Drive (implementa FuenteImagenes)."""

    def __init__(self, servicio, carpeta_id: str):
        self.servicio = servicio
        self.carpeta_id = carpeta_id
        self._archivos: dict[str, ArchivoDrive] = {}

    def listar(self) -> dict[str, ArchivoImagen]:
        self._archivos = {}
        out: dict[str, ArchivoImagen] = {}
        for a in sorted(listar_carpeta(self.servicio, self.carpeta_id), key=lambda x: x.nombre):
            if a.mime.startswith("application/vnd.google-apps") or not a.nombre.lower().endswith(EXTENSIONES_IMAGEN):
                continue
            s = stem(a.nombre)
            if s in out or s.startswith(("_", ".")):
                continue
            self._archivos[s] = a
            out[s] = ArchivoImagen(s, a.nombre, a.md5, a.id)
        return out

    def leer(self, stem_: str) -> bytes:
        if not self._archivos:
            self.listar()
        return descargar(self.servicio, self._archivos[stem_])
