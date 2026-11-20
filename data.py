import calendar
import random
from datetime import date

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


def _make_transactions() -> pd.DataFrame:
    random.seed(42)
    rows = []
    today = date(2026, 11, 20)

    # Gastos y cobros que se repiten cada mes en día fijo
    recurrentes = [
        {"desc": "Nómina Empresa",  "monto":  2800.00, "dia": 14, "cat": "Ingreso",   "tipo": "ingreso"},
        {"desc": "MyInvestor",      "monto":  -300.00, "dia": 14, "cat": "Inversión", "tipo": "gasto"},
        {"desc": "Seguro médico",   "monto":   -45.00, "dia":  1, "cat": "Servicios", "tipo": "gasto"},
        {"desc": "Iberdrola",       "monto":   -65.00, "dia": 10, "cat": "Servicios", "tipo": "gasto"},
        {"desc": "Vodafone",        "monto":   -35.00, "dia": 20, "cat": "Servicios", "tipo": "gasto"},
        {"desc": "Netflix",         "monto":   -17.99, "dia": 15, "cat": "Servicios", "tipo": "gasto"},
        {"desc": "Spotify",         "monto":   -10.99, "dia":  7, "cat": "Servicios", "tipo": "gasto"},
    ]

    # Gastos variables que aparecen N veces al mes con importes aleatorios
    variables = [
        {"desc": "Mercadona",       "rango": (-95, -55),  "veces": (3, 5), "cat": "Alimentación"},
        {"desc": "Carrefour",       "rango": (-60, -30),  "veces": (1, 2), "cat": "Alimentación"},
        {"desc": "Repsol",          "rango": (-90, -55),  "veces": (2, 3), "cat": "Transporte"},
        {"desc": "Restaurante",     "rango": (-55, -20),  "veces": (2, 5), "cat": "Ocio"},
        {"desc": "Bar Cafetería",   "rango": (-12,  -3),  "veces": (4, 8), "cat": "Ocio"},
        {"desc": "Amazon",          "rango": (-120, -20), "veces": (1, 3), "cat": "Ocio"},
        {"desc": "Farmacia",        "rango": (-45, -15),  "veces": (1, 3), "cat": "Salud"},
        {"desc": "Zara",            "rango": (-90, -30),  "veces": (0, 2), "cat": "Ropa"},
        {"desc": "El Corte Inglés", "rango": (-130, -40), "veces": (0, 1), "cat": "Ropa"},
    ]

    # 6 meses; factor > 1 simula más gasto en vacaciones
    meses = [
        (2026, 6,  1.0),
        (2026, 7,  1.0),
        (2026, 8,  1.5),   # agosto: vacaciones
        (2026, 9,  1.6),   # septiembre: vuelta al cole
        (2026, 10, 0.95),
        (2026, 11, 1.0),
    ]

    for year, month, factor in meses:
        last_day = calendar.monthrange(year, month)[1]
        # Noviembre solo hasta hoy
        if year == today.year and month == today.month:
            last_day = today.day

        for r in recurrentes:
            if r["dia"] <= last_day:
                rows.append({
                    "fecha":       date(year, month, r["dia"]),
                    "monto":       r["monto"],
                    "descripcion": r["desc"],
                    "categoria":   r["cat"],
                    "recurrente":  True,
                    "tipo":        r["tipo"],
                })

        for v in variables:
            n = random.randint(v["veces"][0], v["veces"][1])
            for _ in range(n):
                dia = random.randint(1, last_day)
                monto = round(random.uniform(v["rango"][0], v["rango"][1]) * factor, 2)
                rows.append({
                    "fecha":       date(year, month, dia),
                    "monto":       monto,
                    "descripcion": v["desc"],
                    "categoria":   v["cat"],
                    "recurrente":  False,
                    "tipo":        "gasto",
                })

    df = pd.DataFrame(rows)
    df["fecha"] = pd.to_datetime(df["fecha"])
    df["mes"] = df["fecha"].dt.to_period("M")
    return df.sort_values("fecha", ascending=False).reset_index(drop=True)


def get_transactions() -> pd.DataFrame:
    # TODO: cuando tengas las credenciales de Enable Banking, sustituye esta
    # línea por la llamada real a la API:
    #   from enable_banking import fetch_transactions
    #   return fetch_transactions(st.secrets["enable_banking"])
    return _make_transactions()


def monthly_summary(df: pd.DataFrame) -> pd.DataFrame:
    ingresos = df[df["tipo"] == "ingreso"].groupby("mes")["monto"].sum()
    gastos   = df[df["tipo"] == "gasto"].groupby("mes")["monto"].sum().abs()
    resumen  = pd.DataFrame({"ingresos": ingresos, "gastos": gastos}).fillna(0)
    resumen["balance"]    = resumen["ingresos"] - resumen["gastos"]
    resumen["ahorro_pct"] = (resumen["balance"] / resumen["ingresos"] * 100).round(1)
    resumen["deficit"]    = resumen["balance"] < 0
    return resumen.sort_index()
