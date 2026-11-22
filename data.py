import json
import re

import pandas as pd

# --- Paleta de colores (tema azul aprobado) ---
COLORS = {
    "income":      "#2a78d6",
    "expense":     "#eb6834",
    "balance_pos": "#1baf7a",
    "balance_neg": "#e34948",
    "investment":  "#6250d6",
    "neutral":     "#888780",
}

CAT_COLORS = {
    "Alimentación": "#2a78d6",
    "Transporte":   "#eb6834",
    "Servicios":    "#1baf7a",
    "Ocio":         "#eda100",
    "Salud":        "#e87ba4",
    "Inversión":    "#6250d6",
    "Ropa":         "#D85A30",
    "Otros":        "#888780",
}

DEFAULT_CATEGORIAS = list(CAT_COLORS.keys())

# Nombres en español: strftime usa el idioma del servidor (inglés en Streamlit Cloud)
MESES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio",
         "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]


def nombre_mes(periodo, corto: bool = False) -> str:
    """'Agosto 2026', o 'Ago 2026' si corto, a partir de un periodo mensual o una fecha."""
    nombre = MESES[periodo.month - 1]
    return f"{nombre[:3] if corto else nombre} {periodo.year}"


def fecha_corta(fecha) -> str:
    """'17 nov 2026'."""
    return f"{fecha.day} {MESES[fecha.month - 1][:3].lower()} {fecha.year}"

# Reglas de clasificación por palabra clave.
# Buscan en cualquier parte de la descripción, incluyendo formatos reales
# del Santander como "COMPRA TARJETA *MERCADONA 0987 MADRID ES".
MERCHANT_RULES = {
    "Alimentación": [
        "mercadona", "carrefour", "lidl", "aldi", "dia ", "eroski", "supercor",
        "consum", "alcampo", "ahorramas", "hipercor", "simply", "bonarea",
        "el arbol", "condis", "froiz", "coviran",
    ],
    "Transporte": [
        "repsol", "cepsa", "bp ", "galp", "shell", "cabify", "uber", "renfe",
        "metro ", "fgc", "emt ", "tmt ", "blablacar", "parking", "autopista",
        "peaje", "iberia", "vueling", "ryanair", "easyjet", "aena",
    ],
    "Servicios": [
        "netflix", "spotify", "hbo", "disney", "prime video", "apple.com",
        "google", "microsoft", "gym", "gimnasio", "iberdrola", "endesa",
        "naturgy", "vodafone", "movistar", "orange", "masmovil", "jazztel",
        "seguro", "mutua", "mapfre", "axa", "allianz", "comunidad",
        "agua ", "basura",
    ],
    "Ocio": [
        "restaurante", "bar ", "cafeteria", "cafe ", "cine", "teatro",
        "amazon", "steam", "playstation", "fnac", "ticketmaster", "eventbrite",
        "decathlon", "leroy merlin", "ikea",
    ],
    "Salud": [
        "farmacia", "clinica", "hospital", "medico", "dentista", "optica",
        "sanitas", "adeslas", "asisa", "laboratorio", "parafarmacia",
    ],
    "Inversión": [
        "myinvestor", "degiro", "indexa", "finizens", "openbank invest",
        "ing invest", "trading",
    ],
    "Ropa": [
        "zara", "h&m", "hm.com", "mango", "primark", "el corte ingles",
        "corte ingles", "nike", "adidas", "pull", "bershka", "stradivarius",
        "massimo dutti", "springfield", "inside",
    ],
}

# Palabras que indican que el movimiento es un ingreso aunque el monto sea positivo
INCOME_KEYWORDS = ["nomina", "nómina", "transferencia recibida", "bizum recibido", "ingreso"]


# ═══════════════════════════════════════════════
# AJUSTES DE CATEGORÍAS DEL USUARIO
# ═══════════════════════════════════════════════
# Estructura: {"categorias": [...], "colores": {cat: "#rrggbb"},
#              "clasificaciones": {descripción: cat},   # asignadas a mano
#              "renombradas": {cat_automática: cat_actual}}  # renombradas o borradas

CATEGORIA_OTROS = "Otros"      # Destino de lo no clasificado: no se puede renombrar ni borrar
CATEGORIA_INGRESO = "Ingreso"  # Reservada para los ingresos
CATEGORIAS_AUTOMATICAS = set(MERCHANT_RULES) | {CATEGORIA_OTROS}
PALETA_EXTRA = ["#0f8b8d", "#a05195", "#2f4b7c", "#7a9e2e", "#b5651d", "#5f7d95", "#c94f7c", "#3d9970"]

# Límites para validar la copia que sube el usuario
MAX_CATEGORIAS = 50
MAX_LONG_NOMBRE = 40
MAX_CLASIFICACIONES = 5000
MAX_LONG_DESCRIPCION = 300
MAX_BYTES_COPIA = 500_000
_COLOR_VALIDO = re.compile(r"^#[0-9a-fA-F]{6}$")  # Evita inyectar CSS a través del color


