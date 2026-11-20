import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from data import (
    get_transactions,
    parse_eb_transactions,
    monthly_summary,
    COLORS,
    CAT_COLORS,
    DEFAULT_CATEGORIAS,
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
        pwd = st.text_input("Contraseña", type="password", placeholder="Introduce tu contraseña")
        if st.button("Entrar", use_container_width=True):
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

/* Métricas: texto más compacto */
[data-testid="stMetricLabel"] p {font-size: 0.8rem; color: #52514e;}
[data-testid="stMetricValue"] {font-size: 1.5rem; font-weight: 500;}
[data-testid="stMetricDelta"] {font-size: 0.85rem;}

/* Tabs */
[data-testid="stTabs"] button {font-size: 0.9rem;}
</style>
""", unsafe_allow_html=True)

# --- Credenciales Enable Banking ---
try:
    _eb_cfg     = st.secrets.get("enable_banking", {})
    _eb_app_id  = _eb_cfg.get("application_id")
    _eb_key     = _eb_cfg.get("private_key")
    _eb_session = _eb_cfg.get("session_id") or st.secrets.get("session_id")
    _eb_account = _eb_cfg.get("account_uid") or st.secrets.get("account_uid")
except Exception:
    _eb_cfg = {}
    _eb_app_id = _eb_key = _eb_session = _eb_account = None


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
            account_id = acc.get("account_id") or {}
            iban = account_id.get("iban", "") if isinstance(account_id, dict) else ""
            tipo = acc.get("account_type") or acc.get("name") or ""
            if tipo:
                label = tipo.capitalize()
            elif iban:
                label = f"····{iban[-4:]}"
            else:
                label = f"Cuenta {i + 1}"
            result.append({"uid": acc["uid"], "label": label})
        st.session_state["account_list"] = result
        return result
    except Exception:
        return []


def _empty_df() -> pd.DataFrame:
    return pd.DataFrame(columns=["fecha", "monto", "descripcion", "categoria", "recurrente", "tipo", "mes"])


@st.cache_data(ttl=300)
def load_data(account_uids: tuple) -> pd.DataFrame:
    """Carga y combina transacciones de las cuentas seleccionadas."""
    if not account_uids:
        return _empty_df()
    try:
        cfg = st.secrets.get("enable_banking", {})
        app_id = cfg.get("application_id")
        private_key = cfg.get("private_key")

        if not all([app_id, private_key]):
            return _empty_df()

        import enable_banking as eb
        from datetime import date, timedelta
        date_from = (date.today() - timedelta(days=180)).isoformat()

        all_raw = []
        for uid in account_uids:
            raw = eb.get_transactions(app_id, private_key, uid, date_from=date_from)
            all_raw.extend(raw)
        return parse_eb_transactions(all_raw)
    except Exception:
        pass
    return _empty_df()

# --- Categorías en session_state (persisten durante la sesión) ---
if "categorias" not in st.session_state:
    st.session_state.categorias = DEFAULT_CATEGORIAS.copy()

# --- Clasificaciones manuales guardadas por el usuario ---
if "clasificaciones_manuales" not in st.session_state:
    st.session_state.clasificaciones_manuales = {}

# --- Título ---
st.title("Mis Gastos")

# --- Detectar callback OAuth (Santander redirige con ?code=...) ---
_oauth_code = st.query_params.get("code")

# --- Selector de cuentas (encima de las pestañas) ---
_accounts = _get_account_list()

if len(_accounts) > 1:
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
elif _eb_account:
    _selected_uids = (_eb_account,)
else:
    _selected_uids = ()

df = load_data(_selected_uids)

# Aplicar clasificaciones manuales al DataFrame
for desc, cat in st.session_state.clasificaciones_manuales.items():
    df.loc[df["descripcion"] == desc, "categoria"] = cat


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
                    col_desc, col_cat, col_btn = st.columns([3, 2, 1])
                    col_desc.markdown(f"**{desc}**")
                    cat_elegida = col_cat.selectbox(
                        "Categoría",
                        st.session_state.categorias,
                        key=f"sc_{desc}",
                        label_visibility="collapsed",
                    )
                    if col_btn.button("Guardar", key=f"btn_{desc}"):
                        st.session_state.clasificaciones_manuales[desc] = cat_elegida
                        df.loc[df["descripcion"] == desc, "categoria"] = cat_elegida
                        st.rerun()

        ingresos  = df_mes[df_mes["tipo"] == "ingreso"]["monto"].sum()
        gastos    = abs(df_mes[df_mes["tipo"] == "gasto"]["monto"].sum())
        balance   = ingresos - gastos
        ahorro    = round((balance / ingresos * 100), 1) if ingresos > 0 else 0.0

        # Tarjetas de métricas
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Ingresos",  f"{ingresos:,.2f} €")
        c2.metric("Gastos",    f"{gastos:,.2f} €")
        c3.metric(
            "Balance",
            f"{balance:,.2f} €",
            delta=f"{'↑' if balance >= 0 else '↓'} {abs(balance):,.0f} €",
            delta_color="normal" if balance >= 0 else "inverse",
        )
        c4.metric("Ahorro", f"{ahorro:.1f}%")

        st.divider()

        col_rec, col_top = st.columns(2)

        # --- Recurrentes ---
        with col_rec:
            st.markdown("**Gastos recurrentes**")
            recurrentes = df_mes[
                (df_mes["recurrente"] == True) & (df_mes["tipo"] == "gasto")
            ][["descripcion", "monto"]].copy()
            recurrentes["monto"] = recurrentes["monto"].abs()

            if recurrentes.empty:
                st.caption("Sin recurrentes este mes.")
            else:
                for _, row in recurrentes.iterrows():
                    ca, cb = st.columns([3, 1])
                    ca.markdown(
                        f'<span style="background:#E6F1FB;color:#185FA5;'
                        f'font-size:11px;padding:2px 7px;border-radius:6px;'
                        f'margin-right:6px;">mensual</span>{row["descripcion"]}',
                        unsafe_allow_html=True,
                    )
                    cb.markdown(
                        f'<p style="text-align:right;font-weight:500;">'
                        f'{row["monto"]:,.2f} €</p>',
                        unsafe_allow_html=True,
                    )

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
                for i, (nombre, importe) in enumerate(top5.items(), 1):
                    ca, cb = st.columns([3, 1])
                    ca.write(f"{i}. {nombre}")
                    cb.markdown(
                        f'<p style="text-align:right;font-weight:500;'
                        f'color:{COLORS["income"]};">{importe:,.2f} €</p>',
                        unsafe_allow_html=True,
                    )


# ═══════════════════════════════════════════════
# TAB 2 — HISTÓRICO
# ═══════════════════════════════════════════════
with tab2:
    resumen = monthly_summary(df)
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
    )
    st.plotly_chart(fig, use_container_width=True)

    st.divider()

    # Métricas de resumen
    if not resumen.empty:
        mejor_mes = resumen["balance"].idxmax()
        meses_deficit = resumen["deficit"].sum()
        ahorro_total = resumen[resumen["balance"] > 0]["balance"].sum()

        c1, c2, c3 = st.columns(3)
        c1.metric("Mejor mes",        mejor_mes.strftime("%B %Y").capitalize())
        c2.metric("Meses en déficit", f"{int(meses_deficit)} de {len(resumen)}")
        c3.metric("Ahorro acumulado", f"{ahorro_total:,.0f} €")


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
            colores_donut = [CAT_COLORS.get(c, COLORS["neutral"]) for c in por_cat.index]
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
            st.plotly_chart(fig2, use_container_width=True)

        with col_list:
            total_mes = por_cat.sum()
            for cat, importe in por_cat.items():
                pct = round(importe / total_mes * 100) if total_mes > 0 else 0
                color = CAT_COLORS.get(cat, COLORS["neutral"])
                st.markdown(
                    f'<div style="display:flex;justify-content:space-between;'
                    f'margin-bottom:2px;">'
                    f'<span style="font-size:13px;">● {cat}</span>'
                    f'<span style="font-size:13px;font-weight:500;">{importe:,.2f} €</span>'
                    f'</div>'
                    f'<div style="background:#f0efec;border-radius:4px;height:4px;'
                    f'margin-bottom:10px;">'
                    f'<div style="background:{color};height:4px;border-radius:4px;'
                    f'width:{pct}%;"></div></div>',
                    unsafe_allow_html=True,
                )

        st.divider()

    # Editor de categorías (siempre visible)
    with st.expander("Editar categorías"):
        st.caption("Puedes renombrar o eliminar categorías, y añadir nuevas.")

        cats = st.session_state.categorias
        nueva = st.text_input("Nueva categoría", placeholder="Escribe el nombre y pulsa Enter")
        if nueva and nueva not in cats:
            cats.append(nueva)
            st.session_state.categorias = cats
            st.rerun()

        for i, cat in enumerate(cats):
            c1, c2, c3 = st.columns([3, 1, 1])
            nuevo_nombre = c1.text_input(f"cat_{i}", value=cat, label_visibility="collapsed")
            if nuevo_nombre != cat:
                st.session_state.categorias[i] = nuevo_nombre
                st.rerun()
            if c3.button("✕", key=f"del_{i}", help=f"Eliminar {cat}"):
                st.session_state.categorias.pop(i)
                st.rerun()


# ═══════════════════════════════════════════════
# TAB 4 — TRANSACCIONES
# ═══════════════════════════════════════════════
with tab4:
    col_busq, col_cat, col_tipo = st.columns([3, 2, 1])

    busqueda = col_busq.text_input("Buscar", placeholder="Nombre del comercio...", label_visibility="collapsed")
    cats_disponibles = ["Todas"] + sorted(df["categoria"].dropna().unique().tolist())
    cat_filtro = col_cat.selectbox("Categoría", cats_disponibles, label_visibility="collapsed")
    tipo_filtro = col_tipo.selectbox("Tipo", ["Todos", "Gasto", "Ingreso"], label_visibility="collapsed")

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

    for _, row in df_t.head(100).iterrows():
        es_ingreso = row["tipo"] == "ingreso"
        color_monto = COLORS["balance_pos"] if es_ingreso else COLORS["balance_neg"]
        signo = "+" if es_ingreso else "−"
        badge_rec = (
            '<span style="background:#E6F1FB;color:#185FA5;font-size:10px;'
            'padding:1px 6px;border-radius:4px;margin-left:6px;">recurrente</span>'
            if row["recurrente"] else ""
        )
        badge_ing = (
            '<span style="background:#EAF3DE;color:#3B6D11;font-size:10px;'
            'padding:1px 6px;border-radius:4px;margin-left:6px;">ingreso</span>'
            if es_ingreso else ""
        )

        ca, cb = st.columns([4, 1])
        ca.markdown(
            f'<div style="padding:2px 0;">'
            f'<span style="font-size:14px;">{row["descripcion"]}'
            f'{badge_rec}{badge_ing}</span><br>'
            f'<span style="font-size:12px;color:#898781;">'
            f'{row["fecha"].strftime("%d %b %Y").lstrip("0")} · {row["categoria"]}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
        cb.markdown(
            f'<p style="text-align:right;font-weight:500;color:{color_monto};'
            f'margin-top:8px;">{signo}{abs(row["monto"]):,.2f} €</p>',
            unsafe_allow_html=True,
        )
        st.divider()

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
        received_state = st.query_params.get("state", "")
        expected_state = st.session_state.get("oauth_state", "")
        if expected_state and received_state != expected_state:
            st.error("Estado OAuth inválido. Vuelve a intentar la conexión.")
        else:
            st.info("Código de autorización recibido. Obteniendo sesión...")
            try:
                import enable_banking as eb
                session_data = eb.exchange_code(app_id, private_key, _oauth_code)
                new_session_id = session_data["session_id"]
                accounts = session_data.get("accounts", [])
                new_account_uid = accounts[0]["uid"] if accounts else None

                if new_session_id and new_account_uid:
                    st.session_state["new_credentials"] = {
                        "session_id": new_session_id,
                        "account_uid": new_account_uid,
                    }
                    st.query_params.clear()
                    st.session_state.pop("oauth_state", None)
                    st.session_state.pop("auth_url", None)
                    st.rerun()
                else:
                    st.error("No se encontraron cuentas en la sesión.")
            except Exception as e:
                st.error(f"Error al obtener sesión: {e}")

    # --- Credenciales recién obtenidas (tras limpiar la URL) ---
    elif st.session_state.get("new_credentials"):
        creds = st.session_state["new_credentials"]
        st.success("¡Autorización completada!")
        st.markdown("**Copia estos valores en los Secrets de Streamlit Cloud (dentro de `[enable_banking]`):**")
        st.code(
            f'session_id = "{creds["session_id"]}"\n'
            f'account_uid = "{creds["account_uid"]}"',
            language="toml",
        )
        st.markdown(
            "Ve a **Streamlit Cloud → tu app → Settings → Secrets** "
            "y añade estos dos valores dentro de `[enable_banking]`."
        )
        if st.button("Listo"):
            st.session_state.pop("new_credentials", None)
            st.rerun()

    # --- Banco ya conectado ---
    elif session_id and account_uid:
        st.success("Banco conectado")
        st.caption(f"Session activa · account_uid: `{account_uid[:8]}...`")
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
