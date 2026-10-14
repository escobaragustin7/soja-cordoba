"""
Análisis de soja en la provincia de Córdoba por departamento.

Fuente: Estadística de Cultivos de la Bolsa de Cereales de Córdoba (BCCBA).
Descarga: https://www.bccba.org.ar/estadisticas-de-cultivos/ -> "Descarga de datos".

El Excel trae tres hojas (Superficie Sembrada, Rendimiento y Producción) con
datos por provincia, departamento, cultivo y campaña. El script las une,
filtra Córdoba y el cultivo elegido, y calcula para cada departamento:
  - superficie sembrada y producción promedio de las últimas campañas
  - rendimiento promedio reciente e histórico (qq/ha)
  - variabilidad del rendimiento entre campañas (qué tan estable es)
  - cambio de superficie sembrada en todo el período

Genera en la carpeta salida/:
  soja_cordoba_por_departamento.csv  -> para unir a la capa de QGIS
  soja_cordoba_evolucion.csv         -> totales provinciales por campaña
  rendimiento_por_departamento.png   -> ranking de rendimiento
  evolucion_provincial.png           -> superficie y rendimiento por campaña

Uso:
  python soja_cordoba.py BCCBA_ESTADISTICA_CULTIVOS.xlsx
  python soja_cordoba.py BCCBA_ESTADISTICA_CULTIVOS.xlsx --campanias 3
  python soja_cordoba.py BCCBA_ESTADISTICA_CULTIVOS.xlsx --cultivo MAIZ
"""

import argparse
import sys
import unicodedata
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # guarda los gráficos sin abrir ventanas
import matplotlib.pyplot as plt
import pandas as pd

CLAVES = ["provincia", "departamento", "cultivo", "campania"]
VERDE = "#3d7a32"
GRIS = "#6b6b6b"

# Algunos departamentos aparecen escritos de dos formas en el archivo
SINONIMOS = {
    "GRAL. SAN MARTIN": "GENERAL SAN MARTIN",
    "PTE. ROQUE SAENZ PENA": "PRESIDENTE ROQUE SAENZ PENA",
}


# ---------------------------------------------------------------------------
# 1. Lectura y limpieza
# ---------------------------------------------------------------------------

def normalizar(texto):
    """Mayúsculas y sin acentos: 'Río Cuarto' -> 'RIO CUARTO'.
    Así los nombres coinciden con la capa de departamentos de QGIS."""
    if pd.isna(texto):
        return texto
    texto = unicodedata.normalize("NFKD", str(texto))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = " ".join(texto.upper().split())
    return SINONIMOS.get(texto, texto)


def leer_hoja(archivo, hoja, columnas):
    """Lee una hoja del Excel. Los encabezados están en la fila 11
    (las primeras 10 filas son el logo y el título)."""
    df = pd.read_excel(archivo, sheet_name=hoja, header=10)
    df = df.iloc[:, :6]
    df.columns = CLAVES + columnas
    for col in CLAVES:
        df[col] = df[col].map(normalizar)
    return df.dropna(subset=["departamento", "campania"])


def cargar_datos(archivo, cultivo):
    """Une las tres hojas en una sola tabla y filtra Córdoba y el cultivo."""
    try:
        superficie = leer_hoja(archivo, "Superficie Sembrada", ["sembrada_ha", "cosechada_ha"])
        rendimiento = leer_hoja(archivo, "Rendimiento", ["rendimiento_qq_ha", "_rend_lys"])
        produccion = leer_hoja(archivo, "Producción", ["produccion_t", "_prod_lys"])
    except (ValueError, FileNotFoundError) as error:
        sys.exit(f"No pude leer el Excel de la Bolsa de Cereales: {error}")

    datos = (superficie
             .merge(rendimiento[CLAVES + ["rendimiento_qq_ha"]], on=CLAVES, how="outer")
             .merge(produccion[CLAVES + ["produccion_t"]], on=CLAVES, how="outer"))

    datos = datos[(datos["provincia"] == "CORDOBA") & (datos["cultivo"] == normalizar(cultivo))].copy()
    if datos.empty:
        sys.exit(f"No hay datos de {cultivo} en Córdoba en el archivo.")

    datos["anio_inicio"] = datos["campania"].str[:4].astype(int)
    print(f"{cultivo.title()} en Córdoba: {len(datos)} filas, "
          f"{datos['departamento'].nunique()} departamentos, "
          f"campañas {datos['campania'].min()} a {datos['campania'].max()}")
    return datos


