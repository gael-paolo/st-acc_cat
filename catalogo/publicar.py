"""Publicación por FTP (o FTPS) en Netfirms.

- Primero se suben las fotos nuevas, luego el HTML y al final el manifiesto: si algo falla a mitad de camino,
  el catálogo publicado sigue siendo consistente.
- El HTML se sube con nombre temporal y se renombra al final; la versión anterior queda como index.prev.html.
- Las credenciales salen de variables de entorno (o de st.secrets); nunca se guardan en el código.
"""
from __future__ import annotations

import io
import json
import os
import posixpath
from dataclasses import dataclass
from ftplib import FTP, FTP_TLS, error_perm
from pathlib import Path
from typing import Callable


class ErrorPublicacion(Exception):
    """Falla al publicar; el mensaje está pensado para mostrarse a la persona usuaria."""


@dataclass
class ConfigFTP:
    host: str
    user: str
    password: str
    port: int = 21
    remote_dir: str = "/"
    ssl: bool = False
    timeout: int = 30
    url_base: str = ""

    @classmethod
    def desde_entorno(cls, env: dict | None = None, prefijo: str = "TAIYO_FTP_") -> "ConfigFTP":
        env = os.environ if env is None else env
        faltan = [k for k in ("HOST", "USER", "PASS") if not str(env.get(prefijo + k, "")).strip()]
        if faltan:
            raise ErrorPublicacion("Faltan datos de conexión FTP: " + ", ".join(prefijo + k for k in faltan))
        return cls(
            host=str(env[prefijo + "HOST"]).strip(),
            user=str(env[prefijo + "USER"]).strip(),
            password=str(env[prefijo + "PASS"]),
            port=int(str(env.get(prefijo + "PORT", "21") or "21")),
            remote_dir=str(env.get(prefijo + "REMOTE_DIR", "/") or "/"),
            ssl=str(env.get(prefijo + "SSL", "false")).strip().lower() in ("1", "true", "si", "sí", "yes"),
            url_base=str(env.get("TAIYO_SITE_URL", "") or "").strip(),
        )


@dataclass
class ResultadoPublicacion:
    destino: str
    archivos: int
    bytes: int
    url: str = ""


def _conectar(cfg: ConfigFTP) -> FTP:
    try:
        ftp: FTP = FTP_TLS(timeout=cfg.timeout) if cfg.ssl else FTP(timeout=cfg.timeout)
        ftp.connect(cfg.host, cfg.port)
        ftp.login(cfg.user, cfg.password)
        if cfg.ssl:
            ftp.prot_p()
        ftp.set_pasv(True)
        return ftp
    except error_perm as e:
        raise ErrorPublicacion(f"El servidor FTP rechazó el usuario o la contraseña ({e}).") from e
    except OSError as e:
        raise ErrorPublicacion(f"No se pudo conectar al servidor FTP {cfg.host}:{cfg.port} ({e}).") from e


def _ir_a(ftp: FTP, ruta: str) -> None:
    """Entra a la ruta (absoluta) creando las carpetas que falten."""
    ftp.cwd("/")
    for parte in [p for p in ruta.split("/") if p]:
        try:
            ftp.cwd(parte)
        except error_perm:
            ftp.mkd(parte)
            ftp.cwd(parte)


def _destino(cfg: ConfigFTP, subdir: str) -> str:
    base = "/" + cfg.remote_dir.strip("/")
    return posixpath.join(base, subdir.strip("/")) if subdir else base


def _existe(ftp: FTP, nombre: str) -> bool:
    try:
        return nombre in ftp.nlst()
    except error_perm:
        return False


def _borrar_si_existe(ftp: FTP, nombre: str) -> None:
    try:
        ftp.delete(nombre)
    except error_perm:
        pass


def leer_manifest_remoto(cfg: ConfigFTP, subdir: str = "") -> dict:
    """Manifiesto de fotos ya publicadas ({} si todavía no hay)."""
    ftp = _conectar(cfg)
    try:
        _ir_a(ftp, posixpath.join(_destino(cfg, subdir), "img"))
        buf = io.BytesIO()
        try:
            ftp.retrbinary("RETR manifest.json", buf.write)
        except error_perm:
            return {}
        try:
            return json.loads(buf.getvalue().decode("utf-8"))
        except ValueError:
            return {}
    except OSError as e:
        raise ErrorPublicacion(f"Se perdió la conexión con el FTP ({e}).") from e
    finally:
        try:
            ftp.quit()
        except Exception:
            pass


def publicar(
    cfg: ConfigFTP,
    carpeta: Path,
    archivos_nuevos: list[str],
    subdir: str = "",
    progreso: Callable[[int, int, str], None] | None = None,
) -> ResultadoPublicacion:
    carpeta = Path(carpeta)
    destino = _destino(cfg, subdir)
    total = len(archivos_nuevos) + 2
    hecho = 0
    subidos = 0
    nbytes = 0

    def avanzar(texto: str) -> None:
        nonlocal hecho
        hecho += 1
        if progreso:
            progreso(hecho, total, texto)

    ftp = _conectar(cfg)
    try:
        for sub in ("img/t", "img/l"):
            _ir_a(ftp, posixpath.join(destino, sub))
        for rel in archivos_nuevos:
            ruta = carpeta / rel
            _ir_a(ftp, posixpath.join(destino, posixpath.dirname(rel)))
            with open(ruta, "rb") as f:
                ftp.storbinary("STOR " + posixpath.basename(rel), f)
            subidos += 1
            nbytes += ruta.stat().st_size
            avanzar(rel)

        _ir_a(ftp, destino)
        html = carpeta / "index.html"
        with open(html, "rb") as f:
            ftp.storbinary("STOR index.html.tmp", f)
        nbytes += html.stat().st_size
        if _existe(ftp, "index.html"):
            _borrar_si_existe(ftp, "index.prev.html")
            ftp.rename("index.html", "index.prev.html")
        ftp.rename("index.html.tmp", "index.html")
        subidos += 1
        avanzar("index.html")

        _ir_a(ftp, posixpath.join(destino, "img"))
        man = carpeta / "img" / "manifest.json"
        with open(man, "rb") as f:
            ftp.storbinary("STOR manifest.json", f)
        subidos += 1
        nbytes += man.stat().st_size
        avanzar("manifest.json")
    except error_perm as e:
        raise ErrorPublicacion(f"El servidor FTP rechazó una operación ({e}). Revisa los permisos de la carpeta {destino}.") from e
    except OSError as e:
        raise ErrorPublicacion(f"Se interrumpió la conexión con el FTP ({e}). Vuelve a intentarlo: lo ya subido no se pierde.") from e
    finally:
        try:
            ftp.quit()
        except Exception:
            pass

    url = ""
    if cfg.url_base:
        url = cfg.url_base.rstrip("/") + ("/" + subdir.strip("/") if subdir else "") + "/"
    return ResultadoPublicacion(destino=destino, archivos=subidos, bytes=nbytes, url=url)
