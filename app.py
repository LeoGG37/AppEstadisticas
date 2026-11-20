import html
import threading
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from data import (
    parse_eb_transactions,
    monthly_summary,
    COLORS,
    CATEGORIA_OTROS,
    ajustes_por_defecto,
    anadir_categoria,
    renombrar_categoria,
    borrar_categoria,
    aplicar_ajustes,
    exportar_ajustes,
    importar_ajustes,
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


@st.cache_resource
def _almacen() -> tuple:
    """Memoria compartida del servidor: {uid: {"raw", "obtenido", "ultimo_intento"}}.

    Sobrevive a recargas de página y a varias pestañas abiertas, pero se pierde
    si la app se reinicia (push o inactividad). El lock evita llamadas duplicadas.
    """
    return {}, threading.Lock()


def load_data(account_uids: tuple, forzar: bool = False) -> tuple:
    """Devuelve (transacciones, cuentas sin actualizar).

    Cuenta por cuenta: si sus datos tienen más de 24 h se piden al banco; si la
    llamada falla se conservan los últimos datos buenos. Las cuentas sin
    actualizar se devuelven como [(uid, fecha_de_sus_datos_o_None)].
    """
    if not account_uids:
        return _empty_df(), []

    almacen, bloqueo = _almacen()
    sin_actualizar = []
    with bloqueo:
        llamadas = 0
        for uid in account_uids:
            entrada = almacen.setdefault(uid, {"raw": None, "obtenido": None, "ultimo_intento": None})
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
                    date_from = (date.today() - timedelta(days=180)).isoformat()
                    entrada["raw"] = eb.get_transactions(_eb_app_id, _eb_key, uid, date_from=date_from)
                    entrada["obtenido"] = ahora
                except Exception:
                    pass  # 429 u otro error: se quedan los datos anteriores

            if entrada["obtenido"] is None or ahora - entrada["obtenido"] > CADUCIDAD_DATOS:
                sin_actualizar.append((uid, entrada["obtenido"]))

        raw_total = [t for uid in account_uids for t in (almacen[uid]["raw"] or [])]

    return parse_eb_transactions(raw_total), sin_actualizar

@st.cache_resource
def _ajustes() -> dict:
    """Categorías y clasificaciones del usuario, compartidas por todas las sesiones.

    Sobreviven a recargas y son las mismas en el móvil y en el PC, pero se pierden
    al reiniciar la app: para eso está la copia descargable de la pestaña Categorías.
    """
    return ajustes_por_defecto()


AJUSTES = _ajustes()

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

# Aviso cuando alguna cuenta muestra datos antiguos o aún no tiene datos
if _sin_actualizar:
    _etiquetas = (
        {a["uid"]: a["label"] for a in _accounts}
        or {uid: f"Cuenta {i + 1}" for i, uid in enumerate(_eb_account_uids)}
    )
    _partes = []
    for _uid, _obtenido in _sin_actualizar:
        _nombre = _etiquetas.get(_uid, _uid[:8])
        if _obtenido:
            _partes.append(f"{_nombre} (datos del {_obtenido.astimezone(ZONA_ES):%d/%m a las %H:%M})")
        else:
            _partes.append(f"{_nombre} (sin datos todavía)")
    st.caption("⚠️ No se pudieron actualizar: " + ", ".join(_partes))

# Aplicar cambios de nombre y clasificaciones manuales del usuario
df = aplicar_ajustes(df, AJUSTES)


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
    meses = sorted(df["mes"].unique(), reverse=True)
    if not meses:
        st.caption("Sin datos. Conecta tu cuenta en la pestaña **Conexión**.")
    else:
        mes_sel = st.selectbox(
            "Mes",
            meses,
            format_func=lambda m: m.strftime("%B %Y").capitalize(),
            label_visibility="collapsed",
        )

        df_mes = df[df["mes"] == mes_sel]

        # --- Notificación de transacciones sin clasificar ---
        sin_clasificar = df[
            (df["categoria"] == "Otros") & (df["tipo"] == "gasto")
        ]["descripcion"].unique()

        if len(sin_clasificar) > 0:
            with st.expander(f"⚠️ {len(sin_clasificar)} comercio(s) sin clasificar — pulsa para asignarles una categoría"):
                for desc in sin_clasificar:
                    st.markdown(f"**{desc}**")
                    # Selector y botón en la misma fila, también en el móvil
                    with st.container(horizontal=True, vertical_alignment="center"):
                        cat_elegida = st.selectbox(
                            "Categoría",
                            AJUSTES["categorias"],
                            key=f"sc_{desc}",
                            label_visibility="collapsed",
                        )
                        if st.button("Guardar", key=f"btn_{desc}"):
                            AJUSTES["clasificaciones"][desc] = cat_elegida
                            st.rerun()

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
                st.caption("Sin recurrentes este mes.")
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
    resumen = monthly_summary(df)

    if resumen.empty:
        st.caption("Sin datos. Conecta tu cuenta en la pestaña **Conexión**.")
    else:
        etiquetas = [str(m) for m in resumen.index]
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
        fig.update_xaxes(fixedrange=True)
        fig.update_yaxes(fixedrange=True)
        st.plotly_chart(fig, width="stretch", config=PLOTLY_CONFIG)

        st.divider()

        mejor_mes = resumen["balance"].idxmax()
        meses_deficit = resumen["deficit"].sum()
        ahorro_total = resumen[resumen["balance"] > 0]["balance"].sum()

        _metricas([
            ("Mejor mes",        mejor_mes.strftime("%B %Y").capitalize(), None),
            ("Meses en déficit", f"{int(meses_deficit)} de {len(resumen)}", None),
            ("Ahorro acumulado", f"{ahorro_total:,.0f} €", None),
        ], clase="tres")


# ═══════════════════════════════════════════════
# TAB 3 — CATEGORÍAS
# ═══════════════════════════════════════════════
with tab3:
    meses_c = sorted(df["mes"].unique(), reverse=True)
    if not meses_c:
        st.caption("Sin datos. Conecta tu cuenta en la pestaña **Conexión**.")
    else:
        mes_c = st.selectbox(
            "Mes",
            meses_c,
            format_func=lambda m: m.strftime("%B %Y").capitalize(),
            label_visibility="collapsed",
            key="mes_categorias",
        )

        df_c = df[(df["mes"] == mes_c) & (df["tipo"] == "gasto")]
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
            for cat, importe in por_cat.items():
                pct = round(importe / total_mes * 100) if total_mes > 0 else 0
                color = AJUSTES["colores"].get(cat, COLORS["neutral"])
                st.markdown(
                    f'<div style="display:flex;justify-content:space-between;'
                    f'margin-bottom:2px;">'
                    f'<span style="font-size:13px;">● {html.escape(cat)}</span>'
                    f'<span style="font-size:13px;font-weight:500;">{importe:,.2f} €</span>'
                    f'</div>'
                    f'<div style="background:#f0efec;border-radius:4px;height:4px;'
                    f'margin-bottom:10px;">'
                    f'<div style="background:{color};height:4px;border-radius:4px;'
                    f'width:{pct}%;"></div></div>',
                    unsafe_allow_html=True,
                )

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
            fija = cat == CATEGORIA_OTROS
            clave = f"cat_{cat}"
            if clave not in st.session_state:
                st.session_state[clave] = cat  # Valor inicial del campo (sin usar value=)
            # Nombre y botón de borrar en la misma fila, también en el móvil
            with st.container(horizontal=True, vertical_alignment="center"):
                st.text_input(
                    cat, key=clave, label_visibility="collapsed",
                    on_change=_al_renombrar, args=(cat,), disabled=fija,
                )
                st.button(
                    "✕", key=f"del_{cat}", help=f"Eliminar {cat}",
                    on_click=_al_borrar, args=(cat,), disabled=fija,
                )

    # --- Copia de seguridad de las categorías ---
    with st.expander("Copia de seguridad de categorías"):
        st.caption(
            "Las categorías se guardan en la memoria del servidor: se mantienen al recargar y "
            "entre dispositivos, pero **se pierden cada vez que la app se reinicia** (por "
            "ejemplo al hacer push). Descarga una copia después de cambiarlas y súbela tras un reinicio."
        )
        st.download_button(
            "Descargar copia",
            data=exportar_ajustes(AJUSTES),
            file_name=f"categorias_gastos_{date.today():%Y%m%d}.json",
            mime="application/json",
        )
        st.caption("⚠️ La copia incluye nombres de comercios: guárdala en un sitio privado.")

        copia_ok = st.session_state.pop("copia_ok", None)
        if copia_ok:
            st.success(copia_ok)

        # 1 MB: una copia real ocupa unos pocos KB; evita subir archivos grandes al servidor
        archivo = st.file_uploader("Subir copia", type=["json"], key="subir_copia", max_upload_size=1)
        # El archivo sigue en el widget en cada recarga: se aplica solo una vez por subida
        if archivo is not None and st.session_state.get("copia_aplicada") != archivo.file_id:
            st.session_state["copia_aplicada"] = archivo.file_id
            try:
                nuevos = importar_ajustes(archivo.getvalue())
            except ValueError as e:
                st.error(f"No se ha aplicado la copia: {e}")
            else:
                AJUSTES.clear()
                AJUSTES.update(nuevos)
                st.session_state["copia_ok"] = f"Copia aplicada: {len(nuevos['categorias'])} categorías restauradas."
                st.rerun()


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

    df_t = df.copy()
    if busqueda:
        df_t = df_t[df_t["descripcion"].str.contains(busqueda, case=False, na=False)]
    if cat_filtro != "Todas":
        df_t = df_t[df_t["categoria"] == cat_filtro]
    if tipo_filtro == "Gasto":
        df_t = df_t[df_t["tipo"] == "gasto"]
    elif tipo_filtro == "Ingreso":
        df_t = df_t[df_t["tipo"] == "ingreso"]

    st.caption(f"{len(df_t)} transacciones")

    # Toda la lista en un único bloque HTML: más rápido de pintar en el móvil
    filas = []
    for _, row in df_t.head(100).iterrows():
        es_ingreso = row["tipo"] == "ingreso"
        color_monto = COLORS["balance_pos"] if es_ingreso else COLORS["balance_neg"]
        signo = "+" if es_ingreso else "−"
        etiquetas = ""
        if row["recurrente"]:
            etiquetas += '<span class="etiqueta" style="background:#E6F1FB;color:#185FA5;">recurrente</span>'
        if es_ingreso:
            etiquetas += '<span class="etiqueta" style="background:#EAF3DE;color:#3B6D11;">ingreso</span>'

        # La descripción viene del banco: se escapa para que no pueda inyectar HTML
        izquierda = (
            f'{html.escape(row["descripcion"])}{etiquetas}'
            f'<div class="fila-sub">{row["fecha"].strftime("%d %b %Y").lstrip("0")}'
            f' · {html.escape(str(row["categoria"]))}</div>'
        )
        filas.append(_fila(izquierda, f'{signo}{abs(row["monto"]):,.2f} €', color_monto))

    if filas:
        st.markdown("".join(filas), unsafe_allow_html=True)

    if len(df_t) > 100:
        st.caption(f"Mostrando 100 de {len(df_t)} transacciones. Usa los filtros para acotar.")


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
                        }
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
        st.success("¡Autorización completada!")
        st.markdown("**Copia estos valores en los Secrets de Streamlit Cloud (dentro de `[enable_banking]`):**")

        # Construir bloque TOML con session_id + un account_uid por cuenta
        lines = [f'session_id = "{creds["session_id"]}"']
        for i, acc in enumerate(accounts):
            uid = acc.get("uid", "")
            tipo = acc.get("account_type") or acc.get("name") or f"Cuenta {i + 1}"
            suffix = f"_{i + 1}" if len(accounts) > 1 else ""
            lines.append(f'# {tipo}')
            lines.append(f'account_uid{suffix} = "{uid}"')

        st.code("\n".join(lines), language="toml")
        st.markdown(
            "Ve a **Streamlit Cloud → tu app → Settings → Secrets** "
            "y añade estos valores dentro de `[enable_banking]`."
        )
        st.caption(
            "💡 Los `account_uid` son permanentes — no cambian aunque renueves la conexión. "
            "Solo necesitarás actualizar `session_id` cada ~180 días."
        )
        if st.button("Listo"):
            st.session_state.pop("new_credentials", None)
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
            # 1) Sesión + info de cuentas para diagnosticar nombres
            if session_id:
                try:
                    status = eb.get_session_status(app_id, private_key, session_id)
                    accs = status.get("accounts", [])
                    st.success(f"Sesión válida — {len(accs)} cuenta(s) encontrada(s)")
                    for acc in accs:
                        if isinstance(acc, str):
                            st.caption(f"uid: `{acc[:12]}...` (la API devuelve solo el UID)")
                        else:
                            aid = acc.get("account_id") or {}
                            iban = aid.get("iban", "—") if isinstance(aid, dict) else "—"
                            st.caption(
                                f"uid: `{acc.get('uid','?')[:12]}...` | "
                                f"type: `{acc.get('account_type','—')}` | "
                                f"name: `{acc.get('name','—')}` | "
                                f"iban: `{iban}`"
                            )
                except Exception as e:
                    st.error(f"Sesión inválida: {e}")
            else:
                st.warning("No hay session_id configurado.")

            # 2) Transacciones: solo mostramos lo que hay guardado (sin llamar al banco)
            if _eb_account_uids:
                n_txns = len(df)
                if n_txns > 0:
                    st.success(f"Datos guardados — {n_txns} transacción(es)")
                else:
                    st.warning(
                        "Sin datos guardados. El límite de llamadas lo impone el banco (PSD2). "
                        "Espera unas horas y pulsa **Forzar recarga de datos**."
                    )
            else:
                st.warning("No hay account_uid configurado.")
