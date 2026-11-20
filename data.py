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
