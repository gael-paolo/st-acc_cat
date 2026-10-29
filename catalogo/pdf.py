"""PDF del catálogo con reportlab (Python puro: no necesita navegador ni librerías del sistema).

Pensado para entornos con poca memoria: las fotos se reducen a JPEG pequeños, se leen una sola vez y se
dibujan de a una tarjeta por vez; no se arma ningún documento intermedio en memoria.
"""
from __future__ import annotations

import io
from datetime import datetime
from typing import Callable

from PIL import Image
from reportlab import rl_config
from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

from .config import NOMBRE_CATALOGO

# Sin codificación ASCII85: el PDF es más pequeño y se genera mucho más rápido (el codificador es Python puro).
rl_config.useA85 = 0

PRIMARIO = HexColor("#0F4C5C")
ACENTO = HexColor("#D4520A")
TEXTO = HexColor("#1B1F23")
TEXTO2 = HexColor("#5B6470")
BORDE = HexColor("#E3DDD2")
FOTO = HexColor("#EFEAE1")
CLARO = HexColor("#CFE3E8")
BADGE = {
    "disponible": (HexColor("#DDF1E2"), HexColor("#1E5E34")),
    "pocas": (HexColor("#FFF1D6"), HexColor("#7A4A00")),
    "agotado": (HexColor("#ECEBE8"), HexColor("#55504A")),
}
ETIQUETA = {"disponible": "Disponible", "pocas": "Pocas unidades", "agotado": "Sin stock"}

W, H = A4
M = 28          # margen
GAP = 10
COLS = 3
CW = (W - 2 * M - (COLS - 1) * GAP) / COLS
ROW_H = 232
IMG_H = 122
PIE = 30
LADO_FOTO = 420  # px del lado mayor de las fotos dentro del PDF


def _t(texto: object) -> str:
    """Texto compatible con las fuentes estándar del PDF (WinAnsi)."""
    return str(texto).encode("cp1252", "replace").decode("cp1252")


def _precio(v) -> str:
    if v is None:
        return "Consultar"
    n = float(v)
    ent = abs(n - round(n)) < 0.005
    s = f"{abs(n):,.0f}" if ent else f"{abs(n):,.2f}"
    if abs(n) < 10000:
        s = s.replace(",", "")
    s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return "USD " + s


def _cortar(texto: str, fuente: str, tam: float, ancho: float) -> str:
    texto = _t(texto)
    if stringWidth(texto, fuente, tam) <= ancho:
        return texto
    while texto and stringWidth(texto + "…", fuente, tam) > ancho:
        texto = texto[:-1]
    return texto.rstrip() + "..."


