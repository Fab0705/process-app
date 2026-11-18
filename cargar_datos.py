import os
import pandas as pd
from pathlib import Path
from sqlalchemy import create_engine
from dotenv import load_dotenv

# Cargar el .env ubicado junto a este script.
load_dotenv(Path(__file__).resolve().parent / ".env")

DB_URL = os.getenv("SUPABASE_URL")

if not DB_URL:
    raise ValueError("Falta SUPABASE_URL en el archivo .env")

engine = create_engine(DB_URL)

def parse_time_to_seconds(t_val):
    if pd.isna(t_val):
        return 0.0
    if hasattr(t_val, 'hour'):
        return float(t_val.hour * 3600 + t_val.minute * 60 + t_val.second)
    parts = str(t_val).strip().split(':')
    if len(parts) == 3:
        try:
            return float(int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2]))
        except ValueError:
            return 0.0
    return 0.0

def subir_excel_a_supabase(ruta_excel, nombre_mes):
    print(f"Leyendo archivo: {ruta_excel}...")
    xls = pd.ExcelFile(ruta_excel)
    all_sheets = []

    for sheet in xls.sheet_names:
        df = pd.read_excel(xls, sheet_name=sheet)
        # Limpiar nombres de columnas por seguridad
        df.columns = [c.strip() for c in df.columns]
        
        # Parsear fecha desde el nombre de la hoja (ej. '01-01-26' -> DD-MM-YY)
        df['fecha'] = pd.to_datetime(sheet, format='%d-%m-%y', errors='coerce').date()
        df['mes'] = nombre_mes
        df['tiempo_demora_seg'] = df['Tiempo de Demora'].apply(parse_time_to_seconds)
        
        df_clean = pd.DataFrame({
            'fecha': df['fecha'],
            'mes': df['mes'],
            'rango_horario': df['Rango Horario'].astype(str),
            'jobname': df['JOBNAME'].astype(str).str.strip(),
            'hora_inicio': df['Hora Inicio'].astype(str),
            'hora_fin': df['Hora Finalización'].astype(str),
            'tiempo_demora_seg': df['tiempo_demora_seg'],
            'grupo': df['Grupo'].astype(str).str.strip(),
            'veces_repeticion': pd.to_numeric(df['Veces de Repetición'], errors='coerce').fillna(1).astype(int)
        })
        all_sheets.append(df_clean)

    full_df = pd.concat(all_sheets, ignore_index=True)
    # Eliminar filas sin fecha válida si hubiera hojas resumen
    full_df = full_df.dropna(subset=['fecha'])
    
    print(f"Subiendo {len(full_df)} registros de {nombre_mes} a Supabase...")
    # Subir en lotes de 10,000 para evitar timeouts
    full_df.to_sql(
        name="ejecuciones_procesos",
        con=engine,
        schema="public",
        if_exists="append",
        index=False,
        chunksize=1000,
        method="multi"
    )
    print(f"¡Carga de {nombre_mes} completada con éxito!")

if __name__ == "__main__":
    # Carpeta "data" junto a cargar_datos.py.
    # Funciona aunque ejecutes el script desde otra ubicación.
    carpeta_datos = Path(__file__).resolve().parent / "data"

    ANIO = 2026  # Actualiza si tus archivos corresponden a otro año.

    if not carpeta_datos.is_dir():
        raise FileNotFoundError(
            f"No se encontró la carpeta de datos: {carpeta_datos}"
        )

    archivos = sorted(
        carpeta_datos.glob("procesos_*_por_dia.xlsx")
    )

    if not archivos:
        raise FileNotFoundError(
            f"No se encontraron archivos procesos_*_por_dia.xlsx "
            f"en: {carpeta_datos}"
        )

    print(f"Carpeta detectada: {carpeta_datos}")
    print(f"Archivos encontrados: {len(archivos)}")

    for archivo in archivos:
        # Ejemplo: procesos_Enero_por_dia.xlsx → Enero
        mes = archivo.stem[len("procesos_"):-len("_por_dia")]
        nombre_mes = f"{mes} {ANIO}"

        subir_excel_a_supabase(
            ruta_excel=archivo,
            nombre_mes=nombre_mes
        )