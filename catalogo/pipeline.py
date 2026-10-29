"""Orquestación: del reporte, el maestro y las fotos al sitio listo para publicar."""
from __future__ import annotations

import base64
import io
import json
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable

import pandas as pd

from .config import UMBRAL_POCAS_UNIDADES
from .controles import controles_cruce, controles_reporte
from .cruce import construir_catalogo, resumen
from .config import TAM_MINIATURA
from .imagenes import (
    FuenteImagenes, optimizar, planificar, procesar,
)
from .maestro import Maestro, leer_maestro, validar_maestro
from .render import render_html
from .reporte import ReporteLimpio, consolidar_stock, leer_reporte
from .tipos import ERROR, Hallazgo


@dataclass
class Resultado:
    productos: list[dict]
    hallazgos: list[Hallazgo]
    sospechosos: pd.DataFrame
    nuevos: pd.DataFrame
    resumen: dict
    reporte: ReporteLimpio
    maestro: Maestro
    stock: pd.DataFrame
    fecha_stock: datetime
    imagenes_disponibles: set[str] = field(default_factory=set)

    @property
    def errores(self) -> list[Hallazgo]:
        return [h for h in self.hallazgos if h.nivel == ERROR]

    @property
    def puede_publicar(self) -> bool:
        return not self.errores


def analizar(
    reporte: bytes,
    nombre_reporte: str,
    maestro: bytes,
    fuente: FuenteImagenes,
    umbral: int = UMBRAL_POCAS_UNIDADES,
    hoy: date | None = None,
    filas_previas: int | None = None,
) -> Resultado:
    """Limpia, cruza y valida. No toca imágenes pesadas (solo lista los archivos)."""
    rep = leer_reporte(reporte, nombre_reporte)
    stock = consolidar_stock(rep.lineas)
    mae = leer_maestro(maestro)
    listado = fuente.listar()
    disponibles = set(listado)

    hallazgos: list[Hallazgo] = list(rep.hallazgos)
    hallazgos += controles_reporte(rep, hoy, filas_previas)
    hallazgos += validar_maestro(mae, disponibles)
    h_cruce, sospechosos, nuevos = controles_cruce(mae, stock)
    hallazgos += h_cruce

    productos = construir_catalogo(mae, stock, listado, umbral)
    orden = {ERROR: 0, "aviso": 1, "info": 2}
    hallazgos.sort(key=lambda h: orden.get(h.nivel, 3))
    fecha = datetime.combine(rep.fecha, datetime.min.time()) if rep.fecha else datetime.now()
    return Resultado(productos, hallazgos, sospechosos, nuevos, resumen(productos), rep, mae, stock, fecha, disponibles)


def _urls_relativas(res: Resultado) -> dict[str, dict[str, str]]:
    stems = {s for p in res.productos for s in p["imgs"]}
    return {s: {"t": f"img/t/{s}.webp", "l": f"img/l/{s}.webp"} for s in stems}


@dataclass
class Sitio:
    carpeta: Path
    archivos_nuevos: list[str]      # rutas relativas a subir (sin index.html ni manifest)
    manifest: dict
    imagenes_procesadas: int
    imagenes_sin_cambio: int


