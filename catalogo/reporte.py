"""Lectura y limpieza del reporte de stock (LSTRE1PT).

Reglas:
- Las columnas se buscan por nombre, no por posición. Si falta una obligatoria, el proceso se detiene.
- Se descartan las filas vacías y la fila "*** TOTAL".
- Se quita el prefijo de marca "0/" o "1/" del código.
- La existencia viene como texto: se convierte a número.
- Las columnas de costo y valorización nunca se leen.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from .config import PREFIJO_MARCA
from .tipos import AVISO, INFO, Hallazgo
from .util import clave, numero_entero_si_corresponde, sin_acentos


class ReporteInvalido(Exception):
    """El archivo no tiene el formato esperado."""


@dataclass
class ReporteLimpio:
    lineas: pd.DataFrame            # una fila por artículo y almacén
    nombre: str
    fecha: date | None
    filas_origen: int
    hallazgos: list[Hallazgo] = field(default_factory=list)

    @property
    def almacenes(self) -> int:
        return int(self.lineas["almacen"].nunique())


_REGLAS = {
    "art": lambda t: t == "ARTICULO",
    "desc": lambda t: t == "DESCRIPCION",
    "alm": lambda t: t.startswith("ALMAC"),
    "exis": lambda t: t.startswith("EXIS"),
    "pvp": lambda t: t == "PVP",
}
_OBLIGATORIAS = ("art", "alm", "exis")


def _filas_de_archivo(data: bytes) -> list[list]:
    if data[:4] == b"\xd0\xcf\x11\xe0":  # Excel antiguo (.xls)
        import xlrd

        libro = xlrd.open_workbook(file_contents=data, logfile=io.StringIO())
        hoja = libro.sheet_by_index(0)
        return [hoja.row_values(i) for i in range(hoja.nrows)]
    if data[:2] == b"PK":  # .xlsx
        import openpyxl

        libro = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        return [list(f) for f in libro.worksheets[0].iter_rows(values_only=True)]
    raise ReporteInvalido("El archivo no parece un Excel (.xls o .xlsx).")


def _detectar_cabecera(filas: list[list]) -> tuple[int, dict[str, int]]:
    for i, fila in enumerate(filas[:15]):
        textos = [sin_acentos(c).strip().upper() if c is not None else "" for c in fila]
        cols: dict[str, int] = {}
        for nombre, regla in _REGLAS.items():
            for j, t in enumerate(textos):
                if t and regla(t) and nombre not in cols:
                    cols[nombre] = j
        if all(k in cols for k in _OBLIGATORIAS):
            return i, cols
    esperadas = "Artículo, Almacén, Exis."
    raise ReporteInvalido(
        f"No se encontró la fila de encabezados con las columnas obligatorias ({esperadas}). "
        "¿Es el reporte correcto o cambió su formato?"
    )


def _texto(valor) -> str:
    if valor is None:
        return ""
    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))
    return str(valor).strip()


def _numero(valor) -> float | None:
    if valor is None or valor == "":
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    s = str(valor).strip().replace(" ", "")
    if not s:
        return None
    if "," in s and "." in s:
        s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def fecha_desde_nombre(nombre: str) -> date | None:
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", nombre)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.search(r"(\d{2})[-_.](\d{2})[-_.](\d{4})", nombre)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    return None


def leer_reporte(data: bytes, nombre: str = "") -> ReporteLimpio:
    filas = _filas_de_archivo(data)
    idx, cols = _detectar_cabecera(filas)
    cuerpo = filas[idx + 1:]

    registros = []
    invalidas: list[str] = []
    negativas: list[str] = []
    descartadas = 0
    for f in cuerpo:
        def celda(k):
            j = cols.get(k)
            return f[j] if j is not None and j < len(f) else None

        art = _texto(celda("art"))
        if not art or art.startswith("***"):
            descartadas += 1
            continue
        codigo = re.sub(PREFIJO_MARCA, "", art)
        existencia = _numero(celda("exis"))
        if existencia is None:
            invalidas.append(codigo)
            existencia = 0.0
        elif existencia < 0:
            negativas.append(codigo)
        registros.append(
            {
                "clave": clave(codigo),
                "codigo": codigo,
                "descripcion": _texto(celda("desc")),
                "almacen": _texto(celda("alm")),
                "existencia": existencia,
                "pvp": _numero(celda("pvp")),
            }
        )

    lineas = pd.DataFrame(registros, columns=["clave", "codigo", "descripcion", "almacen", "existencia", "pvp"])
    if lineas.empty:
        raise ReporteInvalido("El reporte no tiene filas de artículos.")

    rep = ReporteLimpio(lineas=lineas, nombre=nombre, fecha=fecha_desde_nombre(nombre), filas_origen=len(cuerpo))
    if rep.fecha is None:
        rep.hallazgos.append(
            Hallazgo(INFO, "Reporte", nombre or "reporte", "No se pudo leer la fecha desde el nombre del archivo; se usará la fecha de hoy.")
        )
    if invalidas:
        ejemplo = ", ".join(invalidas[:5])
        rep.hallazgos.append(
            Hallazgo(AVISO, "Reporte", f"{len(invalidas)} filas", f"Existencia no numérica (se tomó como 0). Ejemplos: {ejemplo}.")
        )
    if negativas:
        ejemplo = ", ".join(negativas[:5])
        rep.hallazgos.append(
            Hallazgo(AVISO, "Reporte", f"{len(negativas)} filas", f"Existencia negativa. Ejemplos: {ejemplo}.")
        )
    return rep


def consolidar_stock(lineas: pd.DataFrame) -> pd.DataFrame:
    """Suma la existencia de todos los almacenes por artículo. Índice: clave normalizada."""
    g = lineas.groupby("clave", sort=False).agg(
        codigo=("codigo", "first"),
        descripcion=("descripcion", "first"),
        stock=("existencia", "sum"),
        almacenes=("almacen", "nunique"),
        pvp_bs=("pvp", "max"),
    )
    g["stock"] = g["stock"].clip(lower=0).map(numero_entero_si_corresponde)
    return g
