import io
import re
from typing import Optional, Dict, Tuple, List
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from config.settings import settings
from src.core.logger import logger
from src.models.schemas import CanvasFile


class DriveService:
    """Cliente para interactuar con Google Drive API respetando la estructura del usuario y asegurando idempotencia."""

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

    def get_engineering_root_id(self) -> str:
        """Obtiene el ID de la carpeta INGENIERIA (por ID directo o buscando por nombre)."""
        if settings.DRIVE_ROOT_FOLDER_ID:
            return settings.DRIVE_ROOT_FOLDER_ID

        if not self.root_folder_id:
            query = (
                f"mimeType = 'application/vnd.google-apps.folder' and "
                f"name = '{settings.DRIVE_ROOT_FOLDER_NAME}' and trashed = false"
            )
            res = self.service.files().list(q=query, fields="files(id, name)", pageSize=1).execute()
            files = res.get("files", [])
            if files:
                self.root_folder_id = files[0]["id"]
            else:
                raise ValueError(f"No se encontró la carpeta raíz '{settings.DRIVE_ROOT_FOLDER_NAME}' en Google Drive.")
        return self.root_folder_id

    def discover_existing_subjects(self) -> Dict[str, dict]:
        """
        Escanea la carpeta INGENIERIA explorando sus subcarpetas de cursos (PRIMER, SEGON, etc.)
        y devuelve las asignaturas que el alumno HA CREADO.
        Retorna: {nombre_asignatura: {"id": folder_id, "year": year_name, "year_id": year_id}}
        """
        eng_id = self.get_engineering_root_id()
        query_years = f"'{eng_id}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        years_res = self.service.files().list(q=query_years, fields="files(id, name)", pageSize=50).execute()
        years = years_res.get("files", [])

        subjects: Dict[str, dict] = {}
        for y in years:
            year_id = y["id"]
            year_name = y["name"]
            query_subs = f"'{year_id}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
            subs_res = self.service.files().list(q=query_subs, fields="files(id, name)", pageSize=50).execute()
            for s in subs_res.get("files", []):
                # Ignorar carpetas ocultas o de configuración como .vscode
                if s["name"].startswith("."):
                    continue
                subjects[s["name"]] = {
                    "id": s["id"],
                    "year": year_name,
                    "year_id": year_id
                }

        logger.info(f"Se han descubierto {len(subjects)} asignaturas creadas por el usuario en Drive: {list(subjects.keys())}")
        return subjects

    def get_subject_subfolders(self, subject_folder_id: str) -> Dict[str, str]:
        """
        Obtiene el mapa de subcarpetas (temas y subdivisiones) existentes dentro de una asignatura.
        Retorna: {ruta_relativa: folder_id}, ej: {"T1": "...", "T1 - MATRIUS.../PIZZARRES": "..."}
        """
        subfolders: Dict[str, str] = {}

        def _scan(folder_id: str, current_path: str = "", depth: int = 0):
            if depth > 2:  # Limitar profundidad a 2 niveles
                return
            q = f"'{folder_id}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
            res = self.service.files().list(q=q, fields="files(id, name)", pageSize=50).execute()
            for f in res.get("files", []):
                if f["name"].startswith("."):
                    continue
                rel_path = f"{current_path}/{f['name']}".strip("/")
                subfolders[rel_path] = f["id"]
                _scan(f["id"], rel_path, depth + 1)

        _scan(subject_folder_id)
        return subfolders

    def find_existing_file_by_id(self, canvas_file_id: int) -> Optional[dict]:
        """Busca si el archivo de Canvas ya existe en Google Drive mediante metadatos appProperties."""
        query = f"trashed = false and appProperties has {{ key='canvas_file_id' and value='{canvas_file_id}' }}"
        res = self.service.files().list(
            q=query,
            fields="files(id, name, parents, appProperties, modifiedTime)",
            pageSize=1
        ).execute()

        files = res.get("files", [])
        return files[0] if files else None

    def find_existing_file_in_folder(self, folder_id: str, file_name: str) -> Optional[dict]:
        """Busca un archivo por nombre exacto en una carpeta específica como comprobación secundaria."""
        safe_name = self.sanitize_name(file_name).replace("'", "\\'")
        query = f"'{folder_id}' in parents and name = '{safe_name}' and trashed = false"
        res = self.service.files().list(
            q=query,
            fields="files(id, name, appProperties, modifiedTime)",
            pageSize=1
        ).execute()
        files = res.get("files", [])
        return files[0] if files else None

    def upload_file_if_new(self, target_folder_id: str, canvas_file: CanvasFile, file_stream: io.BytesIO) -> Tuple[str, str]:
        """
        Sube o actualiza un archivo en la carpeta destino designada respetando la idempotencia.
        NUNCA borra ni modifica ningún documento creado por el alumno.
        Retorna (estado, file_id) con estado en ('skipped', 'uploaded', 'updated').
        """
        # 1. Comprobar primero si ya existe por ID de Canvas
        existing = self.find_existing_file_by_id(canvas_file.id)

        # 2. Si no existe por ID, comprobar por nombre en la carpeta de destino
        if not existing:
            existing = self.find_existing_file_in_folder(target_folder_id, canvas_file.display_name)

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

            # Si la fecha de Canvas no ha cambiado, no duplicar ni re-subir
            if existing_updated_at and existing_updated_at == str(canvas_file.updated_at or ""):
                return "skipped", existing_id

            # Si el profesor actualizó el archivo en Canvas, actualizar el contenido manteniendo el ID
            updated_file = self.service.files().update(
                fileId=existing_id,
                body={"appProperties": app_props},
                media_body=media,
                fields="id"
            ).execute()
            logger.info(f"Archivo actualizado en Drive: '{clean_name}' (ID: {updated_file['id']})")
            return "updated", updated_file["id"]

        # Si es un archivo nuevo, subir a la carpeta destino
        file_metadata = {
            "name": clean_name,
            "parents": [target_folder_id],
            "appProperties": app_props
        }

        created_file = self.service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id"
        ).execute()

        logger.info(f"Archivo subido a Drive: '{clean_name}' (ID: {created_file['id']})")
        return "uploaded", created_file["id"]
