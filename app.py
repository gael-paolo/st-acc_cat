"""App de Streamlit: el área comercial revisa los datos y publica el catálogo.

Flujo: 1 Datos -> 2 Revisión -> 3 Vista previa -> 4 Descargas (HTML y PDF).
La lógica vive en el paquete `catalogo`; esta pantalla solo la orquesta.
"""
from __future__ import annotations

import hmac
import os
import tempfile
import time
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from catalogo.config import NOMBRE_CATALOGO, UMBRAL_POCAS_UNIDADES
from catalogo.diagnostico import (
    FALTA, INVALIDO, OK, OPCIONAL, RECOMENDADO, cuenta_de_servicio, drive_listo, ids_de_drive, probar_drive, revisar_secretos,
)
from catalogo.imagenes import FuenteCarpetaLocal, FuenteConCache, FuenteMemoria
from catalogo.maestro import MaestroInvalido, leer_maestro
from catalogo.pipeline import analizar, generar_paquete, generar_sitio, html_vista_previa, zip_archivos
from catalogo.publicar import ConfigFTP, ErrorPublicacion, leer_manifest_remoto, publicar
from catalogo.reporte import ReporteInvalido

st.set_page_config(page_title=NOMBRE_CATALOGO, layout="wide")


@st.cache_resource
def cache_de_fotos() -> dict:
    return {}


# ---------------------------------------------------------------- configuración
def secreto(clave: str, defecto=None):
    """Lee de st.secrets y, si no está, de las variables de entorno."""
    try:
        if clave in st.secrets:
            return st.secrets[clave]
    except Exception:
        pass
    return os.environ.get(clave, defecto)


def config_ftp() -> ConfigFTP | None:
    entorno = {k: secreto(k) for k in (
        "TAIYO_FTP_HOST", "TAIYO_FTP_USER", "TAIYO_FTP_PASS", "TAIYO_FTP_PORT",
        "TAIYO_FTP_REMOTE_DIR", "TAIYO_FTP_SSL", "TAIYO_SITE_URL") if secreto(k) is not None}
    try:
        return ConfigFTP.desde_entorno(entorno)
    except ErrorPublicacion:
        return None


def drive_configurado() -> bool:
    return drive_listo(revisar_secretos(secreto))


ESTADOS = {OK: "OK", FALTA: "Falta", INVALIDO: "Revisar", RECOMENDADO: "Recomendado", OPCIONAL: "Opcional"}


def panel_configuracion() -> None:
    """Estado de los secretos (sin mostrar valores) y prueba de conexión con Drive."""
    chequeos = revisar_secretos(secreto)
    problemas = [c for c in chequeos if c.es_problema]
    etiqueta = "Estado de la configuración" + (f" · {len(problemas)} por resolver" if problemas else " · todo en orden")
    with st.expander(etiqueta, expanded=bool(problemas)):
        st.dataframe(
            pd.DataFrame([{"Elemento": c.nombre, "Estado": ESTADOS[c.estado], "Detalle": c.detalle} for c in chequeos]),
            hide_index=True, width="stretch",
        )
        st.caption("Aquí solo se muestran los nombres y si están bien formados; nunca los valores. Guía completa: DESPLIEGUE.md.")
        if drive_listo(chequeos):
            st.caption("Los datos tienen buena forma. Pulsa el botón para comprobar que la app realmente puede leer tus archivos de Drive.")
            if st.button("Probar conexión con Drive"):
                with st.spinner("Leyendo Drive…"):
                    try:
                        from catalogo import drive

                        info, _ = cuenta_de_servicio(secreto)
                        servicio = drive.conectar(info)
                        resultados = probar_drive(servicio, ids_de_drive(secreto), info["client_email"], drive.descargar, leer_maestro)
                    except Exception as e:
                        st.error(str(e))
                    else:
                        for r in resultados:
                            (st.success if r.estado == OK else st.error)(f"**{r.nombre}**: {r.detalle}")
        elif not problemas:
            st.info("Drive no está configurado: puedes usar «Subir archivos» en la pestaña de datos.")


# ---------------------------------------------------------------- acceso
MAX_FALLOS = 5          # intentos fallidos antes de bloquear esta sesión
BLOQUEO_SEGUNDOS = 60


