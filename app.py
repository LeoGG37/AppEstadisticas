import html
import importlib
import threading
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

# Tras un push, Streamlit Cloud recarga app.py pero puede conservar en memoria el data.py
# antiguo (daba ImportError con funciones nuevas). Releerlo no hace llamadas al banco.
import data
importlib.reload(data)
import enable_banking
if getattr(enable_banking, "__file__", None):  # Solo se relee si viene de un archivo
    importlib.reload(enable_banking)

from data import (  # noqa: E402
    parse_eb_transactions,
    monthly_summary,
    COLORS,
    CATEGORIA_OTROS,
    DEFAULT_FIJAS,
    nombre_mes,
    fecha_corta,
    ajustes_por_defecto,
    anadir_categoria,
    renombrar_categoria,
    borrar_categoria,
    aplicar_ajustes,
    sugerir_palabra_clave,
    normalizar,
    anadir_regla,
    borrar_regla,
    anadir_exclusion_palabra,
    reducir_movimiento,
    combinar_historial,
    exportar_copia,
    importar_copia,
)

# --- Configuración de página ---
st.set_page_config(
    page_title="Mis Gastos",
    page_icon="💶",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# --- Login ---
def check_password() -> bool:
    """Muestra pantalla de login. Devuelve True si el usuario está autenticado."""
    if st.session_state.get("authenticated"):
        return True

    # En local sin secrets.toml la app funciona sin contraseña
    try:
        password_correcta = st.secrets["app_password"]
    except (KeyError, FileNotFoundError):
        return True

    st.markdown("<br><br>", unsafe_allow_html=True)
    col = st.columns([1, 2, 1])[1]
    with col:
        st.markdown("### Mis Gastos")
        with st.form("login", clear_on_submit=False):
            pwd = st.text_input("Contraseña", type="password", placeholder="Introduce tu contraseña")
            submitted = st.form_submit_button("Entrar", width="stretch")
        if submitted:
            if pwd == password_correcta:
                st.session_state["authenticated"] = True
                st.rerun()
            else:
                st.error("Contraseña incorrecta.")
    return False

if not check_password():
    st.stop()

st.markdown("""
<style>
/* Ocultar menú hamburguesa y footer de Streamlit */
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}

/* Ajustar padding superior */
.block-container {padding-top: 2rem;}

/* Tabs */
[data-testid="stTabs"] button {font-size: 0.9rem;}

/* Tarjetas de métricas: en fila en el ordenador, cuadrícula 2×2 en el móvil */
.metricas {display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 8px 0 4px;}
.metricas.tres {grid-template-columns: repeat(3, 1fr);}
.metrica .etq {font-size: 0.8rem; color: #52514e;}
.metrica .val {font-size: 1.4rem; font-weight: 500; line-height: 1.3;}
@media (max-width: 640px) {
    .metricas, .metricas.tres {grid-template-columns: repeat(2, 1fr);}
    .metrica .val {font-size: 1.2rem;}
}

/* Filas de listas: nombre a la izquierda e importe a la derecha, siempre en la misma línea */
.fila {display: flex; justify-content: space-between; align-items: center; gap: 12px;
       padding: 8px 0; border-bottom: 1px solid #eeede9; font-size: 14px;}
.fila-izq {min-width: 0; overflow-wrap: anywhere;}
.fila-der {white-space: nowrap; font-weight: 500;}
.fila-sub {font-size: 12px; color: #898781;}
.etiqueta {font-size: 10px; padding: 1px 6px; border-radius: 4px; margin-left: 6px;}
</style>
""", unsafe_allow_html=True)

# Gráficos sin barra de herramientas ni zoom: en pantallas táctiles el zoom
# se activa al deslizar el dedo y bloquea el desplazamiento de la página
PLOTLY_CONFIG = {"displayModeBar": False, "scrollZoom": False}


def _metricas(items: list, clase: str = "") -> None:
    """Pinta tarjetas de métricas. items: [(etiqueta, valor, color o None)]."""
    tarjetas = "".join(
        f'<div class="metrica"><div class="etq">{etq}</div>'
        f'<div class="val" style="color:{color or "inherit"}">{val}</div></div>'
        for etq, val, color in items
    )
    st.markdown(f'<div class="metricas {clase}">{tarjetas}</div>', unsafe_allow_html=True)


def _fila(izquierda: str, derecha: str, color_derecha: str = "inherit") -> str:
    """HTML de una fila de lista. Los textos que vengan del banco deben ir escapados."""
    return (
        f'<div class="fila"><div class="fila-izq">{izquierda}</div>'
        f'<div class="fila-der" style="color:{color_derecha}">{derecha}</div></div>'
    )


def _lista_movimientos(movs: pd.DataFrame, limite: int = 100, pista: str = "") -> None:
    """Pinta una lista de movimientos en un único bloque HTML (más rápido en el móvil)."""
    filas = []
    for _, row in movs.head(limite).iterrows():
        es_ingreso = row["tipo"] == "ingreso"
        color_monto = COLORS["balance_pos"] if es_ingreso else COLORS["balance_neg"]
        signo = "+" if es_ingreso else "−"
        etiquetas = ""
        if row.get("excluido"):
            etiquetas += '<span class="etiqueta" style="background:#EEEDE9;color:#6B6A66;">no cuenta</span>'
        if row["recurrente"]:
            etiquetas += '<span class="etiqueta" style="background:#E6F1FB;color:#185FA5;">recurrente</span>'
        if es_ingreso:
            etiquetas += '<span class="etiqueta" style="background:#EAF3DE;color:#3B6D11;">ingreso</span>'

        # La descripción viene del banco: se escapa para que no pueda inyectar HTML
        izquierda = (
            f'{html.escape(row["descripcion"])}{etiquetas}'
            f'<div class="fila-sub">{fecha_corta(row["fecha"])}'
            f' · {html.escape(str(row["categoria"]))}</div>'
        )
        filas.append(_fila(izquierda, f'{signo}{abs(row["monto"]):,.2f} €', color_monto))

    if filas:
        st.markdown("".join(filas), unsafe_allow_html=True)
    if len(movs) > limite:
        st.caption(f"Mostrando {limite} de {len(movs)} movimientos. {pista}".strip())

# --- Credenciales Enable Banking ---
try:
    _eb_cfg     = st.secrets.get("enable_banking", {})
    _eb_app_id  = _eb_cfg.get("application_id")
    _eb_key     = _eb_cfg.get("private_key")
    _eb_session = _eb_cfg.get("session_id") or st.secrets.get("session_id")
    _eb_account = _eb_cfg.get("account_uid") or st.secrets.get("account_uid")

    # Leer UIDs múltiples (account_uid_1, account_uid_2, ...): se guardan tras el OAuth
    _eb_account_uids: list = []
    if _eb_account:
        _eb_account_uids.append(_eb_account)
    for _i in range(1, 10):
        _uid = _eb_cfg.get(f"account_uid_{_i}") or st.secrets.get(f"account_uid_{_i}")
        if _uid and _uid not in _eb_account_uids:
            _eb_account_uids.append(_uid)
except Exception:
    _eb_cfg = {}
    _eb_app_id = _eb_key = _eb_session = _eb_account = None
    _eb_account_uids = []


def _get_account_list() -> list:
    """Devuelve [{uid, label}] para las cuentas de la sesión activa."""
    if "account_list" in st.session_state:
        return st.session_state["account_list"]
    if not all([_eb_app_id, _eb_key, _eb_session]):
        return []
    try:
        import enable_banking as eb
        data = eb.get_session_status(_eb_app_id, _eb_key, _eb_session)
        result = []
        for i, acc in enumerate(data.get("accounts", [])):
            # La API puede devolver el UID como string o como objeto
            if isinstance(acc, str):
                uid = acc
                label = f"Cuenta {i + 1}"
            else:
                uid = acc.get("uid", "")
                account_id = acc.get("account_id") or {}
                iban = account_id.get("iban", "") if isinstance(account_id, dict) else ""
                tipo = acc.get("account_type") or acc.get("name") or ""
                if tipo:
                    label = tipo.capitalize()
                elif iban:
                    label = f"····{iban[-4:]}"
                else:
                    label = f"Cuenta {i + 1}"
            if not uid:
                continue
            result.append({"uid": uid, "label": label})
        st.session_state["account_list"] = result
        return result
    except Exception:
        return []


def _empty_df() -> pd.DataFrame:
    return pd.DataFrame(columns=["fecha", "monto", "descripcion", "categoria", "recurrente", "tipo", "mes"])


# El banco (PSD2) solo permite unas pocas consultas de movimientos al día por cuenta
CADUCIDAD_DATOS = timedelta(hours=24)   # Cada cuánto se piden datos nuevos
ESPERA_TRAS_FALLO = timedelta(hours=6)  # Tras un fallo, no reintentar antes de esto
ZONA_ES = ZoneInfo("Europe/Madrid")     # El servidor de Streamlit Cloud funciona en UTC
# Pasada ~1 h desde la autorización, muchos bancos solo dan los últimos 90 días; pedir
# más devuelve WRONG_TRANSACTIONS_PERIOD. 89 deja un día de margen por la zona horaria.
DIAS_HISTORIAL = 89

# El primer mes del historial puede traer solo sus últimos días: se marca para que no confunda.
# Se calcula tras cargar los datos, con la fecha real desde la que hay historial.
MES_PARCIAL = None


def _desde_cuenta(entrada: dict) -> str | None:
    """Fecha (AAAA-MM-DD) desde la que hay historial de una cuenta."""
    if entrada.get("desde"):
        return entrada["desde"]
    if entrada.get("obtenido"):  # Datos guardados antes de llevar la cuenta de "desde"
        return (entrada["obtenido"].date() - timedelta(days=DIAS_HISTORIAL)).isoformat()
    return None


def _rellenar_historial(entrada: dict, movimientos: list, desde: str | None, obtenido=None) -> None:
    """Añade movimientos antiguos (de una copia o de la descarga completa) a una cuenta.

    Lo que ya hay en memoria es lo más reciente y manda en su franja; de 'movimientos'
    solo se toma lo anterior a esa franja, así no se duplica nada.
    """
    if entrada.get("raw"):
        desde_mem = _desde_cuenta(entrada)
        entrada["raw"] = combinar_historial(movimientos, entrada["raw"], desde_mem)
        if desde:
            entrada["desde"] = min(desde_mem, desde)
    else:
        entrada.update(raw=movimientos, desde=desde, obtenido=obtenido)
    entrada["version"] = entrada.get("version", 0) + 1  # Hay que reprocesar


def _mes(periodo, corto: bool = False) -> str:
    """Nombre del mes en español, con '(parcial)' si es el primer mes incompleto."""
    return nombre_mes(periodo, corto) + (" (parcial)" if periodo == MES_PARCIAL else "")


def _motivo_error(e: Exception, uid: str) -> str:
    """Traduce el error de una llamada al banco a un texto corto, sin el identificador de la cuenta."""
    respuesta = getattr(e, "response", None)
    if respuesta is None:
        return f"sin respuesta del banco ({type(e).__name__})"
    estado = respuesta.status_code
    try:
        cuerpo = respuesta.json()
    except ValueError:
        cuerpo = {}
    codigo = str(cuerpo.get("error") or cuerpo.get("code") or "") if isinstance(cuerpo, dict) else ""
    detalle = str(cuerpo.get("message") or "") if isinstance(cuerpo, dict) else ""
    if codigo == "WRONG_TRANSACTIONS_PERIOD":
        motivo = "periodo de fechas no permitido por el banco"
    elif estado == 429:
        motivo = "límite de consultas del banco alcanzado"
    elif estado in (401, 403):
        motivo = "sesión no válida o caducada: renueva la conexión"
    else:
        motivo = " · ".join(p for p in (codigo, detalle) if p) or "error del banco"
    texto = f"{motivo} ({estado})"
    return texto.replace(uid, "…")[:150]  # Nunca mostrar el identificador de la cuenta


# ⚠️ NO MODIFICAR NI UNA LETRA DE ESTA FUNCIÓN (tampoco su docstring): Streamlit identifica
# esta memoria por el texto de la función y, si cambia, la vacía al hacer push (se pierden
# los movimientos en memoria). Los comentarios de encima, como este, sí se pueden cambiar.
@st.cache_resource
def _almacen() -> tuple:
    """Memoria compartida del servidor: {uid: {"raw", "obtenido", "desde", "ultimo_intento", "error"}}.

    Sobrevive a recargas de página y a varias pestañas abiertas, pero se pierde
    si la app se reinicia (push o inactividad). El lock evita llamadas duplicadas.
    """
    return {}, threading.Lock()


def load_data(account_uids: tuple, forzar: bool = False) -> tuple:
    """Devuelve (transacciones, cuentas sin actualizar).

    Cuenta por cuenta: si sus datos tienen más de 24 h se piden al banco; si la
    llamada falla se conservan los últimos datos buenos. Las cuentas sin
    actualizar se devuelven como [(uid, fecha_de_sus_datos_o_None, motivo_o_None)].
    """
    if not account_uids:
        return _empty_df(), []

    almacen, bloqueo = _almacen()
    sin_actualizar = []
    with bloqueo:
        llamadas = 0
        for uid in account_uids:
            entrada = almacen.setdefault(
                uid, {"raw": None, "obtenido": None, "desde": None, "ultimo_intento": None, "error": None}
            )
            ahora = datetime.now(timezone.utc)
            caducado = entrada["obtenido"] is None or ahora - entrada["obtenido"] > CADUCIDAD_DATOS
            en_espera = (
                entrada["ultimo_intento"] is not None
                and ahora - entrada["ultimo_intento"] < ESPERA_TRAS_FALLO
            )
            if caducado and _eb_app_id and _eb_key and (forzar or not en_espera):
                if llamadas > 0:
                    time.sleep(2.0)  # Pausa entre cuentas para no saturar la API
                llamadas += 1
                entrada["ultimo_intento"] = ahora
                try:
                    import enable_banking as eb
                    date_from = (date.today() - timedelta(days=DIAS_HISTORIAL)).isoformat()
                    nuevos = [
                        reducir_movimiento(t)
                        for t in eb.get_transactions(_eb_app_id, _eb_key, uid, date_from=date_from)
                    ]
                    # Historial acumulado: los últimos 89 días se sustituyen, lo anterior se conserva
                    desde_previo = _desde_cuenta(entrada)
                    entrada["raw"] = combinar_historial(entrada["raw"], nuevos, date_from)
                    entrada["version"] = entrada.get("version", 0) + 1  # Hay que reprocesar
                    entrada["desde"] = min(desde_previo, date_from) if desde_previo else date_from
                    entrada["obtenido"] = ahora
                    entrada["error"] = None
                except Exception as e:
                    # Se quedan los datos anteriores; guardamos el motivo para mostrarlo
                    entrada["error"] = _motivo_error(e, uid)
                    print(f"[load_data] Fallo al pedir movimientos: {entrada['error']}", flush=True)

            if entrada["obtenido"] is None or ahora - entrada["obtenido"] > CADUCIDAD_DATOS:
                sin_actualizar.append((uid, entrada["obtenido"], entrada["error"]))

        # Procesar miles de movimientos es lo costoso: solo se repite si han cambiado
        # (nueva consulta, copia subida o descarga completa sube la versión de la cuenta)
        clave = tuple(
            (uid, almacen[uid].get("version", 0), len(almacen[uid]["raw"] or [])) for uid in account_uids
        )
        procesados = _procesados()
        if clave not in procesados:
            raw_total = [t for uid in account_uids for t in (almacen[uid]["raw"] or [])]
            procesados.clear()  # Solo se guarda la última combinación de cuentas
            procesados[clave] = parse_eb_transactions(raw_total)
        df_procesado = procesados[clave]

    return df_procesado, sin_actualizar


# ⚠️ NO MODIFICAR NI UNA LETRA DE ESTA FUNCIÓN (tampoco su docstring): por la misma razón
# que _almacen. Si se vaciara no se pierde nada: solo se vuelve a procesar una vez.
@st.cache_resource
def _procesados() -> dict:
    """Movimientos ya procesados, por combinación de cuentas y versión de sus datos."""
    return {}

# ⚠️ NO MODIFICAR NI UNA LETRA DE ESTA FUNCIÓN (tampoco su docstring): Streamlit identifica
# esta memoria por el texto de la función y, si cambia, la vacía al hacer push (se pierden
# las categorías). Los comentarios de encima, como este, sí se pueden cambiar.
@st.cache_resource
def _ajustes() -> dict:
    """Categorías y clasificaciones del usuario, compartidas por todas las sesiones.

    Sobreviven a recargas y son las mismas en el móvil y en el PC, pero se pierden
    al reiniciar la app: para eso está la copia de seguridad de la pestaña Conexión.
    """
    return ajustes_por_defecto()


AJUSTES = _ajustes()
# Los ajustes que ya estaban en memoria antes de existir los gastos fijos no traen la lista
AJUSTES.setdefault("fijas", [c for c in DEFAULT_FIJAS if c in AJUSTES["categorias"]])
AJUSTES.setdefault("reglas", [])  # Ídem para las reglas por palabra clave
for _clave in ("ignorados", "excluir_descripciones", "excluir_palabras"):  # Ídem
    AJUSTES.setdefault(_clave, [])

# --- Título ---
st.title("Mis Gastos")

# --- Detectar callback OAuth (Santander redirige con ?code=...) ---
_oauth_code = st.query_params.get("code")

# --- Selector de cuentas (encima de las pestañas) ---
_accounts = _get_account_list()

if len(_accounts) > 1:
    # Sesión activa: etiquetas con nombre del tipo de cuenta
    _uid_map = {a["uid"]: a["label"] for a in _accounts}
    _selected = st.multiselect(
        "Cuentas",
        options=list(_uid_map.keys()),
        default=list(_uid_map.keys()),
        format_func=lambda uid: _uid_map.get(uid, uid[:8]),
        label_visibility="collapsed",
    )
    _selected_uids = tuple(_selected) if _selected else tuple(_uid_map.keys())
elif _accounts:
    _selected_uids = (_accounts[0]["uid"],)
elif len(_eb_account_uids) > 1:
    # Sin sesión pero hay múltiples UIDs en secrets: selector con etiquetas genéricas
    _uid_map = {uid: f"Cuenta {i + 1}" for i, uid in enumerate(_eb_account_uids)}
    _selected = st.multiselect(
        "Cuentas",
        options=list(_uid_map.keys()),
        default=list(_uid_map.keys()),
        format_func=lambda uid: _uid_map.get(uid, uid[:8]),
        label_visibility="collapsed",
    )
    _selected_uids = tuple(_selected) if _selected else tuple(_eb_account_uids)
elif _eb_account_uids:
    _selected_uids = tuple(_eb_account_uids)
else:
    _selected_uids = ()

df, _sin_actualizar = load_data(_selected_uids, forzar=st.session_state.pop("forzar_recarga", False))

# Mes parcial: el de la fecha más antigua desde la que hay historial, si no empieza el día 1
_alm_cuentas, _ = _almacen()
_desdes = [d for uid in _selected_uids if uid in _alm_cuentas and (d := _desde_cuenta(_alm_cuentas[uid]))]
if _desdes:
    _inicio = date.fromisoformat(min(_desdes)[:10])
    MES_PARCIAL = pd.Period(_inicio, freq="M") if _inicio.day > 1 else None

# Aviso cuando alguna cuenta muestra datos antiguos o aún no tiene datos
if _sin_actualizar:
    _etiquetas = (
        {a["uid"]: a["label"] for a in _accounts}
        or {uid: f"Cuenta {i + 1}" for i, uid in enumerate(_eb_account_uids)}
    )
    _partes = []
    for _uid, _obtenido, _error in _sin_actualizar:
        _nombre = _etiquetas.get(_uid, _uid[:8])
        _estado = (
            f"datos del {_obtenido.astimezone(ZONA_ES):%d/%m a las %H:%M}" if _obtenido
            else "sin datos todavía"
        )
        _partes.append(f"{_nombre} ({_estado}" + (f" — {_error})" if _error else ")"))
    st.caption("⚠️ No se pudieron actualizar: " + "; ".join(_partes))

# Aplicar cambios de nombre y clasificaciones manuales del usuario
df = aplicar_ajustes(df, AJUSTES)
# Los movimientos marcados como "no contar" (p. ej. traspasos entre tus cuentas) se quedan
# fuera de todos los cálculos; solo se ven en Transacciones, marcados
df_completo = df
df = df[~df["excluido"]]

# --- Selector de periodo (común a todas las pestañas) ---
MESES_POR_DEFECTO = 4  # El mes actual y los 3 anteriores
_meses_disp = sorted(df["mes"].dropna().unique())
if _meses_disp:
    _mes_actual = pd.Period(date.today(), freq="M")
    _ultimo = _mes_actual if _mes_actual in _meses_disp else _meses_disp[-1]
    # Valor inicial, o corregido si el mes ya no existe (p. ej. al cambiar de cuentas)
    if st.session_state.get("periodo_desde") not in _meses_disp:
        st.session_state["periodo_desde"] = max(_meses_disp[0], _ultimo - (MESES_POR_DEFECTO - 1))

    # Los dos desplegables en la misma fila, también en el móvil
    with st.container(horizontal=True):
        # Nombres cortos ("Ago 2026") para que quepan dos desplegables en el móvil
        periodo_desde = st.selectbox(
            "Desde", _meses_disp, key="periodo_desde", format_func=lambda m: _mes(m, corto=True)
        )
        # "Hasta" solo ofrece meses iguales o posteriores a "Desde": no hay periodos al revés
        _opciones_hasta = [m for m in _meses_disp if m >= periodo_desde]
        if st.session_state.get("periodo_hasta") not in _opciones_hasta:
            st.session_state["periodo_hasta"] = _ultimo if _ultimo in _opciones_hasta else _opciones_hasta[-1]
        periodo_hasta = st.selectbox(
            "Hasta", _opciones_hasta, key="periodo_hasta", format_func=lambda m: _mes(m, corto=True)
        )

    df_p = df[(df["mes"] >= periodo_desde) & (df["mes"] <= periodo_hasta)]
    df_p_completo = df_completo[(df_completo["mes"] >= periodo_desde) & (df_completo["mes"] <= periodo_hasta)]
else:
    df_p = df  # Sin datos: las pestañas muestran su aviso
    df_p_completo = df_completo


def _mostrar_enlace_auth(app_id, private_key):
    """Genera (o reutiliza) el enlace de autorización de Santander."""
    import secrets as _secrets
    REDIRECT_URL = "https://appestadisticas-zapp2pp7lgzrnzsxvp2qdth.streamlit.app"

    if "auth_url" not in st.session_state:
        try:
            import enable_banking as eb
            state = _secrets.token_urlsafe(16)
            st.session_state["oauth_state"] = state
            auth_data = eb.start_auth(app_id, private_key, REDIRECT_URL, "Banco Santander", "ES", state=state)
            st.session_state["auth_url"] = auth_data.get("url")
        except Exception as e:
            st.error(f"Error al iniciar autorización: {e}")
            return
        finally:
            st.session_state["iniciar_auth"] = False

    auth_url = st.session_state.get("auth_url")
    if auth_url:
        st.markdown(
            "**Pulsa el enlace para autorizar el acceso en Santander:**\n\n"
            f"[Ir a Santander para autorizar]({auth_url})"
        )
        st.caption("Después de autorizar, serás redirigido aquí automáticamente.")


# --- Tabs principales ---
tab1, tab2, tab3, tab4, tab5 = st.tabs(["Dashboard", "Histórico", "Categorías", "Transacciones", "Conexión"])


# ═══════════════════════════════════════════════
# TAB 1 — DASHBOARD
# ═══════════════════════════════════════════════
with tab1:
    if df_p.empty:
        st.caption("Sin datos. Conecta tu cuenta en la pestaña **Conexión**.")
    else:
        df_mes = df_p  # Movimientos del periodo elegido

        # --- Notificación de transacciones sin clasificar (de todo el historial, no solo del periodo) ---
        movs_sin_clasificar = df[
            (df["categoria"] == "Otros") & (df["tipo"] == "gasto")
            & ~df["descripcion"].isin(AJUSTES["ignorados"])  # Los ignorados ya no se preguntan
        ]
        # Un resumen por comercio, de más a menos gasto (se calcula de una vez para todos)
        resumen_sc = movs_sin_clasificar.groupby("descripcion").agg(
            n=("monto", "size"), total=("monto", "sum"), ultima=("fecha", "max")
        )
        resumen_sc["total"] = resumen_sc["total"].abs()
        resumen_sc = resumen_sc.sort_values("total", ascending=False)

        if not resumen_sc.empty:
            # Solo se muestran unos pocos cada vez: cada comercio son 3 controles y con todo el
            # historial puede haber cientos, lo que hacía muy lenta cada recarga
            n_visibles = st.session_state.get("sc_visibles", 10)
            # Para contar a cuántos afectaría cada palabra clave (limpiando cada texto una sola vez)
            movs_por_desc = df.loc[df["tipo"] == "gasto", "descripcion"].value_counts().to_dict()
            textos_limpios = {d: f" {normalizar(d)} " for d in movs_por_desc}
            with st.expander(f"⚠️ {len(resumen_sc)} comercio(s) sin clasificar — pulsa para asignarles una categoría"):
                st.caption("Ordenados de más a menos gasto.")
                for desc, fila in resumen_sc.head(n_visibles).iterrows():
                    # Importe y fecha para reconocer el movimiento (o total y último, si hay varios)
                    ultima = fecha_corta(fila["ultima"])
                    if fila["n"] == 1:
                        detalle = f"{fila['total']:,.2f} € · {ultima}"
                    else:
                        detalle = f"{fila['n']} movimientos · {fila['total']:,.2f} € en total · último el {ultima}"
                    st.markdown(f"**{desc}**")
                    st.caption(detalle)

                    # Palabra clave sugerida (editable): la regla clasifica también los parecidos
                    palabra = st.text_input(
                        "Aplicar también a todos los que contengan:",
                        value=sugerir_palabra_clave(desc), key=f"kw_{desc}",
                        placeholder="Vacío: solo este comercio",
                    ).strip()
                    if palabra:
                        buscada = f" {normalizar(palabra)} "
                        parecidos = [d for d, texto in textos_limpios.items() if buscada in texto]
                        n_movs = sum(movs_por_desc[d] for d in parecidos)
                        st.caption(f"Afectaría a {n_movs} movimientos de {len(parecidos)} comercio(s).")

                    # Selector y botón en la misma fila, también en el móvil
                    with st.container(horizontal=True, vertical_alignment="center"):
                        cat_elegida = st.selectbox(
                            "Categoría",
                            AJUSTES["categorias"],
                            key=f"sc_{desc}",
                            label_visibility="collapsed",
                        )
                        guardar = st.button("Guardar", key=f"btn_{desc}")
                    if guardar:
                        error_regla = anadir_regla(AJUSTES, palabra, cat_elegida) if palabra else None
                        if error_regla:
                            st.error(error_regla)
                        else:
                            # Este comercio queda clasificado aunque la palabra no aparezca en su texto
                            AJUSTES["clasificaciones"][desc] = cat_elegida
                            st.rerun()

                    # Si no se quiere clasificar: que deje de preguntarse, o que no cuente en nada
                    with st.container(horizontal=True):
                        if st.button("Ignorar", key=f"ign_{desc}",
                                     help="Sigue contando como «Otros», pero deja de aparecer aquí"):
                            AJUSTES["ignorados"].append(desc)
                            st.rerun()
                        if st.button("No contar", key=f"exc_{desc}",
                                     help="No cuenta en ningún cálculo (p. ej. traspasos entre tus cuentas)"):
                            AJUSTES["excluir_descripciones"].append(desc)
                            st.rerun()
                    st.divider()

                restantes = len(resumen_sc) - n_visibles
                if restantes > 0:
                    st.button(
                        f"Mostrar {min(10, restantes)} más (quedan {restantes})", key="sc_mas",
                        on_click=lambda: st.session_state.update(sc_visibles=n_visibles + 10),
                    )

        ingresos  = df_mes[df_mes["tipo"] == "ingreso"]["monto"].sum()
        gastos    = abs(df_mes[df_mes["tipo"] == "gasto"]["monto"].sum())
        balance   = ingresos - gastos
        ahorro    = round((balance / ingresos * 100), 1) if ingresos > 0 else 0.0

        # Tarjetas de métricas (2×2 en el móvil)
        _metricas([
            ("Ingresos", f"{ingresos:,.2f} €", None),
            ("Gastos",   f"{gastos:,.2f} €",   None),
            ("Balance",  f"{balance:,.2f} €",
             COLORS["balance_pos"] if balance >= 0 else COLORS["balance_neg"]),
            ("Ahorro",   f"{ahorro:.1f}%",     None),
        ])

        # Gastos fijos (categorías marcadas en Categorías → Editar categorías) y variables
        gastos_mes = df_mes[df_mes["tipo"] == "gasto"]
        fijos = abs(gastos_mes[gastos_mes["categoria"].isin(AJUSTES["fijas"])]["monto"].sum())
        variables = gastos - fijos
        pct_fijos = round(fijos / gastos * 100) if gastos > 0 else 0
        _metricas([
            (f"Fijos ({pct_fijos} %)",           f"{fijos:,.2f} €",     None),
            (f"Variables ({100 - pct_fijos} %)", f"{variables:,.2f} €", None),
        ])

        st.divider()

        col_rec, col_top = st.columns(2)

        # --- Recurrentes ---
        with col_rec:
            st.markdown("**Gastos recurrentes**")
            recurrentes = df_mes[
                df_mes["recurrente"] & (df_mes["tipo"] == "gasto")
            ][["descripcion", "monto"]].copy()
            recurrentes["monto"] = recurrentes["monto"].abs()

            if recurrentes.empty:
                st.caption("Sin recurrentes en este periodo.")
            else:
                filas = "".join(
                    _fila(
                        f'<span class="etiqueta" style="background:#E6F1FB;color:#185FA5;'
                        f'margin:0 6px 0 0;">mensual</span>{html.escape(row["descripcion"])}',
                        f'{row["monto"]:,.2f} €',
                    )
                    for _, row in recurrentes.iterrows()
                )
                st.markdown(filas, unsafe_allow_html=True)

        # --- Top 5 comercios ---
        with col_top:
            st.markdown("**Top 5 comercios**")
            top5 = (
                df_mes[df_mes["tipo"] == "gasto"]
                .groupby("descripcion")["monto"]
                .sum()
                .abs()
                .nlargest(5)
            )
            if top5.empty:
                st.caption("Sin datos.")
            else:
                filas = "".join(
                    _fila(f"{i}. {html.escape(nombre)}", f"{importe:,.2f} €", COLORS["income"])
                    for i, (nombre, importe) in enumerate(top5.items(), 1)
                )
                st.markdown(filas, unsafe_allow_html=True)


# ═══════════════════════════════════════════════
# TAB 2 — HISTÓRICO
# ═══════════════════════════════════════════════
with tab2:
    resumen = monthly_summary(df_p)

    if resumen.empty:
        st.caption("Sin datos. Conecta tu cuenta en la pestaña **Conexión**.")
    else:
        # Etiquetas cortas en dos líneas ("Jun" / "2026") para que quepan muchos meses en el móvil;
        # el mes incompleto lleva un asterisco y se explica debajo del gráfico
        etiquetas = [
            f"{nombre_mes(m, corto=True).split()[0]}{'*' if m == MES_PARCIAL else ''}<br>{m.year}"
            for m in resumen.index
        ]
        colores_gastos = [
            COLORS["balance_neg"] if d else COLORS["expense"]
            for d in resumen["deficit"]
        ]

        fig = go.Figure()
        fig.add_bar(
            name="Ingresos",
            x=etiquetas,
            y=resumen["ingresos"],
            marker_color=COLORS["income"],
            hovertemplate="%{x}<br>Ingresos: %{y:,.0f} €<extra></extra>",
        )
        fig.add_bar(
            name="Gastos",
            x=etiquetas,
            y=resumen["gastos"],
            marker_color=colores_gastos,
            hovertemplate="%{x}<br>Gastos: %{y:,.0f} €<extra></extra>",
        )
        fig.update_layout(
            barmode="group",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
            margin=dict(l=0, r=0, t=40, b=0),
            yaxis=dict(ticksuffix=" €", gridcolor="#e8e8e8"),
            xaxis=dict(gridcolor="rgba(0,0,0,0)"),
            font=dict(family="sans-serif", color="#52514e"),
            height=300,
            dragmode=False,
        )
        # Eje de categorías: una etiqueta por barra. Si no, Plotly lee "2026-08" como fecha
        # y pone marcas cada ~30 días (salían "Aug 1" y "Aug 31")
        fig.update_xaxes(fixedrange=True, type="category")
        fig.update_yaxes(fixedrange=True)
        # Tocar una barra la selecciona (solo por toque o clic: arrastrar sigue desactivado).
        # Cambiar la clave del gráfico es la forma de quitar la selección.
        evento = st.plotly_chart(
            fig, width="stretch", config=PLOTLY_CONFIG,
            on_select="rerun", selection_mode="points",
            key=f"hist_{st.session_state.get('hist_reinicio', 0)}",
        )
        if MES_PARCIAL in resumen.index:
            st.caption(f"\\* {nombre_mes(MES_PARCIAL)}: mes incompleto, solo incluye sus últimos días.")

        # --- Movimientos de la barra tocada ---
        puntos = evento.selection.points if evento else []
        if puntos:
            punto = puntos[0]
            mes_sel = resumen.index[punto.get("point_index", punto.get("point_number", 0))]
            tipo_sel = "ingreso" if punto.get("curve_number") == 0 else "gasto"  # 0 = barra de Ingresos
            movs_sel = df_p[(df_p["mes"] == mes_sel) & (df_p["tipo"] == tipo_sel)]
            titulo = "Ingresos" if tipo_sel == "ingreso" else "Gastos"
            with st.container(horizontal=True, vertical_alignment="center"):
                st.markdown(f"**{titulo} de {_mes(mes_sel)} · {abs(movs_sel['monto'].sum()):,.2f} €**")
                if st.button("Quitar selección", key="hist_quitar"):
                    st.session_state["hist_reinicio"] = st.session_state.get("hist_reinicio", 0) + 1
                    st.rerun()
            _lista_movimientos(movs_sel)
        else:
            st.caption("Toca una barra para ver sus movimientos.")

        st.divider()

        mejor_mes = resumen["balance"].idxmax()
        meses_deficit = resumen["deficit"].sum()
        ahorro_total = resumen[resumen["balance"] > 0]["balance"].sum()

        _metricas([
            ("Mejor mes",        _mes(mejor_mes), None),
            ("Meses en déficit", f"{int(meses_deficit)} de {len(resumen)}", None),
            ("Ahorro acumulado", f"{ahorro_total:,.0f} €", None),
        ], clase="tres")


# ═══════════════════════════════════════════════
# TAB 3 — CATEGORÍAS
# ═══════════════════════════════════════════════
with tab3:
    if df_p.empty:
        st.caption("Sin datos. Conecta tu cuenta en la pestaña **Conexión**.")
    else:
        df_c = df_p[df_p["tipo"] == "gasto"]
        por_cat = df_c.groupby("categoria")["monto"].sum().abs().sort_values(ascending=False)

        col_chart, col_list = st.columns([1, 1])

        with col_chart:
            colores_donut = [AJUSTES["colores"].get(c, COLORS["neutral"]) for c in por_cat.index]
            fig2 = go.Figure(go.Pie(
                labels=por_cat.index,
                values=por_cat.values,
                hole=0.6,
                marker_colors=colores_donut,
                textinfo="percent",
                hovertemplate="%{label}: %{value:,.2f} €<extra></extra>",
            ))
            fig2.update_layout(
                paper_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=0, r=0, t=10, b=0),
                showlegend=False,
                height=250,
            )
            st.plotly_chart(fig2, width="stretch", config=PLOTLY_CONFIG)

        with col_list:
            total_mes = por_cat.sum()
            # Todas las categorías caen en uno de los dos grupos: las marcadas son fijas, el resto variables
            grupos = [
                ("Fijos", por_cat[por_cat.index.isin(AJUSTES["fijas"])]),
                ("Variables", por_cat[~por_cat.index.isin(AJUSTES["fijas"])]),
            ]
            for nombre_grupo, cats_grupo in grupos:
                subtotal = cats_grupo.sum()
                pct_grupo = round(subtotal / total_mes * 100) if total_mes > 0 else 0
                html_grupo = (
                    f'<div style="display:flex;justify-content:space-between;align-items:baseline;'
                    f'border-bottom:1px solid #e3e2de;padding-bottom:4px;margin:6px 0 10px;">'
                    f'<span style="font-size:15px;font-weight:600;">{nombre_grupo}'
                    f'<span style="font-size:12px;font-weight:400;color:#898781;"> · {pct_grupo} %</span></span>'
                    f'<span style="font-size:15px;font-weight:600;">{subtotal:,.2f} €</span></div>'
                )
                if cats_grupo.empty:
                    html_grupo += '<p style="font-size:12px;color:#898781;margin:0 0 14px;">Sin gastos en este periodo.</p>'
                for cat, importe in cats_grupo.items():
                    pct = round(importe / total_mes * 100) if total_mes > 0 else 0
                    color = AJUSTES["colores"].get(cat, COLORS["neutral"])
                    html_grupo += (
                        f'<div style="padding-left:10px;">'
                        f'<div style="display:flex;justify-content:space-between;margin-bottom:2px;">'
                        f'<span style="font-size:13px;">● {html.escape(cat)}</span>'
                        f'<span style="font-size:13px;font-weight:500;">{importe:,.2f} €</span></div>'
                        f'<div style="background:#f0efec;border-radius:4px;height:4px;margin-bottom:10px;">'
                        f'<div style="background:{color};height:4px;border-radius:4px;width:{pct}%;"></div>'
                        f'</div></div>'
                    )
                st.markdown(html_grupo, unsafe_allow_html=True)

        st.divider()

    # --- Editor de categorías ---
    # Los cambios se aplican en callbacks (on_change / on_click), que se ejecutan
    # antes de repintar la página; así el campo de texto se puede vaciar sin bucles.
    def _al_anadir():
        st.session_state["error_cat"] = anadir_categoria(AJUSTES, st.session_state["nueva_cat"])
        st.session_state["nueva_cat"] = ""

    def _al_renombrar(viejo: str):
        clave = f"cat_{viejo}"
        error = renombrar_categoria(AJUSTES, viejo, st.session_state[clave])
        st.session_state["error_cat"] = error
        if error:
            st.session_state[clave] = viejo  # Deshacer lo escrito

    def _al_borrar(nombre: str):
        st.session_state["error_cat"] = borrar_categoria(AJUSTES, nombre)

    with st.expander("Editar categorías"):
        st.caption(
            "Renombra, elimina o añade categorías. Al eliminar una, sus movimientos pasan a "
            f"«{CATEGORIA_OTROS}», que no se puede cambiar."
        )
        st.text_input(
            "Nueva categoría", key="nueva_cat", on_change=_al_anadir,
            placeholder="Escribe el nombre y pulsa Enter",
        )
        error_cat = st.session_state.pop("error_cat", None)
        if error_cat:
            st.error(error_cat)

        for cat in list(AJUSTES["categorias"]):
            bloqueada = cat == CATEGORIA_OTROS
            clave = f"cat_{cat}"
            if clave not in st.session_state:
                st.session_state[clave] = cat  # Valor inicial del campo (sin usar value=)
            # Nombre y botón de borrar en la misma fila, también en el móvil
            with st.container(horizontal=True, vertical_alignment="center"):
                st.text_input(
                    cat, key=clave, label_visibility="collapsed",
                    on_change=_al_renombrar, args=(cat,), disabled=bloqueada,
                )
                st.button(
                    "✕", key=f"del_{cat}", help=f"Eliminar {cat}",
                    on_click=_al_borrar, args=(cat,), disabled=bloqueada,
                )

        # --- Gastos fijos: botones que se marcan y desmarcan tocándolos ---
        st.markdown("**¿Qué categorías son gastos fijos?**")
        st.caption("Lo que pagas sí o sí cada mes, aunque el importe cambie. El resto cuenta como variable.")
        # La clave cambia con las categorías y las fijas: así el widget nunca muestra un estado viejo
        clave_fijas = f"fijas_{hash((tuple(AJUSTES['categorias']), tuple(AJUSTES['fijas'])))}"

        def _al_cambiar_fijas():
            marcadas = st.session_state[clave_fijas] or []
            AJUSTES["fijas"] = [c for c in AJUSTES["categorias"] if c in marcadas]

        st.pills(
            "Gastos fijos", AJUSTES["categorias"], selection_mode="multi",
            default=AJUSTES["fijas"], key=clave_fijas, on_change=_al_cambiar_fijas,
            label_visibility="collapsed",
        )

        # --- Reglas por palabra clave (se crean al clasificar en el Dashboard) ---
        st.markdown("**Reglas por palabra clave**")
        if not AJUSTES["reglas"]:
            st.caption("Aún no hay reglas. Se crean al clasificar un comercio en el Dashboard.")
        for regla in sorted(AJUSTES["reglas"], key=lambda r: r["palabra"]):
            with st.container(horizontal=True, vertical_alignment="center"):
                st.markdown(f"«{html.escape(regla['palabra'])}» → {html.escape(regla['categoria'])}")
                st.button(
                    "✕", key=f"regla_{regla['palabra']}", help=f"Borrar la regla «{regla['palabra']}»",
                    on_click=borrar_regla, args=(AJUSTES, regla["palabra"]),
                )

        # --- Movimientos que no cuentan y comercios ignorados ---
        def _quitar(clave: str, valor: str):
            if valor in AJUSTES[clave]:
                AJUSTES[clave].remove(valor)

        def _al_excluir_palabra():
            error = anadir_exclusion_palabra(AJUSTES, st.session_state["excl_palabra"])
            st.session_state["error_excl"] = error
            if not error:
                st.session_state["excl_palabra"] = ""

        st.markdown("**Movimientos que no cuentan**")
        st.caption(
            "No suman en métricas, gráficos ni categorías (por ejemplo, traspasos entre tus "
            "cuentas). Siguen viéndose en Transacciones, marcados como «no cuenta»."
        )
        excl_palabra = st.text_input(
            "No contar los movimientos (gastos e ingresos) que contengan:",
            key="excl_palabra", placeholder="Por ejemplo: traspaso",
        ).strip()
        if excl_palabra:
            buscada = f" {normalizar(excl_palabra)} "
            textos = {d: f" {normalizar(d)} " for d in df_completo["descripcion"].unique()}
            afectados = df_completo[df_completo["descripcion"].map(lambda d: buscada in textos[d])]
            n_gastos = int((afectados["tipo"] == "gasto").sum())
            st.caption(
                f"Afectaría a {len(afectados)} movimientos ({n_gastos} gastos y "
                f"{len(afectados) - n_gastos} ingresos)."
            )
            st.button("No contar", key="excl_anadir", on_click=_al_excluir_palabra)
        error_excl = st.session_state.pop("error_excl", None)
        if error_excl:
            st.error(error_excl)

        for palabra_excl in AJUSTES["excluir_palabras"]:
            with st.container(horizontal=True, vertical_alignment="center"):
                st.markdown(f"Contienen «{html.escape(palabra_excl)}»")
                st.button("✕", key=f"exclp_{palabra_excl}", help="Volver a contarlos",
                          on_click=_quitar, args=("excluir_palabras", palabra_excl))

        # Un desplegable y un botón (y no un botón por comercio): la lista puede crecer mucho
        for clave, titulo, boton in (
            ("excluir_descripciones", "Comercios que no cuentan", "Volver a contar"),
            ("ignorados", "Comercios ignorados en el aviso de sin clasificar", "Volver a preguntar"),
        ):
            if AJUSTES[clave]:
                st.caption(f"{titulo} ({len(AJUSTES[clave])})")
                with st.container(horizontal=True, vertical_alignment="center"):
                    elegido = st.selectbox(titulo, sorted(AJUSTES[clave]), key=f"sel_{clave}",
                                           label_visibility="collapsed")
                    st.button(boton, key=f"quitar_{clave}", on_click=_quitar, args=(clave, elegido))