def generar_sitio(res: Resultado, fuente: FuenteImagenes, salida: Path, manifest_previo: dict | None = None) -> Sitio:
    """Escribe index.html, las fotos nuevas y el manifiesto en `salida`.

    Con `manifest_previo` solo se procesan las fotos nuevas o modificadas.
    """
    salida = Path(salida)
    salida.mkdir(parents=True, exist_ok=True)
    listado = fuente.listar()
    necesarias = {s for p in res.productos for s in p["imgs"]}
    por_procesar, sin_cambio = planificar(listado, manifest_previo or {}, necesarias)
    entradas = procesar(por_procesar, fuente, listado, salida)

    previas = dict((manifest_previo or {}).get("imagenes", {}))
    previas.update(entradas)
    manifest = {"version": 1, "imagenes": {s: previas[s] for s in sorted(necesarias) if s in previas}}
    (salida / "img").mkdir(exist_ok=True)
    (salida / "img" / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")

    html = render_html(res.productos, res.maestro.modelos, res.fecha_stock, _urls_relativas(res))
    (salida / "index.html").write_text(html, encoding="utf-8")
    nuevos = [f"img/t/{s}.webp" for s in por_procesar] + [f"img/l/{s}.webp" for s in por_procesar]
    return Sitio(salida, nuevos, manifest, len(por_procesar), len(sin_cambio))


def _data_uri(b: bytes) -> str:
    return "data:image/webp;base64," + base64.b64encode(b).decode()


def html_autocontenido(res: Resultado, fuente: FuenteImagenes, incluir_grande: bool = True) -> str:
    """HTML de un solo archivo: las fotos van incrustadas (miniatura y, si la foto original lo justifica, una versión grande)."""
    stems = {s for p in res.productos for s in p["imgs"]}
    urls: dict[str, dict[str, str]] = {}
    for s in sorted(stems):
        try:
            mini, grande, (w, h) = optimizar(fuente.leer(s), lado_grande=900, calidad_grande=74)
        except Exception:
            continue
        urls[s] = {"t": _data_uri(mini)}
        if incluir_grande and max(w, h) > TAM_MINIATURA * 1.15:
            urls[s]["l"] = _data_uri(grande)
    return render_html(res.productos, res.maestro.modelos, res.fecha_stock, urls)


def html_vista_previa(res: Resultado, fuente: FuenteImagenes) -> str:
    """Vista previa rápida: solo miniaturas."""
    return html_autocontenido(res, fuente, incluir_grande=False)


LEEME = """CATÁLOGO DE ACCESORIOS - {fecha}

Stock al {fecha}. Generado el {generado}. Precios en USD.

catalogo.html
  Catálogo interactivo en un solo archivo (las fotos van incluidas).
  - Para la intranet: cópialo a la carpeta del catálogo reemplazando el anterior.
  - Se abre con Edge, Chrome o Safari. No lo envíes por WhatsApp ni por correo:
    en los celulares puede no abrirse. Comparte el enlace de la intranet.
  - El botón «Descargar PDF» de la página imprime lo que esté filtrado en ese momento.

pdf/
  Catálogos en PDF listos para enviar a clientes por WhatsApp o correo.
  - catalogo_completo_{fecha_iso}.pdf: todos los modelos.
  - catalogo_<MODELO>_{fecha_iso}.pdf: un modelo.

Avisos pendientes al generar: {avisos}
"""


def generar_paquete(
    res: Resultado,
    fuente: FuenteImagenes,
    incluir_html: bool = True,
    incluir_pdf_completo: bool = True,
    incluir_pdf_modelos: bool = True,
    progreso: Callable[[float, str], None] | None = None,
    ahora: datetime | None = None,
) -> dict[str, bytes]:
    """Genera los archivos para distribuir: {ruta dentro del ZIP: contenido}."""
    from .pdf import GeneradorPDF

    ahora = ahora or datetime.now()
    iso = res.fecha_stock.strftime("%Y-%m-%d")
    avance = progreso or (lambda f, t: None)
    archivos: dict[str, bytes] = {}
    modelos = []
    pdfs = None
    if incluir_pdf_completo or incluir_pdf_modelos:
        pdfs = GeneradorPDF(res.productos, res.maestro.modelos, res.fecha_stock, fuente.leer)
        modelos = pdfs.modelos() if incluir_pdf_modelos else []
    pasos = int(incluir_html) + int(incluir_pdf_completo) + len(modelos)
    hecho = 0

    def paso(texto: str) -> None:
        nonlocal hecho
        hecho += 1
        avance(hecho / max(pasos, 1), texto)

    if incluir_html:
        avance(0.0, "Optimizando las fotos e incrustándolas en el HTML…")
        archivos["catalogo.html"] = html_autocontenido(res, fuente).encode("utf-8")
        paso("catalogo.html listo")
    if incluir_pdf_completo and pdfs:
        archivos[f"pdf/catalogo_completo_{iso}.pdf"] = pdfs.pdf_completo()
        paso("PDF completo listo")
    for m in modelos:
        archivos[f"pdf/catalogo_{m}_{iso}.pdf"] = pdfs.pdf_modelo(m)
        paso(f"PDF {m} listo")
    avisos = sum(1 for h in res.hallazgos if h.nivel == "aviso")
    archivos["LEEME.txt"] = LEEME.format(
        fecha=res.fecha_stock.strftime("%d/%m/%Y"), fecha_iso=iso, generado=ahora.strftime("%d/%m/%Y %H:%M"), avisos=avisos
    ).encode("utf-8")
    return archivos


def zip_archivos(archivos: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for nombre, data in archivos.items():
            z.writestr(nombre, data)
    return buf.getvalue()


def zip_sitio(carpeta: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(Path(carpeta).rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(carpeta).as_posix())
    return buf.getvalue()
