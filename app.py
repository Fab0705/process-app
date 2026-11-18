import streamlit as st
import pandas as pd
import plotly.express as px
from sqlalchemy import create_engine, text
import os
from pathlib import Path
from dotenv import load_dotenv
import google.generativeai as genai

# Configuración de página
st.set_page_config(page_title="Análisis de Rendimiento de Procesos", layout="wide")

# Cargar el archivo .env
load_dotenv(Path(__file__).resolve().parent / ".env")

@st.cache_resource
def get_engine():
    db_url = os.getenv("SUPABASE_URL")
    if not db_url:
        raise ValueError("Falta SUPABASE_URL en el archivo .env")
    return create_engine(db_url)

engine = get_engine()

# Configurar Gemini
gemini_api_key = os.getenv("GEMINI_API_KEY")
if gemini_api_key:
    genai.configure(api_key=gemini_api_key)

# Funciones cacheadas
@st.cache_data(ttl=600)
def cargar_filtros():
    with engine.connect() as conn:
        meses = [r[0] for r in conn.execute(text("SELECT DISTINCT mes FROM ejecuciones_procesos ORDER BY mes")).fetchall()]
        grupos = [r[0] for r in conn.execute(text("SELECT DISTINCT grupo FROM ejecuciones_procesos ORDER BY grupo")).fetchall()]
        fechas = [r[0] for r in conn.execute(text("SELECT DISTINCT fecha FROM ejecuciones_procesos ORDER BY fecha")).fetchall()]
    return meses, grupos, fechas

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
    
    q_horario = text(f"""
        SELECT fecha, mes, rango_horario,
               SUM(tiempo_demora_seg)/3600.0 AS horas_totales,
               COUNT(*) AS cantidad_procesos
        FROM ejecuciones_procesos
        WHERE {where_sql}
        GROUP BY fecha, mes, rango_horario
        ORDER BY rango_horario
    """)

    with engine.connect() as conn:
        df_diario = pd.read_sql(q_diario, conn, params=params)
        df_jobs = pd.read_sql(q_jobs, conn, params=params)
        df_horario = pd.read_sql(q_horario, conn, params=params)
        
    return df_diario, df_jobs, df_horario

@st.cache_data(ttl=600)
def consultar_procesos_por_dia(fecha_sel):
    q = text("""
        SELECT jobname, grupo, rango_horario,
               SUM(tiempo_demora_seg)/3600.0 AS horas_totales,
               COUNT(*) AS ejecuciones,
               AVG(tiempo_demora_seg)/60.0 AS promedio_min
        FROM ejecuciones_procesos
        WHERE fecha = :fecha
        GROUP BY jobname, grupo, rango_horario
        ORDER BY horas_totales DESC
    """)
    with engine.connect() as conn:
        return pd.read_sql(q, conn, params={"fecha": fecha_sel})

# --- INTERFAZ ---
st.title("📊 Monitor de Rendimiento y Cuellos de Botella")

try:
    meses_disp, grupos_disp, fechas_disp = cargar_filtros()
except Exception as e:
    st.error(f"Error conectando a Supabase: {e}")
    st.stop()

st.sidebar.header("🔍 Filtros Generales")
meses_sel = st.sidebar.multiselect("Seleccionar Mes(es):", meses_disp, default=meses_disp)
grupos_sel = st.sidebar.multiselect("Filtrar por Grupo:", grupos_disp, default=[])

st.sidebar.markdown("---")
st.sidebar.header("📅 Inspección Diaria")
fecha_seleccionada = st.sidebar.selectbox(
    "Seleccionar Día para ver procesos al final:", 
    ["Ninguno"] + list(fechas_disp)
)

df_diario, df_jobs, df_horario = consultar_datos_agregados(meses_sel, grupos_sel)

if df_diario.empty:
    st.warning("No hay datos.")
    st.stop()

# Pre-cálculo para uso en vistas y en el contexto de la IA
df_mensual = df_diario.groupby("mes", as_index=False).agg(
    Horas_Totales=("horas_totales", "sum"),
    Ejecuciones=("total_ejecuciones", "sum"),
    Promedio_Diario_Hrs=("horas_totales", "mean")
)

