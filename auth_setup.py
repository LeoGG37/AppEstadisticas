"""
Script de autorización única para conectar tu banco con Enable Banking.
Ejecútalo UNA VEZ en local. Guardará la sesión en secrets.toml.

Uso:
    python auth_setup.py

Requisitos previos:
    pip install PyJWT cryptography
"""

import sys
import time
import threading
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from pathlib import Path

# Añadir raíz del proyecto al path
sys.path.insert(0, str(Path(__file__).parent))

REDIRECT_PORT = 8501
REDIRECT_URL = f"http://localhost:{REDIRECT_PORT}"

auth_code = None
server_stop = threading.Event()


class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if "code" in params:
            auth_code = params["code"][0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(
                b"<html><body><h2>Autorizado.</h2>"
                b"<p>Puedes cerrar esta ventana y volver al terminal.</p></body></html>"
            )
            server_stop.set()
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Error: no se recibio codigo.")

    def log_message(self, format, *args):
        pass  # silenciar logs HTTP


def _run_server():
    server = HTTPServer(("localhost", REDIRECT_PORT), CallbackHandler)
    server.timeout = 1
    while not server_stop.is_set():
        server.handle_request()


def _load_secrets():
    secrets_path = Path(__file__).parent / ".streamlit" / "secrets.toml"
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib
        except ImportError:
            sys.exit("Instala tomli: pip install tomli  (necesario en Python < 3.11)")
    with open(secrets_path, "rb") as f:
        return tomllib.load(f), secrets_path


def _append_to_secrets(secrets_path: Path, session_id: str, account_uid: str):
    with open(secrets_path, "r", encoding="utf-8") as f:
        content = f.read()

    lines_to_add = (
        "\n# Sesión activa Enable Banking (válida ~90 días)\n"
        f'session_id = "{session_id}"\n'
        f'account_uid = "{account_uid}"\n'
    )

    # Reemplazar si ya existen, añadir si no
    import re
    if "session_id" in content:
        content = re.sub(r'session_id\s*=\s*"[^"]*"', f'session_id = "{session_id}"', content)
        content = re.sub(r'account_uid\s*=\s*"[^"]*"', f'account_uid = "{account_uid}"', content)
        with open(secrets_path, "w", encoding="utf-8") as f:
            f.write(content)
        print("secrets.toml actualizado.")
    else:
        with open(secrets_path, "a", encoding="utf-8") as f:
            f.write(lines_to_add)
        print("secrets.toml actualizado.")


def main():
    import enable_banking as eb

    secrets, secrets_path = _load_secrets()
    cfg = secrets.get("enable_banking", {})
    app_id = cfg.get("application_id")
    private_key = cfg.get("private_key")

    if not app_id or not private_key:
        sys.exit("No se encontraron credenciales en secrets.toml.")

    # 1. Listar bancos disponibles
    print("Buscando bancos disponibles en España...")
    try:
        aspsps = eb.list_aspsps(app_id, private_key, country="ES")
    except Exception as e:
        print(f"Aviso: no se pudo obtener la lista de bancos ({e}). Continuando...")
        aspsps = []

    # Determinar banco y país
    aspsp_name = None
    aspsp_country = "ES"

    if aspsps:
        print("\nBancos encontrados:")
        for a in aspsps:
            print(f"  · {a.get('name')}")

        santander = next(
            (a for a in aspsps if "santander" in a.get("name", "").lower()), None
        )
        mock = next(
            (a for a in aspsps if "mock" in a.get("name", "").lower()), None
        )

        if santander:
            aspsp_name = santander["name"]
            aspsp_country = "ES"
        elif mock:
            # El banco de prueba usa FI (Enable Banking es finlandés)
            aspsp_name = mock["name"]
            aspsp_country = "FI"
            print(f"\nSantander no disponible en Sandbox. Usando banco de prueba: {aspsp_name}")
        else:
            aspsp_name = aspsps[0]["name"]
            aspsp_country = "ES"
    else:
        aspsp_name = "Mock ASPSP"
        aspsp_country = "FI"

    print(f"Usando: {aspsp_name} ({aspsp_country})")

    # 2. Iniciar autorización
    print(f"\nIniciando sesión con {aspsp_name}...")
    try:
        auth_data = eb.start_auth(app_id, private_key, REDIRECT_URL, aspsp_name, aspsp_country)
    except Exception as e:
        sys.exit(f"Error al iniciar autorización: {e}")

    auth_url = auth_data["url"]

    # 3. Servidor local para capturar callback
    thread = threading.Thread(target=_run_server, daemon=True)
    thread.start()

    print("\nAbriendo el navegador para que te identifiques en el banco...")
    print(f"Si no se abre solo, copia esta URL:\n  {auth_url}\n")
    webbrowser.open(auth_url)

    # 4. Esperar callback (máx. 5 minutos)
    print("Esperando autorización del banco...")
    timeout = 600
    start = time.time()
    while auth_code is None and (time.time() - start) < timeout:
        time.sleep(0.5)

    if auth_code is None:
        sys.exit("Tiempo de espera agotado. Vuelve a intentarlo.")

    # 5. Intercambiar código por sesión
    print("Código recibido. Obteniendo sesión...")
    try:
        session_data = eb.exchange_code(app_id, private_key, auth_code)
    except Exception as e:
        sys.exit(f"Error al obtener sesión: {e}")

    session_id = session_data["session_id"]
    accounts = session_data.get("accounts", [])

    print(f"\nSesión activa: {session_id}")
    print(f"Cuentas autorizadas: {len(accounts)}")
    for acc in accounts:
        account_id = acc.get("account_id") or {}
        iban = account_id.get("iban", "") if isinstance(account_id, dict) else ""
        print(f"  · UID: {acc.get('uid')}  IBAN: {iban or '(sin IBAN)'}")

    if not accounts:
        sys.exit("No se encontraron cuentas en la sesión.")

    account_uid = accounts[0]["uid"]

    # 6. Guardar en secrets.toml
    _append_to_secrets(secrets_path, session_id, account_uid)

    print("\n¡Listo! La app ya puede acceder a tus transacciones del banco.")
    print("Recuerda también añadir session_id y account_uid a los secrets de Streamlit Cloud.")
    print(f"\n  session_id  = {session_id}")
    print(f"  account_uid = {account_uid}")


if __name__ == "__main__":
    main()