def ajustes_por_defecto() -> dict:
    return {
        "categorias": DEFAULT_CATEGORIAS.copy(),
        "colores": dict(CAT_COLORS),
        "clasificaciones": {},
        "renombradas": {},
    }


def _color_libre(ajustes: dict) -> str:
    """Primer color de la paleta extra que no use otra categoría."""
    usados = set(ajustes["colores"].values())
    return next((c for c in PALETA_EXTRA if c not in usados), COLORS["neutral"])


def _validar_nombre(ajustes: dict, nombre: str) -> str | None:
    """Devuelve el motivo por el que el nombre no vale, o None si es válido."""
    if not nombre:
        return "El nombre no puede estar vacío."
    if len(nombre) > MAX_LONG_NOMBRE:
        return f"El nombre puede tener como máximo {MAX_LONG_NOMBRE} caracteres."
    if nombre == CATEGORIA_INGRESO:
        return f"«{CATEGORIA_INGRESO}» está reservado para los ingresos."
    if nombre in ajustes["categorias"]:
        return "Ya existe una categoría con ese nombre."
    return None


def _redirigir(ajustes: dict, viejo: str, nuevo: str) -> None:
    """Hace que todo lo que apuntaba a la categoría 'viejo' apunte a 'nuevo'."""
    for desc, cat in ajustes["clasificaciones"].items():
        if cat == viejo:
            ajustes["clasificaciones"][desc] = nuevo
    for original, actual in ajustes["renombradas"].items():
        if actual == viejo:
            ajustes["renombradas"][original] = nuevo
    # Si 'viejo' es un nombre de la clasificación automática, sus movimientos pasan a 'nuevo'
    if viejo in CATEGORIAS_AUTOMATICAS and viejo not in ajustes["renombradas"]:
        ajustes["renombradas"][viejo] = nuevo
    # Quitar las entradas que han vuelto a su nombre original
    for original in [o for o, a in ajustes["renombradas"].items() if o == a]:
        del ajustes["renombradas"][original]


def anadir_categoria(ajustes: dict, nombre: str) -> str | None:
    nombre = nombre.strip()
    if len(ajustes["categorias"]) >= MAX_CATEGORIAS:
        return f"Como máximo puede haber {MAX_CATEGORIAS} categorías."
    error = _validar_nombre(ajustes, nombre)
    if error:
        return error
    ajustes["colores"][nombre] = _color_libre(ajustes)
    ajustes["categorias"].append(nombre)
    return None


def renombrar_categoria(ajustes: dict, viejo: str, nuevo: str) -> str | None:
    nuevo = nuevo.strip()
    if viejo == CATEGORIA_OTROS:
        return f"«{CATEGORIA_OTROS}» no se puede renombrar."
    if nuevo == viejo:
        return None
    error = _validar_nombre(ajustes, nuevo)
    if error:
        return error
    ajustes["categorias"][ajustes["categorias"].index(viejo)] = nuevo
    ajustes["colores"][nuevo] = ajustes["colores"].pop(viejo, COLORS["neutral"])  # Conserva su color
    _redirigir(ajustes, viejo, nuevo)
    return None


def borrar_categoria(ajustes: dict, nombre: str) -> str | None:
    if nombre == CATEGORIA_OTROS:
        return f"«{CATEGORIA_OTROS}» no se puede borrar: es donde van los comercios sin clasificar."
    ajustes["categorias"].remove(nombre)
    ajustes["colores"].pop(nombre, None)
    _redirigir(ajustes, nombre, CATEGORIA_OTROS)  # Sus movimientos pasan a "Otros"
    return None


def aplicar_ajustes(df: pd.DataFrame, ajustes: dict) -> pd.DataFrame:
    """Aplica a los gastos los cambios de nombre y las clasificaciones manuales."""
    df = df.copy()
    gastos = df["tipo"] == "gasto"
    categorias = df.loc[gastos, "categoria"].replace(ajustes["renombradas"])
    manuales = df.loc[gastos, "descripcion"].map(ajustes["clasificaciones"])
    df.loc[gastos, "categoria"] = categorias.where(manuales.isna(), manuales)  # Lo manual manda
    return df


def exportar_ajustes(ajustes: dict) -> str:
    return json.dumps({"version": 1, **ajustes}, ensure_ascii=False, indent=2)


