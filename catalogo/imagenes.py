"""Imágenes: lectura desde una fuente, optimización a WebP y registro (manifiesto) de lo ya publicado.

Cada foto se identifica por su nombre sin extensión (por ejemplo ACC-001 o ACC-001_2) y por su huella MD5.
Solo se reprocesa y se sube lo que es nuevo o cambió.
"""
from __future__ import annotations

import base64
import hashlib
import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from PIL import Image, ImageOps

from .config import (
    CALIDAD_GRANDE, CALIDAD_MINIATURA, EXTENSIONES_IMAGEN, TAM_GRANDE, TAM_MINIATURA,
)
from .util import stem


@dataclass(frozen=True)
class ArchivoImagen:
    stem: str
    nombre: str
    md5: str
    id: str | None = None


class FuenteImagenes(Protocol):
    def listar(self) -> dict[str, ArchivoImagen]: ...
    def leer(self, stem: str) -> bytes: ...


class FuenteCarpetaLocal:
    def __init__(self, carpeta: str | Path):
        self.carpeta = Path(carpeta)

    def listar(self) -> dict[str, ArchivoImagen]:
        out: dict[str, ArchivoImagen] = {}
        if not self.carpeta.exists():
            return out
        for p in sorted(self.carpeta.iterdir()):
            if p.is_file() and p.suffix.lower() in EXTENSIONES_IMAGEN and not p.name.startswith(("_", ".")):
                s = stem(p.name)
                if s not in out:
                    out[s] = ArchivoImagen(s, p.name, hashlib.md5(p.read_bytes()).hexdigest())
        return out

    def leer(self, stem_: str) -> bytes:
        for p in self.carpeta.iterdir():
            if p.is_file() and stem(p.name) == stem_ and p.suffix.lower() in EXTENSIONES_IMAGEN:
                return p.read_bytes()
        raise FileNotFoundError(stem_)


class FuenteMemoria:
    """Imágenes ya cargadas en memoria (modo subir archivos)."""

    def __init__(self, archivos: dict[str, bytes]):
        """`archivos`: nombre de archivo -> contenido."""
        self._datos: dict[str, bytes] = {}
        self._info: dict[str, ArchivoImagen] = {}
        for nombre, data in sorted(archivos.items()):
            if not nombre.lower().endswith(EXTENSIONES_IMAGEN) or nombre.startswith(("_", ".")):
                continue
            s = stem(nombre)
            if s not in self._datos:
                self._datos[s] = data
                self._info[s] = ArchivoImagen(s, nombre, hashlib.md5(data).hexdigest())

    def listar(self) -> dict[str, ArchivoImagen]:
        return dict(self._info)

    def leer(self, stem_: str) -> bytes:
        return self._datos[stem_]


class FuenteConCache:
    """Evita descargar de nuevo las fotos ya leídas (útil con Drive). `cache` puede ser un dict compartido."""

    def __init__(self, fuente: FuenteImagenes, cache: dict):
        self.fuente = fuente
        self.cache = cache
        self._listado: dict[str, ArchivoImagen] = {}

    def listar(self) -> dict[str, ArchivoImagen]:
        self._listado = self.fuente.listar()
        return self._listado

    def leer(self, stem_: str) -> bytes:
        if not self._listado:
            self.listar()
        clave_cache = (stem_, self._listado[stem_].md5)
        if clave_cache not in self.cache:
            self.cache[clave_cache] = self.fuente.leer(stem_)
        return self.cache[clave_cache]


def imagenes_de_producto(codigo: str, explicitas: list[str], disponibles: set[str]) -> list[str]:
    """Fotos de un producto: por convención de nombre (CODIGO, CODIGO_2...) más las que liste el maestro."""
    por_nombre = {s.lower(): s for s in disponibles}
    patron = re.compile(re.escape(codigo.lower()) + r"(?:_(\d+))?$")
    auto = []
    for bajo, real in por_nombre.items():
        m = patron.fullmatch(bajo)
        if m:
            auto.append((int(m.group(1) or 1), real))
    resultado = [real for _, real in sorted(auto)]
    for e in explicitas:
        real = por_nombre.get(e.lower())
        if real and real not in resultado:
            resultado.append(real)
    return resultado


def _a_rgb(im: Image.Image) -> Image.Image:
    im = ImageOps.exif_transpose(im)
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        fondo = Image.new("RGB", im.size, (255, 255, 255))
        fondo.paste(im, mask=im.getchannel("A"))
        return fondo
    return im.convert("RGB")


def _webp(im: Image.Image, lado: int, calidad: int) -> bytes:
    copia = im.copy()
    copia.thumbnail((lado, lado), Image.LANCZOS)  # nunca amplía
    buf = io.BytesIO()
    copia.save(buf, "WEBP", quality=calidad, method=4)
    return buf.getvalue()


def optimizar(data: bytes, lado_grande: int = TAM_GRANDE, calidad_grande: int = CALIDAD_GRANDE) -> tuple[bytes, bytes, tuple[int, int]]:
    """Devuelve (miniatura, grande, (ancho, alto) original) en WebP."""
    with Image.open(io.BytesIO(data)) as original:
        im = _a_rgb(original)
    return _webp(im, TAM_MINIATURA, CALIDAD_MINIATURA), _webp(im, lado_grande, calidad_grande), im.size


def miniatura_data_uri(data: bytes) -> str:
    mini, _, _ = optimizar(data)
    return "data:image/webp;base64," + base64.b64encode(mini).decode()


def planificar(listado: dict[str, ArchivoImagen], manifest: dict, necesarias: set[str]) -> tuple[list[str], list[str]]:
    """(por procesar, sin cambios) entre las imágenes que el catálogo usa."""
    previas = (manifest or {}).get("imagenes", {})
    por_procesar, sin_cambio = [], []
    for s in sorted(necesarias):
        if s in previas and previas[s].get("md5") == listado[s].md5:
            sin_cambio.append(s)
        else:
            por_procesar.append(s)
    return por_procesar, sin_cambio


def procesar(stems: list[str], fuente: FuenteImagenes, listado: dict[str, ArchivoImagen], salida: Path) -> dict[str, dict]:
    """Escribe img/t/<stem>.webp e img/l/<stem>.webp y devuelve las entradas del manifiesto."""
    (salida / "img" / "t").mkdir(parents=True, exist_ok=True)
    (salida / "img" / "l").mkdir(parents=True, exist_ok=True)
    entradas = {}
    for s in stems:
        mini, grande, (w, h) = optimizar(fuente.leer(s))
        (salida / "img" / "t" / f"{s}.webp").write_bytes(mini)
        (salida / "img" / "l" / f"{s}.webp").write_bytes(grande)
        entradas[s] = {"md5": listado[s].md5, "w": w, "h": h}
    return entradas
