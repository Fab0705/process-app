import streamlit as st
import pandas as pd
import plotly.express as px
from sqlalchemy import create_engine, text
import os
from pathlib import Path
from dotenv import load_dotenv

# Configuración de página
st.set_page_config(page_title="Análisis de Rendimiento de Procesos", layout="wide")

# Cargar el archivo .env ubicado junto a app.py
load_dotenv(Path(__file__).resolve().parent / ".env")

@st.cache_resource
def get_engine():
    db_url = os.getenv("SUPABASE_URL")

    if not db_url:
        raise ValueError("Falta SUPABASE_URL en el archivo .env")

    return create_engine(db_url)

engine = get_engine()

# Funciones cacheadas para consultas SQL ultrarrápidas
@st.cache_data(ttl=600)
def cargar_filtros():
    with engine.connect() as conn:
        meses = [r[0] for r in conn.execute(text("SELECT DISTINCT mes FROM ejecuciones_procesos ORDER BY mes")).fetchall()]
        grupos = [r[0] for r in conn.execute(text("SELECT DISTINCT grupo FROM ejecuciones_procesos ORDER BY grupo")).fetchall()]
    return meses, grupos

@st.cache_data(ttl=600)
def consultar_datos_agregados(meses_sel, grupos_sel):
    where_clauses = ["1=1"]
    params = {}
    
    if meses_sel:
        where_clauses.append("mes = ANY(:meses)")
        params["meses"] = meses_sel
    if grupos_sel:
        where_clauses.append("grupo = ANY(:grupos)")
        params["grupos"] = grupos_sel
        
    where_sql = " AND ".join(where_clauses)
    
    # 1. Resumen Diario
    q_diario = text(f"""
        SELECT fecha, mes,
               SUM(tiempo_demora_seg)/3600.0 AS horas_totales,
               COUNT(*) AS total_ejecuciones,
               AVG(tiempo_demora_seg)/60.0 AS promedio_min
        FROM ejecuciones_procesos
        WHERE {where_sql}
        GROUP BY fecha, mes
        ORDER BY fecha
    """)
    
    # 2. Top Jobs (Acumulado, Promedio, Máximo y Desviación)
    q_jobs = text(f"""
        SELECT jobname, grupo,
               SUM(tiempo_demora_seg)/3600.0 AS horas_acumuladas,
               COUNT(*) AS ejecuciones,
               SUM(veces_repeticion) AS repeticiones_totales,
               AVG(tiempo_demora_seg)/60.0 AS promedio_min,
               MAX(tiempo_demora_seg)/60.0 AS max_min,
               STDDEV(tiempo_demora_seg)/60.0 AS desviacion_min
        FROM ejecuciones_procesos
        WHERE {where_sql}
        GROUP BY jobname, grupo
        ORDER BY horas_acumuladas DESC
        LIMIT 25
    """)
    
    # 3. Carga por Franja Horaria
    q_horario = text(f"""
        SELECT rango_horario,
               SUM(tiempo_demora_seg)/3600.0 AS horas_totales,
               COUNT(*) AS cantidad_procesos
        FROM ejecuciones_procesos
        WHERE {where_sql}
        GROUP BY rango_horario
        ORDER BY rango_horario
    """)

    with engine.connect() as conn:
        df_diario = pd.read_sql(q_diario, conn, params=params)
        df_jobs = pd.read_sql(q_jobs, conn, params=params)
        df_horario = pd.read_sql(q_horario, conn, params=params)
        
    return df_diario, df_jobs, df_horario

# --- INTERFAZ DE USUARIO ---
st.title("📊 Monitor de Rendimiento y Cuellos de Botella de Procesos")
st.markdown("Análisis histórico diario/mensual y proyección de impacto por mejora de infraestructura/conectividad.")

try:
    meses_disp, grupos_disp = cargar_filtros()
except Exception as e:
    st.error(f"Error conectando a Supabase. Verifica tus secrets: {e}")
    st.stop()

# Sidebar de filtros y simulación
st.sidebar.header("🔍 Filtros de Consulta")
meses_sel = st.sidebar.multiselect("Seleccionar Mes(es):", meses_disp, default=meses_disp)
grupos_sel = st.sidebar.multiselect("Filtrar por Grupo:", grupos_disp, default=[])

st.sidebar.markdown("---")
st.sidebar.header("🚀 Simulador de Mejora (Ej. 4G → 5G)")
mejora_pct = st.sidebar.slider(
    "Reducción estimada en tiempos de transmisión/espera (%):",
    min_value=5, max_value=70, value=35, step=5,
    help="Simula cuánto tiempo operativo se ahorraría al incrementar el ancho de banda o capacidad de procesamiento."
)

