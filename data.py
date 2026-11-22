import json
import re
import unicodedata
from datetime import datetime

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
#              "renombradas": {cat_automática: cat_actual},  # renombradas o borradas
#              "fijas": [...],  # categorías de gasto fijo; el resto cuenta como variable
#              "reglas": [{"palabra": "mercadona", "categoria": "Alimentación"}, ...]}

CATEGORIA_OTROS = "Otros"      # Destino de lo no clasificado: no se puede renombrar ni borrar
# Gasto fijo = lo que se paga sí o sí cada mes, aunque el importe cambie (p. ej. el supermercado)
DEFAULT_FIJAS = ["Alimentación", "Transporte", "Servicios", "Salud", "Inversión"]
CATEGORIA_INGRESO = "Ingreso"  # Reservada para los ingresos
CATEGORIAS_AUTOMATICAS = set(MERCHANT_RULES) | {CATEGORIA_OTROS}
PALETA_EXTRA = ["#0f8b8d", "#a05195", "#2f4b7c", "#7a9e2e", "#b5651d", "#5f7d95", "#c94f7c", "#3d9970"]

# Límites para validar los ajustes guardados
MAX_CATEGORIAS = 50
MAX_LONG_NOMBRE = 40
MAX_CLASIFICACIONES = 5000
MAX_LONG_DESCRIPCION = 300
MAX_REGLAS = 500
MIN_LONG_PALABRA = 3   # Palabras más cortas ("sa", "es") agruparían de más
MAX_LONG_PALABRA = 60
_COLOR_VALIDO = re.compile(r"^#[0-9a-fA-F]{6}$")  # Evita inyectar CSS a través del color


def ajustes_por_defecto() -> dict:
    return {
        "categorias": DEFAULT_CATEGORIAS.copy(),
        "colores": dict(CAT_COLORS),
        "clasificaciones": {},
        "renombradas": {},
        "fijas": DEFAULT_FIJAS.copy(),
        "reglas": [],
    }


# ═══════════════════════════════════════════════
# REGLAS POR PALABRA CLAVE
# ═══════════════════════════════════════════════

def normalizar(texto: str) -> str:
    """Minúsculas, sin tildes y solo letras/números separados por un espacio."""
    sin_tildes = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", sin_tildes.lower()).split())


# Partes del texto del banco que no identifican al comercio
_PREFIJOS = ["compra tarjeta", "compra tarj", "pago movil", "pago tarjeta", "transaccion contactless",
             "compra", "pago", "contactless", "recibo", "adeudo", "cargo", "tarjeta", "tarj"]
_IGNORAR = {"es", "sa", "sl", "slu", "sau", "de", "del", "la", "el", "los", "las", "y", "en",
            "www", "com", "the", "eu", "esp", "spain"}
# Movimientos donde lo que sigue suele ser el nombre de una persona: no se sugiere nada
_SIN_SUGERENCIA = {"bizum", "transferencia", "traspaso"}


def sugerir_palabra_clave(descripcion: str) -> str:
    """Propone la palabra que identifica al comercio (p. ej. 'mercadona'), o '' si no conviene."""
    texto = normalizar(descripcion)
    if _SIN_SUGERENCIA & set(texto.split()):
        return ""
    for prefijo in _PREFIJOS:
        if texto.startswith(prefijo + " "):
            texto = texto[len(prefijo) + 1:]
            break
    for palabra in texto.split():
        if len(palabra) >= MIN_LONG_PALABRA and not any(c.isdigit() for c in palabra) and palabra not in _IGNORAR:
            return palabra
    return ""


def coincide(descripcion: str, palabra: str) -> bool:
    """La palabra (o frase) aparece como palabras completas: 'bar' no coincide con 'barcelona'."""
    return f" {normalizar(palabra)} " in f" {normalizar(descripcion)} "


def anadir_regla(ajustes: dict, palabra: str, categoria: str) -> str | None:
    """Crea (o actualiza) la regla 'palabra → categoría'. Devuelve el motivo si no es válida."""
    palabra = normalizar(palabra)
    if len(palabra) < MIN_LONG_PALABRA:
        return f"La palabra clave debe tener al menos {MIN_LONG_PALABRA} letras."
    if len(palabra) > MAX_LONG_PALABRA:
        return f"La palabra clave puede tener como máximo {MAX_LONG_PALABRA} caracteres."
    if categoria not in ajustes["categorias"]:
        return "Esa categoría no existe."
    reglas = [r for r in ajustes["reglas"] if r["palabra"] != palabra]
    if len(reglas) >= MAX_REGLAS:
        return f"Como máximo puede haber {MAX_REGLAS} reglas."
    ajustes["reglas"] = reglas + [{"palabra": palabra, "categoria": categoria}]
    return None


