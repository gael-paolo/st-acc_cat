"""Controles de calidad que acompañan cada corrida."""
from __future__ import annotations

import difflib
from datetime import date

import pandas as pd

from .config import CAIDA_FILAS_ALERTA, DIAS_REPORTE_VIEJO, PREFIJOS_NUEVOS
from .maestro import Maestro
from .reporte import ReporteLimpio
from .tipos import AVISO, INFO, Hallazgo
from .util import clave, texto_busqueda


def _palabras(texto: str) -> set[str]:
    return {w for w in texto_busqueda(texto).replace('(', ' ').replace(')', ' ').split() if len(w) >= 5}


def controles_reporte(rep: ReporteLimpio, hoy: date | None = None, filas_previas: int | None = None) -> list[Hallazgo]:
    hoy = hoy or date.today()
    h: list[Hallazgo] = []
    if rep.fecha:
        dias = (hoy - rep.fecha).days
        if dias > DIAS_REPORTE_VIEJO:
            h.append(Hallazgo(AVISO, "Reporte", rep.nombre, f"El reporte tiene {dias} días de antigüedad (fecha {rep.fecha:%d/%m/%Y})."))
        elif dias < 0:
            h.append(Hallazgo(AVISO, "Reporte", rep.nombre, "La fecha del reporte es posterior a hoy; revisa el nombre del archivo."))
    if filas_previas and len(rep.lineas) < filas_previas * CAIDA_FILAS_ALERTA:
        h.append(
            Hallazgo(
                AVISO, "Reporte", rep.nombre,
                f"El reporte trae {len(rep.lineas)} filas y el anterior {filas_previas}: puede estar incompleto.",
            )
        )
    h.append(
        Hallazgo(INFO, "Reporte", rep.nombre, f"{len(rep.lineas)} filas, {rep.lineas['clave'].nunique()} artículos, {rep.almacenes} almacenes.")
    )
    return h


def controles_cruce(maestro: Maestro, stock: pd.DataFrame) -> tuple[list[Hallazgo], pd.DataFrame, pd.DataFrame]:
    """Devuelve (hallazgos, sospechosos, nuevos).

    sospechosos: productos del maestro que controlan stock y cuyo código no aparece en el reporte
                 (con la sugerencia del código más parecido, para detectar errores de tipeo).
    nuevos:      posibles accesorios del reporte con stock que no están en el maestro.
    """
    h: list[Hallazgo] = []
    activos = maestro.items[maestro.items["activo"] == True]  # noqa: E712
    claves_reporte = list(stock.index)
    conjunto = set(claves_reporte)

    filas_sosp = []
    for _, r in activos[activos["controla_stock"] == True].iterrows():  # noqa: E712
        codigos = r["codigos_sistema"]
        if not codigos or any(clave(c) in conjunto for c in codigos):
            continue
        sugerencia, motivo, desc_sug, firme = "", "", "", False
        palabras = _palabras(r["nombre"])
        for c in codigos:
            k = clave(c)
            variantes = [x for x in claves_reporte if x.startswith(k) or (len(x) > 6 and k.startswith(x))]
            if variantes:
                x = variantes[0]
                sugerencia, motivo, desc_sug, firme = stock.at[x, "codigo"], "variante del mismo código", stock.at[x, "descripcion"], True
                break
            for x in difflib.get_close_matches(k, claves_reporte, n=3, cutoff=0.85):
                if palabras & _palabras(stock.at[x, "descripcion"]):
                    sugerencia, motivo, desc_sug = stock.at[x, "codigo"], "código y descripción parecidos (puede ser otro producto)", stock.at[x, "descripcion"]
                    break
            if sugerencia:
                break
        filas_sosp.append(
            {
                "codigo": r["codigo"], "nombre": r["nombre"], "codigo_sistema": " ; ".join(codigos),
                "sugerencia_del_reporte": sugerencia, "descripcion_en_reporte": desc_sug, "motivo": motivo, "firme": firme,
            }
        )
    sospechosos = pd.DataFrame(
        filas_sosp,
        columns=["codigo", "nombre", "codigo_sistema", "sugerencia_del_reporte", "descripcion_en_reporte", "motivo", "firme"],
    )
    for _, r in sospechosos[sospechosos["sugerencia_del_reporte"] != ""].iterrows():
        nivel = AVISO if r["firme"] else INFO
        h.append(
            Hallazgo(nivel, "Código sospechoso", r["codigo"],
                     f"«{r['nombre']}» no aparece en el reporte como {r['codigo_sistema']}, pero sí {r['sugerencia_del_reporte']} "
                     f"(«{r['descripcion_en_reporte']}»): {r['motivo']}.")
        )
    sin_sug = int((sospechosos["sugerencia_del_reporte"] == "").sum())
    if sin_sug:
        h.append(Hallazgo(INFO, "Sin stock", f"{sin_sug} productos", "No aparecen en el reporte y no hay códigos parecidos: se muestran sin stock."))
    sospechosos = sospechosos.drop(columns=["firme"])

    usados = {clave(c) for _, r in maestro.items.iterrows() for c in r["codigos_sistema"]}
    candidatos = stock[stock["codigo"].str.upper().str.startswith(tuple(p.upper() for p in PREFIJOS_NUEVOS)) & (stock["stock"] > 0)]
    nuevos = candidatos[~candidatos.index.isin(usados)][["codigo", "descripcion", "stock", "pvp_bs"]].reset_index(drop=True)
    nuevos.columns = ["codigo_sistema", "descripcion", "stock", "pvp_bs"]
    if len(nuevos):
        h.append(
            Hallazgo(INFO, "Nuevos sin clasificar", f"{len(nuevos)} artículos",
                     "Hay posibles accesorios en el reporte que no están en el maestro (ver la lista).")
        )
    return h, sospechosos, nuevos