total_horas = df_diario["horas_totales"].sum()
total_proc = df_diario["total_ejecuciones"].sum()
dia_mas_pesado = df_diario.loc[df_diario["horas_totales"].idxmax()]

col1, col2, col3 = st.columns(3)
col1.metric("Tiempo Total Acumulado", f"{total_horas:,.1f} hrs")
col2.metric("Total Ejecuciones", f"{total_proc:,}")
col3.metric("Día Más Pesado", f"{dia_mas_pesado['fecha']}", f"{dia_mas_pesado['horas_totales']:.1f} hrs")

# --- PESTAÑAS ---
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📅 Evolución",
    "🔥 Top Críticos",
    "⏰ Concurrencia Horaria",
    "📈 Variabilidad",
    "⚖️ Comparativa Mensual" # <--- NUEVA PESTAÑA
])

with tab1:
    fig_dia = px.bar(
        df_diario, x="fecha", y="horas_totales", color="mes",
        hover_data=["total_ejecuciones", "promedio_min"]
    )
    st.plotly_chart(fig_dia, use_container_width=True)
    st.dataframe(df_mensual, use_container_width=True)

with tab2:
    fig_jobs = px.bar(
        df_jobs.head(15), x="horas_acumuladas", y="jobname", orientation="h", color="grupo"
    )
    fig_jobs.update_layout(yaxis={'categoryorder':'total ascending'})
    st.plotly_chart(fig_jobs, use_container_width=True)
    st.dataframe(df_jobs, use_container_width=True)

with tab3:
    tipo_vista = st.radio("Nivel de detalle:", ["General", "Mensual", "Día Específico"], horizontal=True)
    df_horario_plot = df_horario.copy()
    if tipo_vista == "Mensual":
        mes_vista = st.selectbox("Mes:", df_horario['mes'].unique())
        df_horario_plot = df_horario_plot[df_horario_plot['mes'] == mes_vista]
    elif tipo_vista == "Día Específico":
        dia_vista = st.selectbox("Día:", sorted(df_horario['fecha'].unique()))
        df_horario_plot = df_horario_plot[df_horario_plot['fecha'] == dia_vista]
        
    df_horario_plot = df_horario_plot.groupby("rango_horario", as_index=False).agg({"horas_totales": "sum"})
    fig_hora = px.area(df_horario_plot, x="rango_horario", y="horas_totales", markers=True)
    st.plotly_chart(fig_hora, use_container_width=True)

with tab4:
    fig_sim = px.line(df_diario, x="fecha", y="horas_totales", title="Escenario Actual (Hrs)")
    st.plotly_chart(fig_sim, use_container_width=True)

# --- NUEVA PESTAÑA: COMPARATIVA MENSUAL ---
with tab5:
    st.subheader("Comparativa Frente a Frente")
    if len(meses_disp) >= 2:
        c1, c2 = st.columns(2)
        mes1 = c1.selectbox("Seleccione el Primer Mes:", meses_disp, index=0)
        mes2 = c2.selectbox("Seleccione el Segundo Mes:", meses_disp, index=min(1, len(meses_disp)-1))
        
        df_comp = df_mensual[df_mensual['mes'].isin([mes1, mes2])]
        
        # Gráfico comparativo
        fig_comp = px.bar(
            df_comp, x="mes", y=["Horas_Totales", "Ejecuciones"], 
            barmode="group", title=f"Comparativa: {mes1} vs {mes2}"
        )
        st.plotly_chart(fig_comp, use_container_width=True)
        
        # Tabla resumen comparativa
        st.dataframe(df_comp.set_index("mes"), use_container_width=True)
    else:
        st.info("Necesitas al menos 2 meses de datos en la base para hacer una comparativa.")

# --- DETALLE POR DÍA (INFERIOR) ---
if fecha_seleccionada != "Ninguno":
    st.markdown("---")
    st.subheader(f"📋 Procesos Ejecutados el {fecha_seleccionada}")
    df_procesos_dia = consultar_procesos_por_dia(fecha_seleccionada)
    if not df_procesos_dia.empty:
        st.dataframe(df_procesos_dia.style.format({"horas_totales": "{:.2f}", "promedio_min": "{:.2f}"}), use_container_width=True)
    else:
        st.info("No se encontraron procesos para esta fecha.")