def borrar_regla(ajustes: dict, palabra: str) -> None:
    ajustes["reglas"] = [r for r in ajustes["reglas"] if r["palabra"] != palabra]


def categoria_por_reglas(descripcion: str, reglas: list) -> str | None:
    """Categoría de la regla que coincide; si hay varias, la de la palabra más larga (más concreta)."""
    texto = f" {normalizar(descripcion)} "  # Se limpia una vez; las palabras ya se guardan limpias
    coincidentes = [r for r in reglas if f" {r['palabra']} " in texto]
    if not coincidentes:
        return None
    return max(coincidentes, key=lambda r: len(r["palabra"]))["categoria"]


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
    ajustes["fijas"] = [nuevo if c == viejo else c for c in ajustes["fijas"]]     # y si es fija
    for regla in ajustes["reglas"]:                                               # y sus reglas
        if regla["categoria"] == viejo:
            regla["categoria"] = nuevo
    _redirigir(ajustes, viejo, nuevo)
    return None


def borrar_categoria(ajustes: dict, nombre: str) -> str | None:
    if nombre == CATEGORIA_OTROS:
        return f"«{CATEGORIA_OTROS}» no se puede borrar: es donde van los comercios sin clasificar."
    ajustes["categorias"].remove(nombre)
    ajustes["colores"].pop(nombre, None)
    ajustes["fijas"] = [c for c in ajustes["fijas"] if c != nombre]
    ajustes["reglas"] = [r for r in ajustes["reglas"] if r["categoria"] != nombre]
    _redirigir(ajustes, nombre, CATEGORIA_OTROS)  # Sus movimientos pasan a "Otros"
    return None


def aplicar_ajustes(df: pd.DataFrame, ajustes: dict) -> pd.DataFrame:
    """Aplica a los gastos, por este orden: cambios de nombre, reglas por palabra clave y
    clasificaciones manuales de un comercio concreto (la más concreta manda)."""
    df = df.copy()
    gastos = df["tipo"] == "gasto"
    categorias = df.loc[gastos, "categoria"].replace(ajustes["renombradas"])
    descripciones = df.loc[gastos, "descripcion"]
    if ajustes.get("reglas"):
        por_regla = {d: categoria_por_reglas(d, ajustes["reglas"]) for d in descripciones.unique()}
        de_reglas = descripciones.map(por_regla)
        categorias = categorias.where(de_reglas.isna(), de_reglas)
    manuales = descripciones.map(ajustes["clasificaciones"])
    df.loc[gastos, "categoria"] = categorias.where(manuales.isna(), manuales)
    return df


