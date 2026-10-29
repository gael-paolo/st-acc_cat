"""Parámetros del proceso.

Todo lo que conviene ajustar sin tocar la lógica vive aquí.
"""

NOMBRE_CATALOGO = "Catálogo de accesorios"

# El reporte de stock antepone la marca al código del artículo: "0/ACCES-00198" o "1/ACCES-00198".
# Se quita ese prefijo y se suman ambas marcas.
PREFIJO_MARCA = r"^[01]/"

# Hasta esta cantidad (incluida) el catálogo muestra "Pocas unidades".
UMBRAL_POCAS_UNIDADES = 3

# Códigos del reporte que se consideran posibles accesorios cuando no están en el maestro
# (alimenta la lista "nuevos sin clasificar").
PREFIJOS_NUEVOS = ("ACCES-",)

# Avisos de calidad
DIAS_REPORTE_VIEJO = 3        # aviso si el reporte tiene más días que estos
CAIDA_FILAS_ALERTA = 0.5      # aviso si el reporte trae menos de esta fracción de las filas anteriores

# Imágenes
TAM_MINIATURA = 480           # lado mayor, en píxeles (nunca se amplía una foto pequeña)
TAM_GRANDE = 1200
CALIDAD_MINIATURA = 72
CALIDAD_GRANDE = 80
EXTENSIONES_IMAGEN = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")

# Precio de marcador de posición que se usó al crear el maestro
PRECIO_MARCADOR = 100