def importar_ajustes(contenido: bytes) -> dict:
    """Valida una copia subida por el usuario. Lanza ValueError con el motivo si no es válida."""
    if len(contenido) > MAX_BYTES_COPIA:
        raise ValueError("El archivo es demasiado grande para ser una copia de categorías.")
    try:
        datos = json.loads(contenido.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("El archivo no es un JSON válido.")
    if not isinstance(datos, dict) or datos.get("version") != 1:
        raise ValueError("No parece una copia de Mis Gastos (versión desconocida).")

    categorias = datos.get("categorias")
    if (
        not isinstance(categorias, list)
        or not 0 < len(categorias) <= MAX_CATEGORIAS
        or not all(isinstance(c, str) and c == c.strip() and 0 < len(c) <= MAX_LONG_NOMBRE for c in categorias)
        or len(set(categorias)) != len(categorias)
        or CATEGORIA_OTROS not in categorias
        or CATEGORIA_INGRESO in categorias
    ):
        raise ValueError("La lista de categorías de la copia no es válida.")

    def _textos(clave: str) -> dict:
        valor = datos.get(clave, {})
        if not isinstance(valor, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in valor.items()
        ):
            raise ValueError(f"El apartado «{clave}» de la copia no tiene el formato esperado.")
        return valor

    colores, clasificaciones, renombradas = _textos("colores"), _textos("clasificaciones"), _textos("renombradas")
    if not all(_COLOR_VALIDO.match(c) for c in colores.values()):
        raise ValueError("La copia contiene colores no válidos.")
    if len(clasificaciones) > MAX_CLASIFICACIONES or not all(
        len(d) <= MAX_LONG_DESCRIPCION and c in categorias for d, c in clasificaciones.items()
    ):
        raise ValueError("Las clasificaciones de la copia no son válidas.")
    if not all(o in CATEGORIAS_AUTOMATICAS and a in categorias for o, a in renombradas.items()):
        raise ValueError("Los cambios de nombre de la copia no son válidos.")

    resultado = {
        "categorias": list(categorias),
        "colores": {c: colores[c] for c in categorias if c in colores},
        "clasificaciones": dict(clasificaciones),
        "renombradas": dict(renombradas),
    }
    for c in categorias:
        if c not in resultado["colores"]:
            resultado["colores"][c] = _color_libre(resultado)
    # Categorías automáticas que faltan en la copia: sus movimientos van a "Otros", como al borrarlas
    for auto in CATEGORIAS_AUTOMATICAS:
        if auto not in categorias and auto not in resultado["renombradas"]:
            resultado["renombradas"][auto] = CATEGORIA_OTROS
    return resultado


def categorize(description: str) -> str:
    """Clasifica una transacción por palabras clave en la descripción."""
    d = description.lower()
    # Normalizar caracteres especiales del banco
    d = d.replace("*", " ").replace("/", " ").replace(".", " ")
    for cat, keywords in MERCHANT_RULES.items():
        if any(kw in d for kw in keywords):
            return cat
    return "Otros"


def is_income(description: str) -> bool:
    """Detecta si una descripción corresponde a un ingreso."""
    d = description.lower()
    return any(kw in d for kw in INCOME_KEYWORDS)


def parse_eb_transactions(raw: list) -> pd.DataFrame:
    """Convierte la respuesta de Enable Banking al DataFrame interno."""
    rows = []
    for t in raw:
        try:
            amount = float(t["transaction_amount"]["amount"])
        except (KeyError, ValueError, TypeError):
            continue

        indicator = t.get("credit_debit_indicator", "DBIT")
        if indicator == "CRDT":
            tipo = "ingreso"
            monto = abs(amount)
        else:
            tipo = "gasto"
            monto = -abs(amount)

        # Descripción: varios campos posibles según el banco
        desc = (
            (t.get("creditor") or {}).get("name")
            or (t.get("debtor") or {}).get("name")
            or ((t.get("remittance_information") or [""])[0])
            or t.get("entry_reference")
            or "Sin descripción"
        )
        desc = str(desc).strip()

        # Si la descripción sugiere ingreso aunque sea DBIT, corregir tipo
        if is_income(desc):
            tipo = "ingreso"
            monto = abs(amount)

        fecha_str = (
            t.get("booking_date")
            or t.get("value_date")
            or t.get("transaction_date")
        )
        if not fecha_str:
            continue

        rows.append({
            "fecha":       pd.to_datetime(fecha_str),
            "monto":       monto,
            "descripcion": desc,
            "categoria":   categorize(desc) if tipo == "gasto" else "Ingreso",
            "recurrente":  False,
            "tipo":        tipo,
        })

    if not rows:
        return pd.DataFrame(columns=["fecha", "monto", "descripcion", "categoria", "recurrente", "tipo", "mes"])

    df = pd.DataFrame(rows)
    df["mes"] = df["fecha"].dt.to_period("M")
    return df.sort_values("fecha", ascending=False).reset_index(drop=True)


def monthly_summary(df: pd.DataFrame) -> pd.DataFrame:
    ingresos = df[df["tipo"] == "ingreso"].groupby("mes")["monto"].sum()
    gastos   = df[df["tipo"] == "gasto"].groupby("mes")["monto"].sum().abs()
    resumen  = pd.DataFrame({"ingresos": ingresos, "gastos": gastos}).fillna(0)
    resumen["balance"]    = resumen["ingresos"] - resumen["gastos"]
    resumen["ahorro_pct"] = resumen.apply(
        lambda r: round(r["balance"] / r["ingresos"] * 100, 1) if r["ingresos"] > 0 else 0.0,
        axis=1,
    )
    resumen["deficit"]    = resumen["balance"] < 0
    return resumen.sort_index()