def validar_ajustes(datos) -> dict:
    """Comprueba unos ajustes guardados (p. ej. en la caja fuerte) y devuelve una versión limpia.

    Lanza ValueError con el motivo si no son válidos.
    """
    if not isinstance(datos, dict):
        raise ValueError("Los ajustes guardados no tienen el formato esperado.")

    categorias = datos.get("categorias")
    if (
        not isinstance(categorias, list)
        or not 0 < len(categorias) <= MAX_CATEGORIAS
        or not all(isinstance(c, str) and c == c.strip() and 0 < len(c) <= MAX_LONG_NOMBRE for c in categorias)
        or len(set(categorias)) != len(categorias)
        or CATEGORIA_OTROS not in categorias
        or CATEGORIA_INGRESO in categorias
    ):
        raise ValueError("La lista de categorías guardada no es válida.")

    def _textos(clave: str) -> dict:
        valor = datos.get(clave, {})
        if not isinstance(valor, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in valor.items()
        ):
            raise ValueError(f"El apartado «{clave}» de los ajustes guardados no tiene el formato esperado.")
        return valor

    colores, clasificaciones, renombradas = _textos("colores"), _textos("clasificaciones"), _textos("renombradas")
    if not all(_COLOR_VALIDO.match(c) for c in colores.values()):
        raise ValueError("Los ajustes guardados contienen colores no válidos.")
    if len(clasificaciones) > MAX_CLASIFICACIONES or not all(
        len(d) <= MAX_LONG_DESCRIPCION and c in categorias for d, c in clasificaciones.items()
    ):
        raise ValueError("Las clasificaciones guardadas no son válidas.")
    if not all(o in CATEGORIAS_AUTOMATICAS and a in categorias for o, a in renombradas.items()):
        raise ValueError("Los cambios de nombre guardados no son válidos.")

    # Los ajustes anteriores a los gastos fijos no traen "fijas": se usan los valores iniciales
    if "fijas" in datos:
        fijas = datos["fijas"]
        if (
            not isinstance(fijas, list)
            or not all(isinstance(c, str) and c in categorias for c in fijas)
            or len(set(fijas)) != len(fijas)
        ):
            raise ValueError("La lista de gastos fijos guardada no es válida.")
    else:
        fijas = [c for c in DEFAULT_FIJAS if c in categorias]

    # Las copias anteriores a las reglas por palabra clave no las traen
    reglas = datos.get("reglas", [])
    if not isinstance(reglas, list) or len(reglas) > MAX_REGLAS or not all(
        isinstance(r, dict) and isinstance(r.get("palabra"), str) and isinstance(r.get("categoria"), str)
        and MIN_LONG_PALABRA <= len(normalizar(r["palabra"])) <= MAX_LONG_PALABRA
        and r["categoria"] in categorias
        for r in reglas
    ):
        raise ValueError("Las reglas por palabra clave guardadas no son válidas.")

    resultado = {
        "categorias": list(categorias),
        "colores": {c: colores[c] for c in categorias if c in colores},
        "clasificaciones": dict(clasificaciones),
        "renombradas": dict(renombradas),
        "fijas": list(fijas),
        "reglas": [{"palabra": normalizar(r["palabra"]), "categoria": r["categoria"]} for r in reglas],
    }
    for c in categorias:
        if c not in resultado["colores"]:
            resultado["colores"][c] = _color_libre(resultado)
    # Categorías automáticas que faltan en los ajustes: sus movimientos van a "Otros", como al borrarlas
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


# ═══════════════════════════════════════════════
# HISTORIAL DE MOVIMIENTOS
# ═══════════════════════════════════════════════

def reducir_movimiento(t: dict) -> dict:
    """Se queda solo con los campos que usa la app (menos datos guardados, p. ej. sin IBAN ajenos)."""
    reducido = {
        clave: t[clave]
        for clave in ("credit_debit_indicator", "booking_date", "value_date", "transaction_date",
                      "remittance_information", "entry_reference", "status")
        if t.get(clave) is not None
    }
    importe = t.get("transaction_amount") or {}
    reducido["transaction_amount"] = {"amount": importe.get("amount"), "currency": importe.get("currency")}
    for parte in ("creditor", "debtor"):
        nombre = (t.get(parte) or {}).get("name")
        if nombre:
            reducido[parte] = {"name": nombre}
    return reducido


def _fecha_movimiento(t: dict) -> str:
    return t.get("booking_date") or t.get("value_date") or t.get("transaction_date") or ""


def combinar_historial(antiguos: list | None, nuevos: list, desde: str) -> list:
    """Historial acumulado: lo nuevo manda desde la fecha 'desde'; lo anterior se conserva.

    Así se recogen las correcciones del banco (pendientes que se contabilizan, cargos
    con fecha atrasada) sin duplicar movimientos. 'desde' es una fecha ISO (AAAA-MM-DD).
    """
    conservados = [t for t in (antiguos or []) if _fecha_movimiento(t) and _fecha_movimiento(t) < desde]
    return conservados + list(nuevos)


# ═══════════════════════════════════════════════
# COPIA DE SEGURIDAD (categorías + historial)
# ═══════════════════════════════════════════════
# Formato: {"version": 2, "ajustes": {...},
#           "cuentas": {uid: {"movimientos": [...], "desde": "AAAA-MM-DD", "obtenido": ISO}}}
# La versión 1 (copias antiguas) solo traía los ajustes, en el primer nivel.

MAX_BYTES_COPIA = 10_000_000
MAX_CUENTAS = 20
MAX_MOVIMIENTOS = 100_000
_FECHA_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}")


def exportar_copia(ajustes: dict, cuentas: dict) -> str:
    return json.dumps({"version": 2, "ajustes": ajustes, "cuentas": cuentas}, ensure_ascii=False)