def puerta() -> None:
    """Pide la contraseña antes de mostrar nada. Sin APP_PASSWORD configurada, la app no se muestra."""
    clave = secreto("APP_PASSWORD")
    if not clave:
        if os.environ.get("CATALOGO_PERMITIR_SIN_CLAVE") == "1":   # solo para pruebas locales
            return
        st.title(NOMBRE_CATALOGO)
        st.error(
            "Esta app no tiene contraseña configurada, así que no se muestra. "
            "Define `APP_PASSWORD` en los secretos (Settings → Secrets) y vuelve a cargar la página."
        )
        st.stop()
    if st.session_state.get("acceso"):
        return

    st.title(NOMBRE_CATALOGO)
    bloqueado_hasta = st.session_state.get("bloqueo_hasta", 0.0)
    if time.time() < bloqueado_hasta:
        st.error(f"Demasiados intentos fallidos. Espera {int(bloqueado_hasta - time.time()) + 1} segundos y recarga la página.")
        st.stop()

    with st.form("form_acceso"):
        ingreso = st.text_input("Contraseña de acceso", type="password")
        enviar = st.form_submit_button("Entrar")
    if enviar:
        if hmac.compare_digest(str(ingreso).encode("utf-8"), str(clave).encode("utf-8")):
            st.session_state["acceso"] = True
            st.session_state["fallos"] = 0
            st.rerun()
        fallos = st.session_state.get("fallos", 0) + 1
        st.session_state["fallos"] = fallos
        time.sleep(1.5)                       # frena los intentos automáticos
        if fallos >= MAX_FALLOS:
            st.session_state["bloqueo_hasta"] = time.time() + BLOQUEO_SEGUNDOS
            st.session_state["fallos"] = 0
            st.error(f"Demasiados intentos fallidos. Espera {BLOQUEO_SEGUNDOS} segundos y recarga la página.")
        else:
            st.error("Contraseña incorrecta.")
    st.stop()


# ---------------------------------------------------------------- carga de datos
def cargar_desde_drive():
    from catalogo import drive

    info, _ = cuenta_de_servicio(secreto)
    ids = ids_de_drive(secreto)
    servicio = drive.conectar(info)
    reportes = drive.listar_carpeta(servicio, ids["DRIVE_REPORTES_ID"])
    rep = drive.ultimo_reporte(reportes)
    maestro_meta = drive.resolver_archivo(servicio, ids["DRIVE_MAESTRO_ID"])
    return {
        "reporte": drive.descargar(servicio, rep), "nombre_reporte": rep.nombre,
        "maestro": drive.descargar(servicio, maestro_meta),
        "fuente": drive.FuenteDrive(servicio, ids["DRIVE_IMAGENES_ID"]),
        "origen": f"Drive · reporte «{rep.nombre}» · maestro «{maestro_meta.nombre}» (modificado {maestro_meta.modificado[:10]})",
    }


def cargar_desde_carpeta(carpeta: Path):
    candidatos = sorted((carpeta / "reportes").glob("*.xls*"), key=lambda p: p.stat().st_mtime)
    if not candidatos:
        raise ReporteInvalido(f"No hay reportes en {carpeta / 'reportes'}.")
    rep = candidatos[-1]
    return {
        "reporte": rep.read_bytes(), "nombre_reporte": rep.name,
        "maestro": (carpeta / "maestro_accesorios.xlsx").read_bytes(),
        "fuente": FuenteCarpetaLocal(carpeta / "imagenes"),
        "origen": f"Carpeta local · reporte «{rep.name}»",
    }


def pantalla_datos(umbral: int) -> None:
    st.subheader("1 · Datos")
    modos = []
    if drive_configurado():
        modos.append("Google Drive")
    modos.append("Subir archivos")
    if os.environ.get("CATALOGO_DATOS"):
        modos.append("Carpeta local (pruebas)")
    modo = st.radio("Origen de los datos", modos, horizontal=True)

    entradas = None
    if modo == "Google Drive":
        st.caption("Toma el reporte más reciente de la carpeta de reportes, el maestro y las fotos de Drive.")
        if st.button("Cargar y analizar", type="primary"):
            with st.spinner("Leyendo Drive…"):
                try:
                    entradas = cargar_desde_drive()
                except Exception as e:
                    st.error(str(e))
    elif modo == "Subir archivos":
        c1, c2 = st.columns(2)
        rep = c1.file_uploader("Reporte de stock (.xls o .xlsx)", type=["xls", "xlsx"])
        mae = c2.file_uploader("Maestro de accesorios (.xlsx)", type=["xlsx"])
        fotos = st.file_uploader("Fotos (opcional: nombradas con el código, por ejemplo ACC-001.jpg)", type=["jpg", "jpeg", "png", "webp"], accept_multiple_files=True)
        if st.button("Analizar", type="primary", disabled=not (rep and mae)):
            entradas = {
                "reporte": rep.getvalue(), "nombre_reporte": rep.name, "maestro": mae.getvalue(),
                "fuente": FuenteMemoria({f.name: f.getvalue() for f in fotos or []}),
                "origen": f"Archivos subidos · reporte «{rep.name}»",
            }
    else:
        if st.button("Cargar y analizar", type="primary"):
            entradas = cargar_desde_carpeta(Path(os.environ["CATALOGO_DATOS"]))

    if entradas:
        entradas["fuente"] = FuenteConCache(entradas["fuente"], cache_de_fotos())
        with st.spinner("Limpiando el reporte y cruzando con el maestro…"):
            try:
                previas = st.session_state.get("filas_previas")
                res = analizar(
                    entradas["reporte"], entradas["nombre_reporte"], entradas["maestro"], entradas["fuente"],
                    umbral=umbral, filas_previas=previas,
                )
            except (ReporteInvalido, MaestroInvalido) as e:
                st.error(str(e))
                return
        st.session_state.update(res=res, fuente=entradas["fuente"], origen=entradas["origen"], vista=None, publicado=None, paquete=None)
        st.session_state["filas_previas"] = len(res.reporte.lineas)

    if st.session_state.get("res"):
        r = st.session_state["res"]
        st.success(f"{st.session_state['origen']} · stock al {r.fecha_stock:%d/%m/%Y}. Sigue con la pestaña «Revisión».")