class GeneradorPDF:
    def __init__(
        self,
        productos: list[dict],
        modelos: dict[str, str],
        fecha_stock: datetime,
        leer_imagen: Callable[[str], bytes],
        titulo: str = NOMBRE_CATALOGO,
    ):
        self.productos = productos
        self.nombres = modelos
        self.fecha = fecha_stock
        self._leer = leer_imagen
        self.titulo = titulo
        self._imagenes: dict[str, ImageReader | None] = {}

    # ------------------------------------------------------------ datos
    def modelos(self) -> list[str]:
        vistos: list[str] = []
        for p in self.productos:
            for m in p["modelos"]:
                if m not in vistos:
                    vistos.append(m)
        return sorted(vistos, key=lambda c: (c not in self.nombres, c))

    def nombre_modelo(self, codigo: str) -> str:
        n = self.nombres.get(codigo, "")
        return f"{codigo} · {n}" if n else codigo

    def _seccion(self, codigo: str) -> dict:
        def de(tipo):
            return [p for p in self.productos if p["tipo"] == tipo and codigo in p["modelos"]]

        return {
            "titulo": self.nombre_modelo(codigo),
            "tarjetas": de("Accesorio"),
            "tablas": [("Mantenimiento", de("Mantenimiento")), ("Color Studio", de("Color Studio"))],
        }

    def _imagen(self, stem: str) -> ImageReader | None:
        if stem not in self._imagenes:
            try:
                with Image.open(io.BytesIO(self._leer(stem))) as im:
                    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
                        im = im.convert("RGBA")
                        fondo = Image.new("RGB", im.size, (255, 255, 255))
                        fondo.paste(im, mask=im.getchannel("A"))
                        im = fondo
                    else:
                        im = im.convert("RGB")
                    im.thumbnail((LADO_FOTO, LADO_FOTO), Image.LANCZOS)
                    buf = io.BytesIO()
                    im.save(buf, "JPEG", quality=72, optimize=True)
                self._imagenes[stem] = ImageReader(io.BytesIO(buf.getvalue()))
            except Exception:
                self._imagenes[stem] = None
        return self._imagenes[stem]

    # ------------------------------------------------------------ salidas
    def pdf_modelo(self, codigo: str) -> bytes:
        return self._render([self._seccion(codigo)], [], f"{self.titulo} - {self.nombre_modelo(codigo)}")

    def pdf_completo(self) -> bytes:
        secciones = [self._seccion(m) for m in self.modelos()]
        generales = [p for p in self.productos if p["tipo"] == "Color Studio" and not p["modelos"]]
        extra = [("Color Studio (aplica a todos los modelos)", generales)] if generales else []
        return self._render(secciones, extra, self.titulo)

    # ------------------------------------------------------------ dibujo
    def _render(self, secciones: list[dict], extra: list[tuple[str, list[dict]]], titulo_doc: str) -> bytes:
        buf = io.BytesIO()
        c = Canvas(buf, pagesize=A4, pageCompression=1)
        c.setTitle(_t(titulo_doc))
        pagina = [0]
        fecha = self.fecha.strftime("%d/%m/%Y")

        def pie():
            c.setFont("Helvetica", 7.5)
            c.setFillColor(TEXTO2)
            c.drawString(M, 14, _t(f"{self.titulo} · Stock al {fecha} · Precios en USD"))
            c.drawRightString(W - M, 14, f"Página {pagina[0]}")

        def nueva(titulo: str, primera: bool) -> float:
            if pagina[0]:
                pie()
                c.showPage()
            pagina[0] += 1
            alto = 58 if primera else 24
            c.setFillColor(PRIMARIO)
            c.rect(0, H - alto, W, alto, stroke=0, fill=1)
            if primera:
                c.setFillColor(white)
                c.setFont("Helvetica-Bold", 17)
                c.drawString(M, H - 26, _t(titulo))
                c.setFillColor(CLARO)
                c.setFont("Helvetica", 9)
                c.drawString(M, H - 44, _t(f"{self.titulo} · Stock al {fecha} · Precios en USD"))
            else:
                c.setFillColor(white)
                c.setFont("Helvetica-Bold", 10)
                c.drawString(M, H - 16, _t(titulo + " (continuación)"))
            return H - alto - 14

        def tarjeta(x: float, ytop: float, p: dict) -> None:
            c.setFillColor(white)
            c.setStrokeColor(BORDE)
            c.setLineWidth(0.8)
            c.roundRect(x, ytop - ROW_H, CW, ROW_H, 8, stroke=1, fill=1)
            img = self._imagen(p["imgs"][0]) if p["imgs"] else None
            if img is not None:
                c.drawImage(img, x + 4, ytop - IMG_H - 2, width=CW - 8, height=IMG_H - 4, preserveAspectRatio=True, anchor="c")
            else:
                c.setFillColor(TEXTO2)
                c.setFont("Helvetica", 8)
                c.drawCentredString(x + CW / 2, ytop - IMG_H / 2, "Sin foto")
            c.setStrokeColor(BORDE)
            c.line(x, ytop - IMG_H - 4, x + CW, ytop - IMG_H - 4)
            ty = ytop - IMG_H - 18
            if p["modelos"]:
                c.setFillColor(PRIMARIO)
                c.setFont("Helvetica-Bold", 7)
                c.drawString(x + 8, ty, _cortar("   ".join(p["modelos"]), "Helvetica-Bold", 7, CW - 16))
            ty -= 13
            lineas = simpleSplit(_t(p["nombre"]), "Helvetica-Bold", 8.6, CW - 16)
            if len(lineas) > 3:
                lineas = lineas[:3]
                lineas[2] = _cortar(lineas[2] + " ...", "Helvetica-Bold", 8.6, CW - 16)
            c.setFillColor(TEXTO)
            c.setFont("Helvetica-Bold", 8.6)
            for ln in lineas:
                c.drawString(x + 8, ty, ln)
                ty -= 10.6
            if p["np"]:
                c.setFillColor(TEXTO2)
                c.setFont("Helvetica", 7)
                c.drawString(x + 8, ty - 1, _cortar("Cód. " + p["np"], "Helvetica", 7, CW - 16))
            by = ytop - ROW_H + 11
            c.setFillColor(PRIMARIO)
            c.setFont("Helvetica-Bold", 12)
            c.drawString(x + 8, by, _precio(p["precio"]))
            if p["estado"] in ETIQUETA:
                etq = ETIQUETA[p["estado"]]
                if p["estado"] != "agotado" and p["stock"] is not None:
                    etq += f" · {p['stock']}"
                etq = _t(etq)
                ancho = stringWidth(etq, "Helvetica-Bold", 7) + 12
                fondo, texto = BADGE[p["estado"]]
                c.setFillColor(fondo)
                c.roundRect(x + CW - 8 - ancho, by - 3.5, ancho, 13, 6.5, stroke=0, fill=1)
                c.setFillColor(texto)
                c.setFont("Helvetica-Bold", 7)
                c.drawString(x + CW - 8 - ancho + 6, by + 0.5, etq)

        def tabla(y: float, titulo_sec: str, nombre: str, filas: list[dict]) -> float:
            if not filas:
                return y
            if y - 60 < PIE:
                y = nueva(titulo_sec, False)
            c.setFillColor(PRIMARIO)
            c.setFont("Helvetica-Bold", 10.5)
            c.drawString(M, y - 10, _t(nombre))
            c.setStrokeColor(PRIMARIO)
            c.setLineWidth(1)
            c.line(M, y - 15, W - M, y - 15)
            y -= 28
            for i, p in enumerate(filas):
                if y < PIE + 6:
                    y = nueva(titulo_sec, False)
                    c.setFillColor(PRIMARIO)
                    c.setFont("Helvetica-Bold", 9)
                    c.drawString(M, y - 10, _t(nombre + " (continuación)"))
                    y -= 24
                if i % 2 == 0:
                    c.setFillColor(HexColor("#F6F3EE"))
                    c.rect(M, y - 4, W - 2 * M, 15, stroke=0, fill=1)
                c.setFillColor(TEXTO)
                c.setFont("Helvetica", 8.6)
                precio = _precio(p["precio"])
                c.drawString(M + 6, y, _cortar(p["nombre"], "Helvetica", 8.6, W - 2 * M - 110))
                c.setFont("Helvetica-Bold", 9)
                c.setFillColor(PRIMARIO)
                c.drawRightString(W - M - 6, y, precio)
                y -= 15
            return y - 8

        primera_vez = True
        for sec in secciones:
            y = nueva(sec["titulo"], True)
            primera_vez = False
            for i, p in enumerate(sec["tarjetas"]):
                col = i % COLS
                if col == 0 and y - ROW_H < PIE:
                    y = nueva(sec["titulo"], False)
                tarjeta(M + col * (CW + GAP), y, p)
                if col == COLS - 1 or i == len(sec["tarjetas"]) - 1:
                    y -= ROW_H + GAP
            for nombre, filas in sec["tablas"]:
                y = tabla(y, sec["titulo"], nombre, filas)
        for nombre, filas in extra:
            y = nueva(nombre, True)
            tabla(y, nombre, "Detalle", filas)
        if primera_vez and not extra:
            y = nueva(titulo_doc, True)
            c.setFillColor(TEXTO2)
            c.setFont("Helvetica", 10)
            c.drawString(M, y - 10, "No hay productos para mostrar.")
        pie()
        c.showPage()
        c.save()
        return buf.getvalue()
