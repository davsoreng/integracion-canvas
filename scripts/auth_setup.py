"""
Script de autorización OAuth 2.0 para el Asistente Académico (Canvas -> Drive/Calendar).

Este script se ejecuta localmente UNA SOLA VEZ para:
1. Abrir tu navegador web y permitir el acceso a tu cuenta personal de Google (Drive y Calendar).
2. Generar un `refresh_token` permanente (que no expira).
3. Guardar automáticamente las credenciales en tu archivo `.env`.
"""

import os
import sys
import urllib.parse
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import dotenv

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/calendar"
]

ENV_FILE_PATH = Path(__file__).resolve().parent.parent / ".env"
received_code = None


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Manejador HTTP para capturar la redirección de Google con el código de autorización."""

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        global received_code
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if "code" in params:
            received_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            html = """
            <!DOCTYPE html>
            <html lang="es">
            <head><meta charset="utf-8"><title>Autorización Exitosa</title></head>
            <body style="font-family: -apple-system, sans-serif; text-align: center; padding: 50px; background: #f8f9fa;">
                <div style="background: white; max-width: 450px; margin: auto; padding: 30px; border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.1);">
                    <h1 style="color: #2e7d32; margin-top: 0;">✅ ¡Autorización Exitosa!</h1>
                    <p style="color: #555; line-height: 1.5;">Tu cuenta de Google ha sido conectada correctamente con el Asistente Académico.</p>
                    <p style="color: #888; font-size: 0.9em;">Ya puedes cerrar esta pestaña y volver al chat/terminal.</p>
                </div>
            </body>
            </html>
            """
            self.wfile.write(html.encode("utf-8"))
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Error: No authorization code received.")


def update_env_variable(key: str, value: str):
    """Actualiza o añade una variable en el archivo .env."""
    if not ENV_FILE_PATH.exists():
        ENV_FILE_PATH.touch()
    dotenv.set_key(str(ENV_FILE_PATH), key, value)
    print(f"  [OK] Guardado en .env: {key}=***")


def main():
    global received_code
    print("=" * 60)
    print("   ASISTENTE ACADÉMICO: AUTORIZACIÓN GOOGLE OAUTH 2.0")
    print("=" * 60)
    print("\nEste proceso conectará tu cuenta personal de Google (2 TB Google One)")
    print("con el asistente para sincronizar Drive y Calendar.\n")

    # Cargar .env existente si existe
    if ENV_FILE_PATH.exists():
        dotenv.load_dotenv(str(ENV_FILE_PATH))

    client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()

    if not client_id or not client_secret:
        print("[ERROR] GOOGLE_CLIENT_ID y GOOGLE_CLIENT_SECRET son obligatorios en .env.")
        sys.exit(1)

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost:8085/"]
        }
    }

    local_port = 8085
    redirect_uri = f"http://localhost:{local_port}/"

    flow = InstalledAppFlow.from_client_config(
        client_config,
        scopes=SCOPES
    )
    flow.redirect_uri = redirect_uri

    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent"
    )

    # Guardar en archivo para fácil lectura
    url_file = Path(__file__).resolve().parent.parent / "AUTH_URL.txt"
    with open(url_file, "w", encoding="utf-8") as f:
        f.write(auth_url)

    print("\n" + "=" * 60)
    print("🔗 ENLACE DE AUTORIZACIÓN:")
    print(auth_url)
    print("=" * 60)
    print("\n[INFO] Servidor local escuchando en http://localhost:8085/ esperando respuesta...")
    sys.stdout.flush()

    # Intentar abrir el navegador en la sesión de Windows
    try:
        os.system(f'start "" "{auth_url}"')
    except Exception:
        pass

    try:
        # Levantar el servidor HTTP para recibir la redirección
        server = HTTPServer(("localhost", local_port), OAuthCallbackHandler)
        server.handle_request()  # Atiende exactamente una petición y continúa

        if not received_code:
            print("\n[ERROR] No se recibió ningún código de autorización.")
            sys.exit(1)

        print("\n[INFO] Código de autorización recibido. Solicitando refresh token...")
        sys.stdout.flush()

        flow.fetch_token(code=received_code)
        credentials = flow.credentials
        refresh_token = credentials.refresh_token

        if not refresh_token:
            print("\n[ALERTA] No se recibió un refresh_token nuevo.")
            print("Si ya habías autorizado antes, revoca el acceso en https://myaccount.google.com/permissions")
            sys.stdout.flush()
            return

        print("\n[ÉXITO] ¡Refresh Token obtenido correctamente!")
        update_env_variable("GOOGLE_REFRESH_TOKEN", refresh_token)
        sys.stdout.flush()

        # Limpiar archivo temporal si existe
        if url_file.exists():
            url_file.unlink(missing_ok=True)

        # Prueba de conexión rápida
        print("\n[INFO] Verificando acceso a Google Drive y Google Calendar...")
        sys.stdout.flush()
        drive_service = build("drive", "v3", credentials=credentials)
        about = drive_service.about().get(fields="user, storageQuota").execute()
        user_name = about.get("user", {}).get("displayName", "Usuario")
        user_email = about.get("user", {}).get("emailAddress", "")
        print(f"  [OK] Conectado a Google Drive como: {user_name} ({user_email})")

        calendar_service = build("calendar", "v3", credentials=credentials)
        cal_list = calendar_service.calendarList().list(maxResults=5).execute()
        print(f"  [OK] Conectado a Google Calendar ({len(cal_list.get('items', []))} calendarios encontrados)")

        print("\n" + "=" * 60)
        print("  ¡TODO LISTO! Tus credenciales están guardadas en .env")
        print("=" * 60)
        sys.stdout.flush()

    except Exception as e:
        print(f"\n[ERROR] Ocurrió un fallo durante la autorización: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