# ---------------------------------------------------------------------------
# 2. Análisis
# ---------------------------------------------------------------------------

def resumen_por_departamento(datos, n_campanias):
    """Una fila por departamento con promedios recientes e históricos."""
    campanias = sorted(datos["campania"].unique())
    recientes = campanias[-n_campanias:]
    periodo = f"{recientes[0]} a {recientes[-1]}"

    reciente = (datos[datos["campania"].isin(recientes)]
                .groupby("departamento")
                .agg(sembrada_ha=("sembrada_ha", "mean"),
                     produccion_t=("produccion_t", "mean"),
                     cosechada_ha=("cosechada_ha", "mean")))
    # Rendimiento ponderado: producción total / superficie cosechada total
    reciente["rendimiento_qq_ha"] = reciente["produccion_t"] * 10 / reciente["cosechada_ha"]

    historico = datos.groupby("departamento").agg(
        rendimiento_historico_qq_ha=("rendimiento_qq_ha", "mean"),
        # Coeficiente de variación: desvío / promedio. Más alto = rinde más inestable
        variabilidad_pct=("rendimiento_qq_ha", lambda x: x.std() / x.mean() * 100),
        campanias_con_datos=("campania", "nunique"),
    )

    # Cambio de superficie: primera vs última campaña
    sup = datos.pivot_table(index="departamento", columns="campania", values="sembrada_ha")
    cambio = ((sup[campanias[-1]] - sup[campanias[0]]) / sup[campanias[0]] * 100)

    resumen = reciente.join(historico)
    resumen["cambio_sembrada_pct"] = cambio
    resumen = resumen.drop(columns="cosechada_ha").reset_index()
    resumen = resumen.round({"sembrada_ha": 0, "produccion_t": 0, "rendimiento_qq_ha": 1,
                             "rendimiento_historico_qq_ha": 1, "variabilidad_pct": 1,
                             "cambio_sembrada_pct": 1})
    return resumen.sort_values("rendimiento_qq_ha", ascending=False), periodo


def evolucion_provincial(datos):
    """Totales de Córdoba por campaña."""
    evo = (datos.groupby("campania", as_index=False)
           [["sembrada_ha", "cosechada_ha", "produccion_t"]].sum())
    evo["rendimiento_qq_ha"] = (evo["produccion_t"] * 10 / evo["cosechada_ha"]).round(1)
    return evo


# ---------------------------------------------------------------------------
# 3. Gráficos
# ---------------------------------------------------------------------------

def estilo(ax):
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color("#bbbbbb")
    ax.tick_params(colors="#444444", labelsize=9)


def grafico_rendimiento(resumen, periodo, cultivo, carpeta, min_ha):
    """Ranking de rendimiento. Deja afuera departamentos con muy poca
    superficie, cuyo promedio no es representativo."""
    datos = (resumen[resumen["sembrada_ha"] >= min_ha]
             .dropna(subset=["rendimiento_qq_ha"])
             .sort_values("rendimiento_qq_ha"))
    promedio = datos["rendimiento_qq_ha"].mean()

    fig, ax = plt.subplots(figsize=(8, 0.34 * len(datos) + 1.6))
    ax.barh(datos["departamento"].str.title(), datos["rendimiento_qq_ha"], color=VERDE, height=0.7, zorder=2)
    for y, valor in enumerate(datos["rendimiento_qq_ha"]):
        ax.text(valor - 0.4, y, f"{valor:.1f}", va="center", ha="right", fontsize=8, color="white", zorder=3)
    ax.axvline(promedio, color=GRIS, linestyle="--", linewidth=1, zorder=1)
    ax.text(promedio, len(datos) - 0.3, f"promedio {promedio:.1f} ",
            fontsize=8, color=GRIS, va="bottom", ha="right")
    ax.set_xlabel("Rendimiento promedio (qq/ha)", fontsize=9)
    ax.set_title(f"{cultivo.title()} en Córdoba: rendimiento por departamento\n"
                 f"Campañas {periodo} · departamentos con más de {min_ha:,} ha".replace(",", "."),
                 loc="left", fontsize=11)
    estilo(ax)
    fig.text(0.01, 0.005, "Fuente: Bolsa de Cereales de Córdoba", fontsize=7, color=GRIS)
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    fig.savefig(carpeta / "rendimiento_por_departamento.png", dpi=150)
    plt.close(fig)