def _validar_movimiento(t) -> dict:
    """Comprueba un movimiento de la copia y lo deja solo con los campos que usa la app."""
    if not isinstance(t, dict):
        raise ValueError
    float((t.get("transaction_amount") or {}).get("amount"))  # Lanza si no es un número
    fecha = _fecha_movimiento(t)
    if t.get("credit_debit_indicator") not in ("CRDT", "DBIT") or not (
        isinstance(fecha, str) and _FECHA_ISO.match(fecha) and len(fecha) <= 30
    ):
        raise ValueError
    for parte in ("creditor", "debtor"):
        nombre = (t.get(parte) or {}).get("name")
        if nombre is not None and not (isinstance(nombre, str) and len(nombre) <= MAX_LONG_DESCRIPCION):
            raise ValueError
    info = t.get("remittance_information")
    if info is not None and not (
        isinstance(info, list) and len(info) <= 10
        and all(isinstance(x, str) and len(x) <= MAX_LONG_DESCRIPCION for x in info)
    ):
        raise ValueError
    return reducir_movimiento(t)


def importar_copia(contenido: bytes) -> tuple:
    """Valida una copia subida por el usuario. Devuelve (ajustes, cuentas).

    Lanza ValueError con el motivo si no es válida: no se aplica nada a medias.
    """
    if len(contenido) > MAX_BYTES_COPIA:
        raise ValueError("El archivo es demasiado grande para ser una copia de Mis Gastos.")
    try:
        datos = json.loads(contenido.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("El archivo no es un JSON válido.")
    if not isinstance(datos, dict) or datos.get("version") not in (1, 2):
        raise ValueError("No parece una copia de Mis Gastos (versión desconocida).")

    if datos["version"] == 1:
        return validar_ajustes(datos), {}

    ajustes = validar_ajustes(datos.get("ajustes"))
    cuentas = datos.get("cuentas", {})
    if not isinstance(cuentas, dict) or len(cuentas) > MAX_CUENTAS:
        raise ValueError("Las cuentas de la copia no tienen el formato esperado.")
    if sum(len(c.get("movimientos") or []) for c in cuentas.values() if isinstance(c, dict)) > MAX_MOVIMIENTOS:
        raise ValueError("La copia tiene demasiados movimientos.")

    limpias = {}
    for uid, cuenta in cuentas.items():
        try:
            if not (isinstance(uid, str) and 0 < len(uid) <= 100 and isinstance(cuenta, dict)):
                raise ValueError
            movimientos = cuenta.get("movimientos") or []
            if not isinstance(movimientos, list):
                raise ValueError
            desde, obtenido = cuenta.get("desde"), cuenta.get("obtenido")
            if desde is not None and not (isinstance(desde, str) and _FECHA_ISO.match(desde)):
                raise ValueError
            if obtenido is not None:
                datetime.fromisoformat(obtenido)  # Lanza si no es una fecha válida
            limpias[uid] = {
                "movimientos": [_validar_movimiento(t) for t in movimientos],
                "desde": desde,
                "obtenido": obtenido,
            }
        except (ValueError, TypeError, AttributeError):
            raise ValueError("La copia contiene movimientos o cuentas no válidos.")
    return ajustes, limpias


def parse_eb_transactions(raw: list) -> pd.DataFrame:
    """Convierte la respuesta de Enable Banking al DataFrame interno."""
    rows = []
    categoria_de = {}  # Cada comercio se clasifica una sola vez, aunque tenga muchos movimientos
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

        if tipo == "gasto" and desc not in categoria_de:
            categoria_de[desc] = categorize(desc)
        rows.append({
            "fecha":       fecha_str,
            "monto":       monto,
            "descripcion": desc,
            "categoria":   categoria_de[desc] if tipo == "gasto" else "Ingreso",
            "recurrente":  False,
            "tipo":        tipo,
        })

    columnas = ["fecha", "monto", "descripcion", "categoria", "recurrente", "tipo", "mes"]
    if not rows:
        return pd.DataFrame(columns=columnas)

    df = pd.DataFrame(rows)
    # Todas las fechas de golpe (una a una era lo más lento). Solo el día, tal como lo escribe
    # el banco: así un movimiento a las 00:30 no cambia de mes por la zona horaria.
    # Las que no se entiendan se descartan.
    df["fecha"] = pd.to_datetime(df["fecha"].astype(str).str[:10], format="%Y-%m-%d", errors="coerce")
    df = df.dropna(subset=["fecha"])
    if df.empty:
        return pd.DataFrame(columns=columnas)
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
