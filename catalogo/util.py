from __future__ import annotations

import os
import re
import unicodedata


def sin_acentos(texto: object) -> str:
    s = unicodedata.normalize("NFKD", str(texto))
    return s.encode("ascii", "ignore").decode()


def clave(codigo: object) -> str:
    """Forma normalizada de un código para cruzar: mayúsculas y solo letras y números."""
    return re.sub(r"[^A-Z0-9]", "", sin_acentos(codigo).upper())


def stem(nombre_archivo: str) -> str:
    return os.path.splitext(os.path.basename(str(nombre_archivo)))[0]


def texto_busqueda(texto: object) -> str:
    return re.sub(r"\s+", " ", sin_acentos(texto).lower()).strip()


def numero_entero_si_corresponde(valor: float):
    """5.0 -> 5; 2.5 -> 2.5."""
    if abs(valor - round(valor)) < 1e-9:
        return int(round(valor))
    return round(valor, 2)
