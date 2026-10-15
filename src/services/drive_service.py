import io
import re
from typing import Optional, Dict, Tuple
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from config.settings import settings
from src.core.logger import logger
from src.models.schemas import CanvasFile


class DriveService:
    """Cliente para interactuar con Google Drive API asegurando idempotencia y sin duplicados."""

    def __init__(self, client_id: Optional[str] = None, client_secret: Optional[str] = None, refresh_token: Optional[str] = None):
        c_id = client_id or settings.GOOGLE_CLIENT_ID
        c_secret = client_secret or settings.GOOGLE_CLIENT_SECRET
        r_token = refresh_token or settings.GOOGLE_REFRESH_TOKEN

        if not c_id or not c_secret or not r_token:
            raise ValueError("Credenciales de Google OAuth incompletas (se requiere Client ID, Secret y Refresh Token).")

        self.creds = Credentials(
            token=None,
            refresh_token=r_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=c_id,
            client_secret=c_secret,
            scopes=["https://www.googleapis.com/auth/drive"]
        )
        self.service = build("drive", "v3", credentials=self.creds, cache_discovery=False)
        self._folder_cache: Dict[str, str] = {}
        self.root_folder_id: Optional[str] = None

    @staticmethod
    def sanitize_name(name: str) -> str:
        """Limpia caracteres inválidos para nombres de carpetas y archivos en Drive."""
        return re.sub(r'[\\/*?:"<>|]', "_", name).strip()

    def get_or_create_folder(self, folder_name: str, parent_id: Optional[str] = None) -> str:
        """Busca una carpeta por nombre y parent_id. Si no existe, la crea."""
        clean_name = self.sanitize_name(folder_name)
        cache_key = f"{parent_id or 'root'}::{clean_name}"
        if cache_key in self._folder_cache:
            return self._folder_cache[cache_key]

        query = f"mimeType = 'application/vnd.google-apps.folder' and name = '{clean_name}' and trashed = false"
        if parent_id:
            query += f" and '{parent_id}' in parents"
        else:
            query += " and 'root' in parents"

        response = self.service.files().list(
            q=query,
            spaces="drive",
            fields="files(id, name)",
            pageSize=1
        ).execute()

        files = response.get("files", [])
        if files:
            folder_id = files[0]["id"]
        else:
            folder_metadata = {
                "name": clean_name,
                "mimeType": "application/vnd.google-apps.folder",
            }
            if parent_id:
                folder_metadata["parents"] = [parent_id]

            created = self.service.files().create(
                body=folder_metadata,
                fields="id"
            ).execute()
            folder_id = created["id"]
            logger.info(f"Carpeta creada en Drive: '{clean_name}' (ID: {folder_id})")

        self._folder_cache[cache_key] = folder_id
        return folder_id

    def get_root_academic_folder_id(self) -> str:
        """Obtiene o crea la carpeta raíz principal para todo el material de Canvas."""
        if not self.root_folder_id:
            self.root_folder_id = self.get_or_create_folder(settings.DRIVE_ROOT_FOLDER_NAME)
        return self.root_folder_id

    def get_course_folder_id(self, course_name: str) -> str:
        """Obtiene o crea la carpeta específica para una asignatura dentro de la carpeta raíz."""
        root_id = self.get_academic_folder_id()
        return self.get_or_create_folder(course_name, parent_id=root_id)

    def get_academic_folder_id(self) -> str:
        return self.get_root_academic_folder_id()

    def find_existing_file(self, folder_id: str, canvas_file: CanvasFile) -> Optional[dict]:
        """Busca si el archivo ya existe en la carpeta mediante metadatos appProperties o por nombre."""
        # 1. Búsqueda precisa por ID de archivo en Canvas usando appProperties
        query_prop = (
            f"'{folder_id}' in parents and trashed = false and "
            f"appProperties has {{ key='canvas_file_id' and value='{canvas_file.id}' }}"
        )
        res = self.service.files().list(
            q=query_prop,
            fields="files(id, name, appProperties, modifiedTime)",
            pageSize=1
        ).execute()

        files = res.get("files", [])
        if files:
            return files[0]

        # 2. Búsqueda por nombre de archivo como fallback
        safe_name = self.sanitize_name(canvas_file.display_name)
        escaped_name = safe_name.replace("'", "\\'")
        query_name = f"'{folder_id}' in parents and name = '{escaped_name}' and trashed = false"
        res_name = self.service.files().list(
            q=query_name,
            fields="files(id, name, appProperties, modifiedTime)",
            pageSize=1
        ).execute()

        files_name = res_name.get("files", [])
        if files_name:
            return files_name[0]

        return None

    def upload_file_if_new(self, folder_id: str, canvas_file: CanvasFile, file_stream: io.BytesIO) -> Tuple[str, str]:
        """
        Sube un archivo a Google Drive si no existe o si ha sido actualizado en Canvas.
        Retorna (estado, drive_file_id) donde estado es 'skipped', 'uploaded' o 'updated'.
        """
        existing = self.find_existing_file(folder_id, canvas_file)
        clean_name = self.sanitize_name(canvas_file.display_name)
        file_mime = canvas_file.content_type or "application/octet-stream"

        app_props = {
            "canvas_file_id": str(canvas_file.id),
            "canvas_updated_at": str(canvas_file.updated_at or "")
        }

        media = MediaIoBaseUpload(
            file_stream,
            mimetype=file_mime,
            resumable=True
        )

        if existing:
            existing_id = existing["id"]
            existing_props = existing.get("appProperties") or {}
            existing_updated_at = existing_props.get("canvas_updated_at", "")

            # Si la fecha de modificación coincide, no duplicar
            if existing_updated_at and existing_updated_at == str(canvas_file.updated_at or ""):
                return "skipped", existing_id

            # Si cambió en Canvas, actualizar el contenido del archivo existente
            updated_file = self.service.files().update(
                fileId=existing_id,
                body={"appProperties": app_props},
                media_body=media,
                fields="id"
            ).execute()
            logger.info(f"Archivo actualizado en Drive: '{clean_name}' (ID: {updated_file['id']})")
            return "updated", updated_file["id"]

        # Si no existe, crear un nuevo archivo con sus metadatos
        file_metadata = {
            "name": clean_name,
            "parents": [folder_id],
            "appProperties": app_props
        }

        created_file = self.service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id"
        ).execute()

        logger.info(f"Archivo subido a Drive: '{clean_name}' (ID: {created_file['id']})")
        return "uploaded", created_file["id"]
