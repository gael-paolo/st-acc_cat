from __future__ import annotations

from dataclasses import dataclass

ERROR = "error"   # bloquea la publicación
AVISO = "aviso"   # conviene revisar antes de publicar
INFO = "info"     # contexto, no requiere acción


@dataclass(frozen=True)
class Hallazgo:
    nivel: str
    categoria: str
    referencia: str
    detalle: str

    def como_dict(self) -> dict:
        return {
            "nivel": self.nivel,
            "categoria": self.categoria,
            "referencia": self.referencia,
            "detalle": self.detalle,
        }
