"""Revisión de la configuración (secretos de Streamlit) y prueba de conexión con Drive.

Nunca devuelve ni muestra valores secretos: solo si existen y si tienen la forma esperada.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable

OK, FALTA, INVALIDO, RECOMENDADO, OPCIONAL = "ok", "falta", "invalido", "recomendado", "opcional"

CLAVES_CUENTA = ("type", "project_id", "private_key_id", "private_key", "client_email", "token_uri")
CLAVES_DRIVE = {
    "DRIVE_REPORTES_ID": "Carpeta de reportes de stock",
    "DRIVE_MAESTRO_ID": "Archivo maestro (Google Sheet o .xlsx)",
    "DRIVE_IMAGENES_ID": "Carpeta de fotos",
}
CLAVES_FTP = ("TAIYO_FTP_HOST", "TAIYO_FTP_USER", "TAIYO_FTP_PASS")
NOMBRE_CUENTA = "Cuenta de servicio"


@dataclass(frozen=True)
class Chequeo:
    nombre: str
    estado: str
    detalle: str

    @property
    def es_problema(self) -> bool:
        return self.estado in (FALTA, INVALIDO)


_MARCADOR = re.compile(r"PEGA_AQUI|PON_AQUI|ID_O_URL", re.I)


def es_marcador(valor: Any) -> bool:
    """¿Sigue siendo el texto de ejemplo de la plantilla (sin reemplazar)?"""
    return bool(_MARCADOR.search(str(valor or "")))


def extraer_id(valor: Any) -> str:
    """Acepta el ID o la URL completa de Drive/Sheets y devuelve el ID."""
    s = str(valor or "").strip()
    m = re.search(r"/(?:folders|d)/([A-Za-z0-9_-]{10,})", s) or re.search(r"[?&]id=([A-Za-z0-9_-]{10,})", s)
    return m.group(1) if m else s


def cuenta_de_servicio(get: Callable[[str], Any]) -> tuple[dict | None, str]:
    """(datos, error). Acepta la tabla [gcp_service_account] o el JSON completo como texto."""
    crudo = get("gcp_service_account")
    if crudo is None:
        crudo = get("gcp_service_account_json")
    if crudo is None or crudo == "":
        return None, "No hay [gcp_service_account] ni gcp_service_account_json."
    if isinstance(crudo, str):
        try:
            crudo = json.loads(crudo)
        except ValueError:
            return None, "El texto de la cuenta de servicio no es un JSON válido."
    try:
        return dict(crudo), ""
    except (TypeError, ValueError):
        return None, "La cuenta de servicio debe ser una tabla TOML o un JSON."


def ids_de_drive(get: Callable[[str], Any]) -> dict[str, str]:
    return {k: extraer_id(get(k)) for k in CLAVES_DRIVE if get(k)}


def _chequeo_cuenta(get) -> Chequeo:
    info, error = cuenta_de_servicio(get)
    nombre = NOMBRE_CUENTA
    if info is None:
        return Chequeo(nombre, FALTA, error)
    faltan = [k for k in CLAVES_CUENTA if not str(info.get(k, "")).strip()]
    if faltan:
        return Chequeo(nombre, FALTA, "Faltan campos: " + ", ".join(faltan))
    pendientes = [k for k, v in info.items() if isinstance(v, str) and es_marcador(v)]
    if pendientes:
        return Chequeo(nombre, INVALIDO, "Quedan textos de ejemplo (PEGA_AQUI) sin reemplazar en: " + ", ".join(sorted(pendientes)) + ". Pega el contenido completo del archivo JSON.")
    if info["type"] != "service_account":
        return Chequeo(nombre, INVALIDO, f"«type» debe ser service_account (es «{info['type']}»).")
    clave = str(info["private_key"])
    if not clave.strip().startswith("-----BEGIN PRIVATE KEY-----") or "\n" not in clave.strip():
        return Chequeo(
            nombre, INVALIDO,
            "private_key no tiene el formato esperado. En TOML debe ir entre comillas dobles y con los saltos de línea como \\n "
            "(copia el valor tal cual del archivo JSON).",
        )
    email = str(info["client_email"])
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.iam\.gserviceaccount\.com", email):
        return Chequeo(nombre, INVALIDO, "client_email no parece el correo de una cuenta de servicio (termina en .iam.gserviceaccount.com).")
    return Chequeo(nombre, OK, f"Comparte tus carpetas de Drive con: {email}")


def revisar_secretos(get: Callable[[str], Any]) -> list[Chequeo]:
    """Revisa los secretos de la app. `get(clave)` devuelve el valor o None."""
    out: list[Chequeo] = []

    clave = get("APP_PASSWORD")
    if clave and es_marcador(clave):
        out.append(Chequeo("APP_PASSWORD", INVALIDO, "Sigue con el texto de ejemplo: escribe tu propia contraseña."))
    elif not clave:
        out.append(Chequeo("APP_PASSWORD", FALTA, "Obligatoria: sin ella la app no se muestra (protege la generación de archivos)."))
    elif len(str(clave)) < 8:
        out.append(Chequeo("APP_PASSWORD", RECOMENDADO, "Existe, pero es corta (menos de 8 caracteres)."))
    else:
        out.append(Chequeo("APP_PASSWORD", OK, "Definida."))

    out.append(_chequeo_cuenta(get))

    vistos: dict[str, str] = {}
    for k, descripcion in CLAVES_DRIVE.items():
        crudo = get(k)
        if not crudo:
            out.append(Chequeo(k, FALTA, f"{descripcion}: falta definirlo."))
            continue
        if es_marcador(crudo):
            out.append(Chequeo(k, INVALIDO, f"{descripcion}: sigue con el texto de ejemplo; pega el ID o la URL real."))
            continue
        valor = extraer_id(crudo)
        if not re.fullmatch(r"[A-Za-z0-9_-]{15,}", valor):
            out.append(Chequeo(k, INVALIDO, f"{descripcion}: no parece un ID de Drive (es el tramo final de la URL, sin espacios)."))
            continue
        if valor in vistos:
            out.append(Chequeo(k, INVALIDO, f"{descripcion}: repite el ID de {vistos[valor]}; deben ser distintos."))
            continue
        vistos[valor] = k
        nota = " (se tomó el ID de la URL)" if valor != str(crudo).strip() else ""
        out.append(Chequeo(k, OK, f"{descripcion}: definido{nota}."))

    ftp = [k for k in CLAVES_FTP if get(k)]
    if not ftp:
        out.append(Chequeo("TAIYO_FTP_*", OPCIONAL, "No configurado (la subida por FTP es una fase futura)."))
    elif len(ftp) < len(CLAVES_FTP):
        faltan = ", ".join(k for k in CLAVES_FTP if k not in ftp)
        out.append(Chequeo("TAIYO_FTP_*", INVALIDO, f"Configuración incompleta; falta: {faltan}."))
    else:
        out.append(Chequeo("TAIYO_FTP_*", OK, "Configurado: la app mostrará la opción de publicar por FTP."))
    return out


def drive_listo(chequeos: list[Chequeo]) -> bool:
    obligatorios = {NOMBRE_CUENTA, *CLAVES_DRIVE}
    return all(c.estado == OK for c in chequeos if c.nombre in obligatorios)


# ---------------------------------------------------------------- prueba de conexión
def _explicar(error: Exception, email: str, que: str) -> str:
    t = str(error).lower()
    if "has not been used" in t or "accessnotconfigured" in t or "api has not" in t or "disabled" in t:
        return "La API de Google Drive no está habilitada en el proyecto de la cuenta de servicio. Habilítala en «APIs y servicios → Biblioteca»."
    if "404" in t or "not found" in t or "notfound" in t:
        return f"No se encontró {que}. Revisa el ID y comparte el elemento con {email} (rol Lector)."
    if "403" in t or "forbidden" in t or "insufficient" in t or "permission" in t:
        return f"Sin permiso para leer {que}. Compártelo con {email} (rol Lector)."
    if "invalid_grant" in t or "invalid jwt" in t or "private key" in t or "could not deserialize" in t:
        return "La clave de la cuenta de servicio no es válida. Vuelve a copiarla del JSON descargado."
    return f"No se pudo leer {que}: {error}"


def probar_drive(
    servicio: Any,
    ids: dict[str, str],
    email: str,
    descargar_fn: Callable[[Any, Any], bytes],
    leer_maestro_fn: Callable[[bytes], Any],
) -> list[Chequeo]:
    """Lee de verdad las tres fuentes de Drive y dice qué encontró (o qué falta compartir)."""
    from . import drive

    out: list[Chequeo] = []

    def carpeta(clave: str, etiqueta: str):
        """Comprueba que el ID sea una carpeta accesible. Devuelve (carpeta | None, chequeo de error | None)."""
        try:
            elemento = drive.resolver_archivo(servicio, ids[clave])
        except Exception as e:
            return None, Chequeo(etiqueta, INVALIDO, _explicar(e, email, etiqueta.lower().replace("carpeta de", "la carpeta de")))
        if elemento.mime != drive.MIME_FOLDER:
            return None, Chequeo(
                etiqueta, INVALIDO,
                f"El ID apunta a «{elemento.nombre}», que no es una carpeta. Abre la carpeta correcta en Drive y copia su URL.",
            )
        return elemento, None

    nombre = "Carpeta de reportes"
    elemento, fallo = carpeta("DRIVE_REPORTES_ID", nombre)
    if fallo:
        out.append(fallo)
    else:
        try:
            archivos = drive.listar_carpeta(servicio, ids["DRIVE_REPORTES_ID"])
            ultimo = drive.ultimo_reporte(archivos)
            out.append(Chequeo(nombre, OK, f"«{elemento.nombre}»: {len(archivos)} archivos; el más reciente que usaría: «{ultimo.nombre}»."))
        except drive.ErrorDrive as e:
            out.append(Chequeo(nombre, INVALIDO, str(e) if "No hay reportes" in str(e) else _explicar(e, email, "la carpeta de reportes")))
        except Exception as e:
            out.append(Chequeo(nombre, INVALIDO, _explicar(e, email, "la carpeta de reportes")))

    nombre = "Maestro"
    try:
        archivo = drive.resolver_archivo(servicio, ids["DRIVE_MAESTRO_ID"])
        m = leer_maestro_fn(descargar_fn(servicio, archivo))
        n = len(m.items)
        out.append(Chequeo(nombre, OK, f"«{archivo.nombre}» legible: {n} filas en la hoja Maestro."))
    except Exception as e:
        texto = str(e)
        if isinstance(e, drive.ErrorDrive) and ("es una carpeta" in texto or "documento de Google de tipo" in texto or "acceso directo" in texto):
            out.append(Chequeo(nombre, INVALIDO, texto))
        elif "Maestro" in texto and "hoja" in texto.lower():
            out.append(Chequeo(nombre, INVALIDO, f"Se pudo abrir el archivo pero: {texto}"))
        else:
            out.append(Chequeo(nombre, INVALIDO, _explicar(e, email, "el archivo maestro")))

    nombre = "Carpeta de fotos"
    elemento, fallo = carpeta("DRIVE_IMAGENES_ID", nombre)
    if fallo:
        out.append(fallo)
    else:
        try:
            fotos = drive.FuenteDrive(servicio, ids["DRIVE_IMAGENES_ID"]).listar()
            if fotos:
                out.append(Chequeo(nombre, OK, f"«{elemento.nombre}»: {len(fotos)} fotos reconocidas (ejemplo: {next(iter(fotos))})."))
            else:
                out.append(Chequeo(nombre, INVALIDO, "La carpeta se puede leer, pero no tiene imágenes (JPG, PNG, WebP…) directamente dentro."))
        except Exception as e:
            out.append(Chequeo(nombre, INVALIDO, _explicar(e, email, "la carpeta de fotos")))
    return out
