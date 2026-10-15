"""
Script de autorización OAuth 2.0 para el Asistente Académico (Canvas -> Drive/Calendar).

Este script se ejecuta localmente UNA SOLA VEZ para:
1. Abrir tu navegador web y permitir el acceso a tu cuenta personal de Google (Drive y Calendar).
2. Generar un `refresh_token` permanente (que no expira).
3. Guardar automáticamente las credenciales en tu archivo `.env`.
"""

import os
import sys
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import dotenv

# Scopes necesarios para gestionar archivos en Drive y eventos en Calendar
SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/calendar"
]

ENV_FILE_PATH = Path(__file__).resolve().parent.parent / ".env"


def update_env_variable(key: str, value: str):
    """Actualiza o añade una variable en el archivo .env."""
    if not ENV_FILE_PATH.exists():
        ENV_FILE_PATH.touch()
    
    dotenv.set_key(str(ENV_FILE_PATH), key, value)
    print(f"  [OK] Guardado en .env: {key}=***")


def main():
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
        print("No se encontraron GOOGLE_CLIENT_ID y GOOGLE_CLIENT_SECRET en .env.")
        print("Por favor, introduce los datos creados en Google Cloud Console:")
        client_id = input("Introduce tu GOOGLE_CLIENT_ID: ").strip()
        client_secret = input("Introduce tu GOOGLE_CLIENT_SECRET: ").strip()

        if not client_id or not client_secret:
            print("[ERROR] El Client ID y Client Secret son obligatorios.")
            sys.exit(1)

        update_env_variable("GOOGLE_CLIENT_ID", client_id)
        update_env_variable("GOOGLE_CLIENT_SECRET", client_secret)

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost", "http://127.0.0.1"]
        }
    }

    print("\n[INFO] Abriendo navegador para autorizar la aplicación...")
    print("Si aparece una pantalla de advertencia 'Google no ha verificado esta app',")
    print("haz clic en 'Avanzado' (o 'Configuración avanzada') y luego en 'Ir a Asistente Canvas (no seguro)'.")
    print("Esto es normal ya que la app está en modo Testing para tu propio uso personal.\n")

    try:
        flow = InstalledAppFlow.from_client_config(
            client_config,
            scopes=SCOPES
        )
        credentials = flow.run_local_server(
            port=0,
            access_type="offline",
            prompt="consent",
            success_message="¡Autorización completada con éxito! Ya puedes cerrar esta ventana."
        )

        refresh_token = credentials.refresh_token

        if not refresh_token:
            print("\n[ALERTA] No se recibió un refresh_token nuevo.")
            print("Si ya habías autorizado antes, revoca el acceso en https://myaccount.google.com/permissions")
            print("o asegúrate de que prompt='consent' esté activo.")
            return

        print("\n[ÉXITO] ¡Refresh Token obtenido correctamente!")
        update_env_variable("GOOGLE_REFRESH_TOKEN", refresh_token)

        # Prueba de conexión rápida
        print("\n[INFO] Verificando acceso a Google Drive y Google Calendar...")
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

    except Exception as e:
        print(f"\n[ERROR] Ocurrió un fallo durante la autorización: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