# ═══════════════════════════════════════════════
# TAB 4 — TRANSACCIONES
# ═══════════════════════════════════════════════
with tab4:
    busqueda = st.text_input("Buscar", placeholder="Nombre del comercio...", label_visibility="collapsed")
    cats_disponibles = ["Todas"] + sorted(df["categoria"].dropna().unique().tolist())
    # Los dos filtros comparten fila, también en el móvil
    with st.container(horizontal=True):
        cat_filtro = st.selectbox("Categoría", cats_disponibles, label_visibility="collapsed")
        tipo_filtro = st.selectbox("Tipo", ["Todos", "Gasto", "Ingreso"], label_visibility="collapsed")

    df_t = df_p_completo.copy()  # Movimientos del periodo elegido, también los que no cuentan
    if busqueda:
        df_t = df_t[df_t["descripcion"].str.contains(busqueda, case=False, na=False)]
    if cat_filtro != "Todas":
        df_t = df_t[df_t["categoria"] == cat_filtro]
    if tipo_filtro == "Gasto":
        df_t = df_t[df_t["tipo"] == "gasto"]
    elif tipo_filtro == "Ingreso":
        df_t = df_t[df_t["tipo"] == "ingreso"]

    st.caption(f"{len(df_t)} transacciones")

    _lista_movimientos(df_t, pista="Usa los filtros para acotar.")


