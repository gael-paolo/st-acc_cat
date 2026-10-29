"""Genera el HTML del catálogo a partir de los productos y la plantilla."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import NOMBRE_CATALOGO

PLANTILLAS = Path(__file__).resolve().parent.parent / "plantillas"
ORDEN_TIPOS = ["Accesorio", "Color Studio", "Mantenimiento"]


def _json_seguro(obj) -> str:
    """JSON apto para incrustar dentro de <script>: evita que un texto cierre la etiqueta."""
    s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return s.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def render_html(
    productos: list[dict],
    modelos: dict[str, str],
    fecha_stock: datetime | None,
    img_urls: dict[str, dict[str, str]],
    titulo: str = NOMBRE_CATALOGO,
    ahora: datetime | None = None,
) -> str:
    ahora = ahora or datetime.now()
    usados = []
    for p in productos:
        for m in p["modelos"]:
            if m not in usados:
                usados.append(m)
    ordenados = sorted(usados, key=lambda c: (c not in modelos, c))
    categorias = sorted({p["categoria"] for p in productos if p["categoria"]})
    tipos = [t for t in ORDEN_TIPOS if any(p["tipo"] == t for p in productos)]
    stems = {s for p in productos for s in p["imgs"]}
    datos = {
        "productos": productos,
        "modelos": [{"codigo": c, "nombre": modelos.get(c, "")} for c in ordenados],
        "categorias": categorias,
        "tipos": tipos,
        "img": {s: img_urls[s] for s in stems if s in img_urls},
    }
    env = Environment(loader=FileSystemLoader(str(PLANTILLAS)), autoescape=select_autoescape(["html", "j2"]))
    return env.get_template("catalogo.html.j2").render(
        titulo=titulo,
        fecha_stock=(fecha_stock or ahora).strftime("%d/%m/%Y"),
        generado=ahora.strftime("%d/%m/%Y %H:%M"),
        datos_json=_json_seguro(datos),
    )
