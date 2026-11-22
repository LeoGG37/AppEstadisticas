"""Módulo de integración con Enable Banking API."""

import time
from datetime import datetime, timedelta, timezone
import requests

try:
    import jwt
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    _DEPS_OK = True
except ImportError:
    _DEPS_OK = False

API_URL = "https://api.enablebanking.com"


def _make_jwt(application_id: str, private_key_pem: str) -> str:
    """Genera un JWT firmado con la clave RSA para autenticar peticiones."""
    private_key = load_pem_private_key(private_key_pem.encode(), password=None)
    now = int(time.time())
    return jwt.encode(
        {
            "iss": "enablebanking.com",
            "aud": "api.enablebanking.com",
            "iat": now,
            "exp": now + 3600,
        },
        private_key,
        algorithm="RS256",
        headers={"typ": "JWT", "alg": "RS256", "kid": application_id},
    )


def list_aspsps(application_id: str, private_key_pem: str, country: str = "ES") -> list:
    """Devuelve la lista de bancos disponibles en un país."""
    token = _make_jwt(application_id, private_key_pem)
    resp = requests.get(
        f"{API_URL}/aspsps",
        headers={"Authorization": f"Bearer {token}"},
        params={"country": country},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("aspsps", [])


def start_auth(
    application_id: str,
    private_key_pem: str,
    redirect_url: str,
    aspsp_name: str,
    aspsp_country: str = "ES",
    state: str = "gastos_app",
) -> dict:
    """Inicia el flujo de autorización con el banco. Devuelve url y authorization_id."""
    token = _make_jwt(application_id, private_key_pem)
    resp = requests.post(
        f"{API_URL}/auth",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={
            "access": {
                "valid_until": (datetime.now(timezone.utc) + timedelta(days=179)).strftime(
                    "%Y-%m-%dT%H:%M:%S.000000+00:00"
                ),
                "balances": True,
                "transactions": True,
            },
            "aspsp": {"name": aspsp_name, "country": aspsp_country},
            "state": state,
            "redirect_url": redirect_url,
            "psu_type": "personal",
        },
        timeout=15,
    )
    if not resp.ok:
        raise Exception(f"{resp.status_code}: {resp.text}")
    return resp.json()


def exchange_code(application_id: str, private_key_pem: str, code: str) -> dict:
    """Intercambia el código de autorización por una sesión activa."""
    token = _make_jwt(application_id, private_key_pem)
    resp = requests.post(
        f"{API_URL}/sessions",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"code": code},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def get_session_status(application_id: str, private_key_pem: str, session_id: str) -> dict:
    """Consulta el estado de una sesión autorizada."""
    token = _make_jwt(application_id, private_key_pem)
    resp = requests.get(
        f"{API_URL}/sessions/{session_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def get_transactions(
    application_id: str,
    private_key_pem: str,
    account_uid: str,
    date_from: str = None,
) -> list:
    """Descarga todas las transacciones de una cuenta (con paginación)."""
    token = _make_jwt(application_id, private_key_pem)
    params = {}
    if date_from:
        params["date_from"] = date_from

    resp = requests.get(
        f"{API_URL}/accounts/{account_uid}/transactions",
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    all_txns = list(data.get("transactions", []))

    # Paginación (máximo 50 páginas para evitar bucles infinitos)
    page = 0
    while data.get("continuation_key") and page < 50:
        page += 1
        params["continuation_key"] = data["continuation_key"]
        resp = requests.get(
            f"{API_URL}/accounts/{account_uid}/transactions",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        all_txns.extend(data.get("transactions", []))

    return all_txns


def get_all_transactions(
    application_id: str,
    private_key_pem: str,
    account_uid: str,
    max_peticiones: int = 200,
) -> tuple:
    """Pide todo el historial que permita el banco (strategy=longest).

    Solo funciona bien en la ~1 h siguiente a autorizar. Mientras busca el inicio,
    la API puede devolver páginas vacías con continuation_key: hay que seguir pidiendo.
    Devuelve (movimientos, peticiones, problema): problema es None si terminó bien,
    "tope" si se alcanzó max_peticiones, o la excepción si falló a mitad. En los dos
    últimos casos los movimientos son los descargados hasta ese momento.
    """
    token = _make_jwt(application_id, private_key_pem)
    params = {"strategy": "longest"}
    movimientos, peticiones = [], 0
    try:
        while True:
            resp = requests.get(
                f"{API_URL}/accounts/{account_uid}/transactions",
                headers={"Authorization": f"Bearer {token}"},
                params=params,
                timeout=60,
            )
            peticiones += 1
            resp.raise_for_status()
            data = resp.json()
            movimientos.extend(data.get("transactions", []))
            clave = data.get("continuation_key")
            if not clave:
                return movimientos, peticiones, None
            if peticiones >= max_peticiones:
                return movimientos, peticiones, "tope"
            params["continuation_key"] = clave
    except Exception as e:
        return movimientos, peticiones, e