# ═══════════════════════════════════════════════
# TAB 5 — CONEXIÓN BANCARIA
# ═══════════════════════════════════════════════
with tab5:
    st.markdown("### Conexión con Santander")

    app_id = _eb_app_id
    private_key = _eb_key
    session_id = _eb_session
    account_uid = _eb_account

    # --- Callback OAuth: Santander ha redirigido con ?code=... ---
    if _oauth_code:
        # Protección contra doble-consumo del código (Streamlit re-ejecuta el script)
        _code_key = f"eb_code_{abs(hash(_oauth_code)) % 1_000_000}"
        if st.session_state.get(_code_key):
            # Ya procesado en este render; limpiar URL y esperar el rerun
            st.query_params.clear()
        else:
            st.session_state[_code_key] = True
            received_state = st.query_params.get("state", "")
            expected_state = st.session_state.get("oauth_state", "")
            if expected_state and received_state != expected_state:
                st.error("Estado OAuth inválido. Vuelve a intentar la conexión.")
                st.query_params.clear()
            else:
                st.info("Código de autorización recibido. Obteniendo sesión...")
                try:
                    import enable_banking as eb
                    session_data = eb.exchange_code(app_id, private_key, _oauth_code)
                    new_session_id = session_data["session_id"]
                    accounts = session_data.get("accounts", [])

                    if new_session_id and accounts:
                        st.session_state["new_credentials"] = {
                            "session_id": new_session_id,
                            "accounts": accounts,
                            # Desde ahora hay ~1 h para pedir todo el historial
                            "autorizado": datetime.now(timezone.utc),
                        }
                        st.session_state.pop("descarga_completa", None)
                        st.query_params.clear()
                        st.session_state.pop("oauth_state", None)
                        st.session_state.pop("auth_url", None)
                        st.session_state.pop("account_list", None)
                        st.rerun()
                    else:
                        st.error("No se encontraron cuentas en la sesión.")
                        st.query_params.clear()
                except Exception as e:
                    st.error(f"Error al obtener sesión: {e}")
                    st.query_params.clear()

    # --- Credenciales recién obtenidas (tras limpiar la URL) ---
    elif st.session_state.get("new_credentials"):
        creds = st.session_state["new_credentials"]
        accounts = creds.get("accounts", [])
        # La API puede devolver cada cuenta como texto (solo el uid) o como objeto
        nuevos_uids = [a if isinstance(a, str) else a.get("uid", "") for a in accounts]
        st.success("¡Autorización completada!")

        # --- Paso 1: todo el historial (el banco solo lo permite ~1 h tras autorizar) ---
        st.markdown("**1. Descarga todo tu historial**")
        autorizado = creds.get("autorizado")
        transcurrido = (datetime.now(timezone.utc) - autorizado).total_seconds() // 60 if autorizado else 60
        minutos_restantes = max(0, 60 - int(transcurrido))
        resultado = st.session_state.get("descarga_completa")

        for r in resultado or []:
            linea = f"**{r['cuenta']}**: {r['movimientos']} movimientos"
            if r["desde"]:
                linea += f" desde el {fecha_corta(date.fromisoformat(r['desde']))}"
            linea += f" · {r['peticiones']} peticiones al banco"
            if r["problema"]:
                st.warning(f"{linea}\n\nIncompleta: {r['problema']}. Lo descargado se ha guardado igualmente.")
            else:
                st.success(linea)

        hay_problemas = any(r["problema"] for r in resultado or [])
        if minutos_restantes > 0 and (not resultado or hay_problemas):
            st.caption(
                "El banco solo deja pedir todo el historial durante ~1 hora tras autorizar. "
                f"Quedan unos {minutos_restantes} minutos. Puede tardar un poco: no cierres la página."
            )
            if st.button("Reintentar la descarga" if resultado else "Descargar todo el historial", type="primary"):
                import enable_banking as eb
                alm_desc, bloqueo_desc = _almacen()
                resumen = []
                with st.spinner("Descargando todo el historial…"):
                    for i, uid in enumerate(nuevos_uids):
                        if i > 0:
                            time.sleep(2.0)  # Pausa entre cuentas para no saturar la API
                        movs, peticiones, problema = eb.get_all_transactions(_eb_app_id, _eb_key, uid)
                        movs = [reducir_movimiento(t) for t in movs]
                        fechas = sorted(
                            f for t in movs
                            if (f := t.get("booking_date") or t.get("value_date") or t.get("transaction_date"))
                        )
                        desde = fechas[0][:10] if fechas else None
                        with bloqueo_desc:
                            ent = alm_desc.setdefault(
                                uid, {"raw": None, "obtenido": None, "desde": None, "ultimo_intento": None, "error": None}
                            )
                            # Solo si terminó bien se sabe que los datos llegan hasta hoy
                            _rellenar_historial(ent, movs, desde,
                                                obtenido=datetime.now(timezone.utc) if problema is None else None)
                        if problema is None:
                            motivo = None
                        elif problema == "tope":
                            motivo = "se alcanzó el tope de peticiones"
                        else:
                            motivo = _motivo_error(problema, uid)
                            print(f"[descarga completa] Fallo: {motivo}", flush=True)
                        resumen.append({"cuenta": f"Cuenta {i + 1}", "movimientos": len(movs), "desde": desde,
                                        "peticiones": peticiones, "problema": motivo})
                st.session_state["descarga_completa"] = resumen
                st.rerun()
        elif not resultado:
            st.warning(
                "Ha pasado más de una hora desde que autorizaste: para descargar todo el historial "
                "habría que renovar la conexión otra vez."
            )

        # --- Paso 2: copia antes de tocar los Secrets ---
        st.markdown(
            "**2. Descarga la copia de seguridad** en el desplegable *💾 Copia de seguridad* de más abajo. "
            "Hazlo **antes** del paso 3: al cambiar los Secrets la app se reinicia y se vacía la memoria."
        )

        # --- Paso 3: Secrets ---
        st.markdown("**3. Copia estos valores en los Secrets de Streamlit Cloud** (dentro de `[enable_banking]`):")
        if set(nuevos_uids) == set(_eb_account_uids):
            st.caption("✅ Los identificadores de cuenta no han cambiado: basta con actualizar `session_id`.")
        else:
            st.warning("⚠️ Los identificadores de cuenta han cambiado: actualiza también los `account_uid`.")
        lines = [f'session_id = "{creds["session_id"]}"']
        for i, (acc, uid) in enumerate(zip(accounts, nuevos_uids)):
            tipo = (acc.get("account_type") or acc.get("name")) if isinstance(acc, dict) else None
            suffix = f"_{i + 1}" if len(accounts) > 1 else ""
            lines.append(f'# {tipo or f"Cuenta {i + 1}"}')
            lines.append(f'account_uid{suffix} = "{uid}"')
        st.code("\n".join(lines), language="toml")
        st.caption("Streamlit Cloud → tu app → Settings → Secrets.")

        # --- Paso 4: restaurar ---
        st.markdown("**4. Cuando la app se reinicie, sube la copia** en *💾 Copia de seguridad*.")
        if st.button("Listo"):
            st.session_state.pop("new_credentials", None)
            st.session_state.pop("descarga_completa", None)
            st.rerun()

    # --- Banco ya conectado ---
    elif session_id and (account_uid or _eb_account_uids):
        st.success("Banco conectado")
        uid_display = (account_uid or (_eb_account_uids[0] if _eb_account_uids else ""))
        st.caption(f"Sesión activa · {len(_eb_account_uids or [account_uid])} cuenta(s) · `{uid_display[:8]}...`")
        if st.button("Renovar conexión"):
            st.session_state["iniciar_auth"] = True
            st.session_state.pop("auth_url", None)
            st.rerun()
        if st.session_state.get("iniciar_auth"):
            _mostrar_enlace_auth(app_id, private_key)

    # --- Sin sesión ---
    else:
        st.warning("No hay banco conectado.")
        if st.button("Conectar Santander"):
            st.session_state["iniciar_auth"] = True
            st.session_state.pop("auth_url", None)
            st.rerun()
        if st.session_state.get("iniciar_auth"):
            _mostrar_enlace_auth(app_id, private_key)

    # --- Copia de seguridad (categorías + historial de movimientos) ---
    with st.expander("💾 Copia de seguridad"):
        st.caption(
            "Tus categorías y tu historial de movimientos se guardan en la memoria del servidor: "
            "se mantienen al recargar y entre dispositivos, pero **se pierden cada vez que la app "
            "se reinicia**. Descarga una copia de vez en cuando y súbela tras un reinicio."
        )
        _alm_copia, _bloqueo_copia = _almacen()
        with _bloqueo_copia:
            _cuentas_copia = {
                uid: {
                    "movimientos": [reducir_movimiento(t) for t in ent["raw"]],
                    "desde": _desde_cuenta(ent),
                    "obtenido": ent["obtenido"].isoformat() if ent.get("obtenido") else None,
                }
                for uid, ent in _alm_copia.items() if ent.get("raw")
            }
        _n_movs = sum(len(c["movimientos"]) for c in _cuentas_copia.values())
        st.download_button(
            "Descargar copia",
            data=exportar_copia(AJUSTES, _cuentas_copia),
            file_name=f"copia_gastos_{date.today():%Y%m%d}.json",
            mime="application/json",
        )
        st.caption(
            f"Incluye tus categorías y {_n_movs} movimientos. ⚠️ Contiene todos tus movimientos "
            "bancarios: guárdala en un sitio privado y nunca en el repositorio, que es público."
        )

        copia_ok = st.session_state.pop("copia_ok", None)
        if copia_ok:
            st.success(copia_ok)

        archivo = st.file_uploader("Subir copia", type=["json"], key="subir_copia", max_upload_size=10)
        # El archivo sigue en el widget en cada recarga: se aplica solo una vez por subida
        if archivo is not None and st.session_state.get("copia_aplicada") != archivo.file_id:
            st.session_state["copia_aplicada"] = archivo.file_id
            try:
                ajustes_copia, cuentas_copia = importar_copia(archivo.getvalue())
            except ValueError as e:
                st.error(f"No se ha aplicado la copia: {e}")
            else:
                AJUSTES.clear()
                AJUSTES.update(ajustes_copia)
                with _bloqueo_copia:
                    antes = sum(len(e.get("raw") or []) for e in _alm_copia.values())
                    for uid, cuenta in cuentas_copia.items():
                        obtenido = datetime.fromisoformat(cuenta["obtenido"]) if cuenta["obtenido"] else None
                        if obtenido and obtenido.tzinfo is None:
                            obtenido = obtenido.replace(tzinfo=timezone.utc)
                        ent = _alm_copia.setdefault(
                            uid, {"raw": None, "obtenido": None, "desde": None, "ultimo_intento": None, "error": None}
                        )
                        _rellenar_historial(ent, cuenta["movimientos"], cuenta["desde"], obtenido=obtenido)
                    recuperados = sum(len(e.get("raw") or []) for e in _alm_copia.values()) - antes
                st.session_state["copia_ok"] = (
                    f"Copia aplicada: {len(ajustes_copia['categorias'])} categorías restauradas y "
                    f"{recuperados} movimientos anteriores recuperados."
                )
                st.rerun()

    # --- Diagnóstico ---
    with st.expander("🔍 Diagnóstico de conexión"):
        col_d1, col_d2 = st.columns(2)
        btn_test   = col_d1.button("Probar conexión ahora")
        btn_reload = col_d2.button("Forzar recarga de datos")

        if btn_reload:
            # Se salta la espera de 6 h tras un fallo; los datos buenos se conservan si vuelve a fallar
            st.session_state["forzar_recarga"] = True
            st.session_state.pop("account_list", None)
            st.rerun()

        if btn_test:
            import enable_banking as eb
            # Comprueba la sesión (no gasta el cupo de consultas de movimientos)
            if session_id:
                try:
                    status = eb.get_session_status(app_id, private_key, session_id)
                    st.success(f"Sesión válida — {len(status.get('accounts', []))} cuenta(s) encontrada(s)")
                except Exception as e:
                    st.error(f"Sesión inválida: {_motivo_error(e, session_id)}")
            else:
                st.warning("No hay session_id configurado.")

        # Estado de la carga de movimientos de cada cuenta (lo guardado en el servidor, sin llamar al banco)
        if _eb_account_uids:
            st.markdown("**Carga de movimientos**")
            _alm, _ = _almacen()
            _nombres = (
                {a["uid"]: a["label"] for a in _accounts}
                or {uid: f"Cuenta {i + 1}" for i, uid in enumerate(_eb_account_uids)}
            )
            for _i, _uid in enumerate(_eb_account_uids):
                _nombre = _nombres.get(_uid, f"Cuenta {_i + 1}")
                _ent = _alm.get(_uid)
                if not _ent or _ent["ultimo_intento"] is None:
                    st.caption(f"{_nombre}: todavía no se han pedido sus movimientos.")
                    continue
                _datos = (
                    f"{len(_ent['raw'] or [])} movimientos del "
                    f"{_ent['obtenido'].astimezone(ZONA_ES):%d/%m a las %H:%M}"
                    if _ent["obtenido"] else "sin datos"
                )
                _intento = f"último intento el {_ent['ultimo_intento'].astimezone(ZONA_ES):%d/%m a las %H:%M}"
                if _ent["error"]:
                    _proximo = (_ent["ultimo_intento"] + ESPERA_TRAS_FALLO).astimezone(ZONA_ES)
                    st.error(
                        f"**{_nombre}**: {_datos} · {_intento}\n\n"
                        f"Motivo: {_ent['error']}. Reintento automático a partir de las {_proximo:%H:%M}."
                    )
                else:
                    st.caption(f"**{_nombre}**: {_datos} · {_intento}")
        else:
            st.warning("No hay account_uid configurado.")