df_diario, df_jobs, df_horario = consultar_datos_agregados(meses_sel, grupos_sel)

if df_diario.empty:
    st.warning("No hay datos para los filtros seleccionados.")
    st.stop()

# --- KPIs PRINCIPALES ---
total_horas = df_diario["horas_totales"].sum()
total_proc = df_diario["total_ejecuciones"].sum()
dia_mas_pesado = df_diario.loc[df_diario["horas_totales"].idxmax()]
horas_ahorradas = total_horas * (mejora_pct / 100.0)

col1, col2, col3, col4 = st.columns(4)
col1.metric("Tiempo Total Acumulado", f"{total_horas:,.1f} hrs")
col2.metric("Total Ejecuciones", f"{total_proc:,}")
col3.metric("Día Más Pesado", f"{dia_mas_pesado['fecha']}", f"{dia_mas_pesado['horas_totales']:.1f} hrs")
col4.metric(f"Ahorro Proyectado (-{mejora_pct}%)", f"{horas_ahorradas:,.1f} hrs", delta=f"-{mejora_pct}% tiempo")

# --- PESTAÑAS DE ANÁLISIS ---
tab1, tab2, tab3, tab4 = st.tabs([
    "📅 Evolución Diaria y Mensual",
    "🔥 Top Procesos Críticos",
    "⏰ Concurrencia Horaria",
    "📈 Impacto y Variabilidad"
])

with tab1:
    st.subheader("Carga Operativa por Día (Horas Acumuladas de Demora)")
    fig_dia = px.bar(
        df_diario, x="fecha", y="horas_totales", color="mes",
        labels={"fecha": "Fecha", "horas_totales": "Horas Acumuladas", "mes": "Mes"},
        hover_data=["total_ejecuciones", "promedio_min"]
    )
    st.plotly_chart(fig_dia, use_container_width=True)
    
    st.subheader("Comparativa Mensual")
    df_mensual = df_diario.groupby("mes", as_index=False).agg(
        Horas_Totales=("horas_totales", "sum"),
        Ejecuciones=("total_ejecuciones", "sum"),
        Promedio_Diario_Hrs=("horas_totales", "mean")
    )
    st.dataframe(df_mensual, use_container_width=True)

with tab2:
    st.subheader("Top 25 Jobs con Mayor Tiempo Acumulado")
    fig_jobs = px.bar(
        df_jobs.head(15), x="horas_acumuladas", y="jobname", orientation="h", color="grupo",
        labels={"horas_acumuladas": "Horas Acumuladas", "jobname": "Proceso (JOBNAME)"},
        hover_data=["ejecuciones", "promedio_min", "max_min"]
    )
    fig_jobs.update_layout(yaxis={'categoryorder':'total ascending'})
    st.plotly_chart(fig_jobs, use_container_width=True)
    
    st.dataframe(df_jobs.style.format({
        "horas_acumuladas": "{:.2f}",
        "promedio_min": "{:.2f}",
        "max_min": "{:.2f}",
        "desviacion_min": "{:.2f}"
    }), use_container_width=True)

with tab3:
    st.subheader("Distribución de Carga por Rango Horario (Detección de Cuellos de Botella)")
    fig_hora = px.area(
        df_horario, x="rango_horario", y="horas_totales",
        labels={"rango_horario": "Franja Horaria", "horas_totales": "Horas Acumuladas"},
        markers=True
    )
    st.plotly_chart(fig_hora, use_container_width=True)

with tab4:
    st.subheader("Simulación: Escenario Actual vs. Escenario Optimizado")
    df_sim = df_diario[["fecha", "horas_totales"]].copy()
    df_sim["Escenario Actual (Hrs)"] = df_sim["horas_totales"]
    df_sim[f"Con Mejora -{mejora_pct}% (Hrs)"] = df_sim["horas_totales"] * (1 - mejora_pct / 100.0)
    
    fig_sim = px.line(
        df_sim, x="fecha", y=["Escenario Actual (Hrs)", f"Con Mejora -{mejora_pct}% (Hrs)"],
        labels={"value": "Horas de Demora", "variable": "Escenario", "fecha": "Fecha"}
    )
    st.plotly_chart(fig_sim, use_container_width=True)
    
    st.info(
        "**Nota de análisis:** Los procesos con alta **desviación estándar (`desviacion_min`)** en la pestaña 'Top Procesos Críticos' "
        "son los más sensibles a congestión de red o ancho de banda, por lo que serán los principales beneficiados tras la mejora."
    )