def grafico_evolucion(evo, cultivo, carpeta):
    """Dos paneles con el mismo eje de campañas (no un gráfico de doble eje)."""
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 6), sharex=True)
    ax1.plot(evo["campania"], evo["sembrada_ha"] / 1e6, color=VERDE, linewidth=2, marker="o", markersize=4)
    ax1.set_ylabel("Millones de ha", fontsize=9)
    ax1.set_title(f"{cultivo.title()} en Córdoba: superficie sembrada", loc="left", fontsize=10)
    ax1.set_ylim(bottom=0)

    ax2.plot(evo["campania"], evo["rendimiento_qq_ha"], color=VERDE, linewidth=2, marker="o", markersize=4)
    ax2.set_ylabel("qq/ha", fontsize=9)
    ax2.set_title("Rendimiento promedio provincial", loc="left", fontsize=10)
    ax2.set_ylim(bottom=0)
    peor = evo.loc[evo["rendimiento_qq_ha"].idxmin()]
    ax2.annotate(f"{peor['campania']}: {peor['rendimiento_qq_ha']:.1f} qq/ha",
                 (peor["campania"], peor["rendimiento_qq_ha"]),
                 xytext=(0, -16), textcoords="offset points", ha="center", fontsize=8, color=GRIS)

    for ax in (ax1, ax2):
        estilo(ax)
        ax.grid(axis="y", color="#eeeeee")
    ax2.tick_params(axis="x", rotation=60)
    fig.text(0.01, 0.005, "Fuente: Bolsa de Cereales de Córdoba", fontsize=7, color=GRIS)
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    fig.savefig(carpeta / "evolucion_provincial.png", dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------------------
# 4. Programa principal
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Análisis de cultivos en Córdoba por departamento")
    parser.add_argument("excel", help="Excel de Estadística de Cultivos de la BCCBA")
    parser.add_argument("--cultivo", default="SOJA", help="SOJA, MAIZ, TRIGO, MANI... (por defecto SOJA)")
    parser.add_argument("--campanias", type=int, default=5, help="campañas recientes a promediar (por defecto 5)")
    parser.add_argument("--min-ha", type=int, default=10000,
                        help="superficie mínima para entrar al gráfico de ranking (por defecto 10000)")
    parser.add_argument("--salida", default="salida", help="carpeta de resultados")
    args = parser.parse_args()

    carpeta = Path(args.salida)
    carpeta.mkdir(exist_ok=True)

    datos = cargar_datos(args.excel, args.cultivo)
    resumen, periodo = resumen_por_departamento(datos, args.campanias)
    evo = evolucion_provincial(datos)

    prefijo = normalizar(args.cultivo).lower().replace(" ", "_")
    resumen.to_csv(carpeta / f"{prefijo}_cordoba_por_departamento.csv", index=False, encoding="utf-8")
    evo.to_csv(carpeta / f"{prefijo}_cordoba_evolucion.csv", index=False, encoding="utf-8")
    grafico_rendimiento(resumen, periodo, args.cultivo, carpeta, args.min_ha)
    grafico_evolucion(evo, args.cultivo, carpeta)

    principales = resumen[resumen["sembrada_ha"] >= args.min_ha]
    print(f"\nTop 5 departamentos por rendimiento ({periodo}):")
    print(principales.head(5)[["departamento", "rendimiento_qq_ha", "sembrada_ha", "variabilidad_pct"]]
          .to_string(index=False))
    print(f"\nResultados guardados en la carpeta '{carpeta}'.")


if __name__ == "__main__":
    main()