# ---------------------------------------------------------------- revisión
def pantalla_revision() -> None:
    st.subheader("2 · Revisión")
    res = st.session_state.get("res")
    if not res:
        st.info("Primero carga los datos.")
        return
    s = res.resumen
    c = st.columns(5)
    c[0].metric("Productos activos", s["productos"])
    c[1].metric("Disponibles", s["disponible"])
    c[2].metric("Pocas unidades", s["pocas"])
    c[3].metric("Sin stock", s["agotado"])
    c[4].metric("Accesorios sin foto", s["sin_foto"])

    errores = [h for h in res.hallazgos if h.nivel == "error"]
    avisos = [h for h in res.hallazgos if h.nivel == "aviso"]
    if errores:
        st.error(f"{len(errores)} errores bloquean la publicación. Corrígelos en el maestro o en el reporte y vuelve a cargar.")
    elif avisos:
        st.warning(f"{len(avisos)} avisos para revisar. No bloquean la publicación.")
    else:
        st.success("Sin errores ni avisos.")

    niveles = st.multiselect("Mostrar", ["error", "aviso", "info"], default=["error", "aviso"])
    tabla = pd.DataFrame([h.como_dict() for h in res.hallazgos if h.nivel in niveles])
    if tabla.empty:
        st.caption("No hay hallazgos con esos niveles.")
    else:
        st.dataframe(tabla, hide_index=True, width="stretch")

    with st.expander(f"Productos del maestro que no aparecen en el reporte ({len(res.sospechosos)})"):
        st.caption("Salen sin stock. Si el código tiene un error de tipeo, la sugerencia ayuda a corregirlo en el maestro.")
        st.dataframe(res.sospechosos, hide_index=True, width="stretch")
        st.download_button("Descargar CSV", res.sospechosos.to_csv(index=False).encode("utf-8-sig"), "sospechosos.csv", "text/csv")
    with st.expander(f"Posibles accesorios del reporte que no están en el maestro ({len(res.nuevos)})"):
        st.caption("Artículos con stock del reporte (códigos ACCES-) que todavía no se clasificaron. Los que correspondan se agregan al maestro.")
        st.dataframe(res.nuevos, hide_index=True, width="stretch")
        st.download_button("Descargar CSV", res.nuevos.to_csv(index=False).encode("utf-8-sig"), "nuevos_sin_clasificar.csv", "text/csv")


# ---------------------------------------------------------------- vista previa
def pantalla_vista() -> None:
    st.subheader("3 · Vista previa")
    res = st.session_state.get("res")
    if not res:
        st.info("Primero carga los datos.")
        return
    st.caption("Así se verá el catálogo. La vista previa usa miniaturas; el archivo descargable incluye además una versión más grande de las fotos que lo permiten.")
    if st.button("Generar vista previa", type="primary"):
        with st.spinner("Preparando la vista previa…"):
            st.session_state["vista"] = html_vista_previa(res, st.session_state["fuente"])
    if st.session_state.get("vista") is None:
        return
    components.html(st.session_state["vista"], height=900, scrolling=True)


