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
            "state": "gastos_app",
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

    # Paginación
    while data.get("continuation_key"):
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
