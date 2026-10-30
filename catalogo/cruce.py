"""Cruce del maestro con el stock consolidado -> lista de productos del catálogo."""
from __future__ import annotations

import pandas as pd

from .config import UMBRAL_POCAS_UNIDADES
from .imagenes import ArchivoImagen, imagenes_de_producto
from .maestro import Maestro
from .util import clave, numero_entero_si_corresponde

DISPONIBLE, POCAS, AGOTADO, SERVICIO = "disponible", "pocas", "agotado", "servicio"


def estado_de(stock: float, umbral: int = UMBRAL_POCAS_UNIDADES) -> str:
    if stock <= 0:
        return AGOTADO
    if stock <= umbral:
        return POCAS
    return DISPONIBLE


def stock_de_producto(codigos: list[str], stock: pd.DataFrame) -> tuple[float, bool]:
    """Suma el stock de todos los códigos del producto. Devuelve (total, alguno_visto_en_el_reporte)."""
    total, visto = 0.0, False
    for c in codigos:
        k = clave(c)
        if k in stock.index:
            visto = True
            total += float(stock.at[k, "stock"])
    return total, visto


def _sin_repetidas(stems: list[str], listado: dict[str, ArchivoImagen]) -> list[str]:
    """Quita fotos idénticas (misma huella) dentro de un mismo producto."""
    vistas, out = set(), []
    for s in stems:
        h = listado[s].md5
        if h not in vistas:
            vistas.add(h)
            out.append(s)
    return out


def pvp_de_producto(codigos: list[str], stock: pd.DataFrame) -> float | None:
    """PVP en Bs del reporte: el del primer código del producto que lo tenga (mayor que cero)."""
    for c in codigos:
        k = clave(c)
        if k in stock.index:
            v = stock.at[k, "pvp_bs"]
            if pd.notna(v) and float(v) > 0:
                return float(v)
    return None


def construir_catalogo(
    maestro: Maestro,
    stock: pd.DataFrame,
    imagenes_disponibles: dict[str, ArchivoImagen],
    umbral: int = UMBRAL_POCAS_UNIDADES,
) -> list[dict]:
    productos = []
    activos = maestro.items[maestro.items["activo"] == True]  # noqa: E712
    for _, r in activos.sort_values(["orden", "nombre"]).iterrows():
        controla = bool(r["controla_stock"])
        if controla:
            total, _ = stock_de_producto(r["codigos_sistema"], stock)
            cantidad = numero_entero_si_corresponde(total)
            estado = estado_de(total, umbral)
            pvp_bs = pvp_de_producto(r["codigos_sistema"], stock)
        else:
            cantidad, estado, pvp_bs = None, SERVICIO, None
        productos.append(
            {
                "id": r["codigo"],
                "nombre": r["nombre"],
                "descripcion": r["descripcion"],
                "tipo": r["tipo"],
                "categoria": r["categoria"],
                "modelos": list(r["modelos"]),
                "precio": float(r["precio_usd"]) if pd.notna(r["precio_usd"]) else None,
                "precio_bs": pvp_bs,
                "np": " / ".join(r["codigos_sistema"]),
                "stock": cantidad,
                "estado": estado,
                "imgs": _sin_repetidas(imagenes_de_producto(r["codigo"], r["imagenes_explicitas"], set(imagenes_disponibles)), imagenes_disponibles),
            }
        )
    return productos


def resumen(productos: list[dict]) -> dict:
    con_control = [p for p in productos if p["estado"] != SERVICIO]
    return {
        "productos": len(productos),
        "con_control_de_stock": len(con_control),
        DISPONIBLE: sum(p["estado"] == DISPONIBLE for p in productos),
        POCAS: sum(p["estado"] == POCAS for p in productos),
        AGOTADO: sum(p["estado"] == AGOTADO for p in productos),
        "sin_foto": sum(1 for p in productos if p["tipo"] == "Accesorio" and not p["imgs"]),
    }