# ---------------------------------------------------------------- descargas
def pantalla_descargas() -> None:
    st.subheader("4 · Descargas")
    res = st.session_state.get("res")
    if not res:
        st.info("Primero carga los datos.")
        return
    if not res.puede_publicar:
        st.error("Hay errores que bloquean la generación de archivos. Revísalos en la pestaña «Revisión».")
        return

    avisos = [h for h in res.hallazgos if h.nivel == "aviso"]
    if avisos:
        with st.expander(f"{len(avisos)} avisos pendientes"):
            for h in avisos:
                st.write(f"**{h.categoria} · {h.referencia}**: {h.detalle}")

    st.caption("Genera los archivos y descárgalos para distribuirlos: el HTML para la intranet y los PDF para enviar a clientes.")
    c1, c2, c3 = st.columns(3)
    con_html = c1.checkbox("catalogo.html (un solo archivo)", value=True)
    con_completo = c2.checkbox("PDF completo", value=True)
    con_modelos = c3.checkbox("PDF por modelo", value=True)

    if st.button("Generar archivos", type="primary", disabled=not (con_html or con_completo or con_modelos)):
        barra = st.progress(0.0, text="Preparando…")
        try:
            archivos = generar_paquete(
                res, st.session_state["fuente"], con_html, con_completo, con_modelos,
                progreso=lambda f, t: barra.progress(min(f, 1.0), text=t),
            )
            st.session_state["paquete"] = {"archivos": archivos, "zip": zip_archivos(archivos), "fecha": res.fecha_stock}
            barra.progress(1.0, text="Listo")
        except Exception as e:  # mostrar algo útil sin volcar el traceback
            barra.empty()
            st.error(f"No se pudieron generar los archivos: {e}")

    paq = st.session_state.get("paquete")
    if paq:
        iso = paq["fecha"].strftime("%Y-%m-%d")
        archivos = paq["archivos"]
        peso = sum(len(b) for b in archivos.values()) / 1e6
        st.success(f"{len(archivos)} archivos generados ({peso:.1f} MB). Stock al {paq['fecha']:%d/%m/%Y}.")
        st.download_button("Descargar todo (ZIP)", paq["zip"], f"catalogo_{iso}.zip", "application/zip", type="primary")
        cols = st.columns(2)
        if "catalogo.html" in archivos:
            cols[0].download_button("Solo catalogo.html", archivos["catalogo.html"], "catalogo.html", "text/html")
        completo = next((n for n in archivos if "catalogo_completo" in n), None)
        if completo:
            cols[1].download_button("Solo PDF completo", archivos[completo], completo.split("/")[-1], "application/pdf")
        por_modelo = sorted(n for n in archivos if n.startswith("pdf/") and "completo" not in n)
        if por_modelo:
            with st.expander(f"PDF por modelo ({len(por_modelo)})"):
                for n in por_modelo:
                    st.download_button(n.split("/")[-1], archivos[n], n.split("/")[-1], "application/pdf", key=f"dl_{n}")

    cfg = config_ftp()
    if cfg is not None:
        with st.expander("Opcional: publicar por FTP"):
            _bloque_ftp(res, cfg)


def _bloque_ftp(res, cfg: ConfigFTP) -> None:
    """Publicación por FTP (fase de automatización). Solo aparece si hay configuración TAIYO_FTP_*."""
    destino = st.radio("Destino", ["Pruebas", "Producción"], horizontal=True, key="ftp_destino")
    sub = "_prueba" if destino == "Pruebas" else ""
    confirmo = st.checkbox("Revisé los datos y quiero publicar por FTP", key="ftp_confirmo")
    if st.button("Publicar por FTP", disabled=not confirmo):
        barra = st.progress(0.0, text="Conectando al FTP…")
        try:
            manifest = leer_manifest_remoto(cfg, sub)
            with tempfile.TemporaryDirectory() as tmp:
                sitio = generar_sitio(res, st.session_state["fuente"], Path(tmp), manifest)
                r = publicar(cfg, sitio.carpeta, sitio.archivos_nuevos, sub, lambda i, n, t: barra.progress(i / n, text=f"Subiendo {t}"))
            barra.progress(1.0, text="Listo")
            st.session_state["publicado"] = (r, sitio.imagenes_procesadas, sitio.imagenes_sin_cambio)
        except ErrorPublicacion as e:
            barra.empty()
            st.error(str(e))
    pub = st.session_state.get("publicado")
    if pub:
        r, nuevas, iguales = pub
        st.success(f"Publicado en {r.destino}: {r.archivos} archivos ({r.bytes / 1024:.0f} KB). Fotos nuevas: {nuevas}; sin cambio: {iguales}.")
        if r.url:
            st.markdown(f"Catálogo: [{r.url}]({r.url})")


# ---------------------------------------------------------------- principal
def main() -> None:
    puerta()
    st.title(NOMBRE_CATALOGO)
    st.caption("Reporte de stock + maestro + fotos → catálogo web. Revisa los datos antes de descargar.")
    panel_configuracion()
    with st.sidebar:
        if secreto("APP_PASSWORD") and st.button("Cerrar sesión"):
            st.session_state.clear()
            st.rerun()
        umbral = st.number_input("«Pocas unidades» hasta", min_value=0, max_value=50, value=UMBRAL_POCAS_UNIDADES, step=1)
    t1, t2, t3, t4 = st.tabs(["1 · Datos", "2 · Revisión", "3 · Vista previa", "4 · Descargas"])
    with t1:
        pantalla_datos(int(umbral))
    with t2:
        pantalla_revision()
    with t3:
        pantalla_vista()
    with t4:
        pantalla_descargas()


main()
