import io
import json
import os
import re
import unicodedata
from typing import Optional, Dict, Tuple, List
from google.oauth2.credentials import Credentials as OAuthCredentials
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from config.settings import settings
from src.core.logger import logger
from src.models.schemas import CanvasFile


def build_google_credentials(scopes: List[str]):
    """
    Construye las credenciales de Google priorizando Cuenta de Servicio (Service Account)
    para operación desatendida bajo el principio de mínimo privilegio, con soporte
    alternativo para OAuth 2.0 (Refresh Token).
    """
    sa_raw = settings.GOOGLE_SERVICE_ACCOUNT_JSON
    if sa_raw and sa_raw.strip():
        sa_raw = sa_raw.strip()
        try:
            if os.path.isfile(sa_raw):
                return service_account.Credentials.from_service_account_file(sa_raw, scopes=scopes)
            sa_info = json.loads(sa_raw)
            return service_account.Credentials.from_service_account_info(sa_info, scopes=scopes)
        except Exception as e:
            raise ValueError(f"No se pudo cargar GOOGLE_SERVICE_ACCOUNT_JSON: formato JSON o ruta inválida ({type(e).__name__}).")

    c_id = settings.GOOGLE_CLIENT_ID
    c_secret = settings.GOOGLE_CLIENT_SECRET
    r_token = settings.GOOGLE_REFRESH_TOKEN

    if c_id and c_secret and r_token:
        return OAuthCredentials(
            token=None,
            refresh_token=r_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=c_id,
            client_secret=c_secret,
            scopes=scopes
        )

    raise ValueError(
        "Credenciales de Google no configuradas. Defina GOOGLE_SERVICE_ACCOUNT_JSON "
        "(recomendado) o bien GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET y GOOGLE_REFRESH_TOKEN."
    )


class DriveService:
    """Cliente para interactuar con Google Drive API respetando la estructura del usuario y asegurando idempotencia."""

    def __init__(self, client_id: Optional[str] = None, client_secret: Optional[str] = None, refresh_token: Optional[str] = None):
        scopes = ["https://www.googleapis.com/auth/drive"]
        if client_id and client_secret and refresh_token:
            self.creds = OAuthCredentials(
                token=None,
                refresh_token=refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=client_id,
                client_secret=client_secret,
                scopes=scopes
            )
        else:
            self.creds = build_google_credentials(scopes)

        self.service = build("drive", "v3", credentials=self.creds, cache_discovery=False)
        self._folder_cache: Dict[str, str] = {}
        self.root_folder_id: Optional[str] = None

    @staticmethod
    def sanitize_name(name: str) -> str:
        """Limpia caracteres inválidos para nombres de carpetas y archivos en Drive."""
        return re.sub(r'[\\/*?:"<>|]', "_", name).strip()

    @classmethod
    def normalize_filename(cls, name: str) -> str:
        """
        Normaliza un nombre de archivo para comparación insensible a mayúsculas, acentos,
        espacios múltiples o caracteres sanitizados, conservando la extensión.
        """
        if not name:
            return ""
        clean = cls.sanitize_name(name).lower()
        clean = unicodedata.normalize("NFKD", clean).encode("ASCII", "ignore").decode("utf-8")
        base, ext = os.path.splitext(clean)
        base_norm = re.sub(r"[^a-z0-9]+", " ", base).strip()
        ext_norm = re.sub(r"[^a-z0-9.]+", "", ext).lower()
        return f"{base_norm}{ext_norm}"

    def _list_files_paginated(self, query: str, fields: str = "files(id, name)", page_size: int = 100) -> List[dict]:
        """Ejecuta una consulta paginada en Drive compatible tanto con Mi Unidad como con carpetas compartidas."""
        results: List[dict] = []
        page_token: Optional[str] = None
        full_fields = f"nextPageToken, {fields}" if "nextPageToken" not in fields else fields

        while True:
            res = self.service.files().list(
                q=query,
                fields=full_fields,
                pageSize=page_size,
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True
            ).execute()
            results.extend(res.get("files", []))
            page_token = res.get("nextPageToken")
            if not page_token:
                break
        return results

    def get_engineering_root_id(self) -> str:
        """Obtiene el ID de la carpeta raíz de estudio (por ID directo o buscando por nombre)."""
        if settings.DRIVE_ROOT_FOLDER_ID:
            return settings.DRIVE_ROOT_FOLDER_ID

        if not self.root_folder_id:
            safe_root = settings.DRIVE_ROOT_FOLDER_NAME.replace("'", "\\'")
            query = (
                f"mimeType = 'application/vnd.google-apps.folder' and "
                f"name = '{safe_root}' and trashed = false"
            )
            files = self._list_files_paginated(query, fields="files(id, name)", page_size=10)
            if files:
                self.root_folder_id = files[0]["id"]
            else:
                raise ValueError(
                    f"No se encontró la carpeta raíz '{settings.DRIVE_ROOT_FOLDER_NAME}' en Google Drive "
                    "(verifique que exista y esté compartida con la cuenta de servicio si usa Service Account)."
                )
        return self.root_folder_id

    def discover_existing_subjects(self) -> Dict[str, dict]:
        """
        Escanea la carpeta raíz de estudio explorando tanto carpetas de cursos/años (PRIMER, SEGON, etc.)
        como asignaturas directas que el alumno haya creado.
        Retorna: {nombre_asignatura: {"id": folder_id, "year": year_name, "year_id": year_id}}
        """
        eng_id = self.get_engineering_root_id()
        query_top = f"'{eng_id}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        top_folders = self._list_files_paginated(query_top, fields="files(id, name)")

        subjects: Dict[str, dict] = {}
        for y in top_folders:
            if y["name"].startswith("."):
                continue
            year_id = y["id"]
            year_name = y["name"]
            query_subs = f"'{year_id}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
            sub_folders = self._list_files_paginated(query_subs, fields="files(id, name)")
            for s in sub_folders:
                if s["name"].startswith("."):
                    continue
                subjects[s["name"]] = {
                    "id": s["id"],
                    "year": year_name,
                    "year_id": year_id
                }

        logger.info(f"Se han descubierto {len(subjects)} asignaturas creadas por el usuario en Drive.")
        return subjects

    def get_subject_subfolders(self, subject_folder_id: str, max_depth: int = 6) -> Dict[str, str]:
        """
        Obtiene recursivamente el mapa de TODAS las subcarpetas existentes dentro de una asignatura.
        Retorna: {ruta_relativa: folder_id}, ej: {"T1": "...", "T1/PIZZARRES": "..."}
        """
        subfolders: Dict[str, str] = {}

        def _scan(folder_id: str, current_path: str = "", depth: int = 0):
            if depth > max_depth:
                return
            q = f"'{folder_id}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
            found_folders = self._list_files_paginated(q, fields="files(id, name)")
            for f in found_folders:
                if f["name"].startswith("."):
                    continue
                rel_path = f"{current_path}/{f['name']}".strip("/")
                subfolders[rel_path] = f["id"]
                _scan(f["id"], rel_path, depth + 1)

        _scan(subject_folder_id)
        return subfolders

    def get_subject_files_index(self, subject_folder_id: str, subfolders_map: Dict[str, str]) -> Dict[str, Dict[str, dict]]:
        """
        Escanea todos los archivos existentes dentro de una asignatura —tanto en su propia raíz
        como en cualquiera de sus subcarpetas— y construye un índice rápido por ID de Canvas y por nombre.
        """
        all_folder_ids = [subject_folder_id] + list(subfolders_map.values())
        # Invertir mapa para conocer la ubicación legible de cada carpeta
        id_to_relpath = {subject_folder_id: "(raíz)"}
        for rel_path, fid in subfolders_map.items():
            id_to_relpath[fid] = rel_path

        by_canvas_id: Dict[str, dict] = {}
        by_exact_name: Dict[str, dict] = {}
        by_norm_name: Dict[str, dict] = {}

        # Consultar en bloques de 20 carpetas para minimizar llamadas HTTP a la API de Drive
        chunk_size = 20
        for i in range(0, len(all_folder_ids), chunk_size):
            chunk = all_folder_ids[i:i + chunk_size]
            parents_clause = " or ".join(f"'{fid}' in parents" for fid in chunk)
            query = (
                f"({parents_clause}) and "
                f"mimeType != 'application/vnd.google-apps.folder' and "
                f"trashed = false"
            )
            files = self._list_files_paginated(
                query,
                fields="files(id, name, parents, appProperties, modifiedTime, size)"
            )
            for f in files:
                fname = f.get("name", "")
                if not fname or fname.startswith("."):
                    continue

                parents = f.get("parents") or []
                f["folder_relpath"] = id_to_relpath.get(parents[0], "(subcarpeta)") if parents else "(raíz)"

                props = f.get("appProperties") or {}
                cid = props.get("canvas_file_id")
                if cid:
                    by_canvas_id[str(cid)] = f

                exact_key = self.sanitize_name(fname).lower()
                by_exact_name[exact_key] = f
                by_exact_name[fname.strip().lower()] = f

                norm_key = self.normalize_filename(fname)
                if norm_key:
                    by_norm_name[norm_key] = f

        return {
            "by_canvas_id": by_canvas_id,
            "by_exact_name": by_exact_name,
            "by_norm_name": by_norm_name,
        }

    def find_existing_file_in_subject(
        self,
        canvas_file: CanvasFile,
        subject_files_index: Optional[Dict[str, Dict[str, dict]]] = None
    ) -> Optional[dict]:
        """
        Busca si un archivo de Canvas ya existe dentro de la carpeta de la asignatura
        (tanto en su raíz como en cualquiera de sus subcarpetas):
        1. Por canvas_file_id en el índice de la asignatura.
        2. Por nombre exacto o sanitizado en cualquier carpeta de la asignatura.
        3. Por nombre normalizado (sin acentos/espacios extra) en cualquier carpeta de la asignatura.
        4. Fallback global por canvas_file_id en Drive.
        """
        if subject_files_index:
            cid_str = str(canvas_file.id)
            by_id = subject_files_index.get("by_canvas_id", {})
            if cid_str in by_id:
                return by_id[cid_str]

            by_exact = subject_files_index.get("by_exact_name", {})
            by_norm = subject_files_index.get("by_norm_name", {})

            candidate_names = [canvas_file.display_name, canvas_file.filename]
            for cand in candidate_names:
                if not cand:
                    continue
                sanitized_key = self.sanitize_name(cand).lower()
                raw_key = cand.strip().lower()
                if sanitized_key in by_exact:
                    return by_exact[sanitized_key]
                if raw_key in by_exact:
                    return by_exact[raw_key]

                norm_key = self.normalize_filename(cand)
                if norm_key and norm_key in by_norm:
                    return by_norm[norm_key]

        return self.find_existing_file_by_id(canvas_file.id)

    def find_existing_file_by_id(self, canvas_file_id: int) -> Optional[dict]:
        """Busca si el archivo de Canvas ya existe en Google Drive mediante metadatos appProperties."""
        query = f"trashed = false and appProperties has {{ key='canvas_file_id' and value='{canvas_file_id}' }}"
        files = self._list_files_paginated(
            query,
            fields="files(id, name, parents, appProperties, modifiedTime)",
            page_size=1
        )
        return files[0] if files else None

    def find_existing_file_in_folder(self, folder_id: str, file_name: str) -> Optional[dict]:
        """Busca un archivo por nombre exacto o normalizado en una carpeta específica."""
        query = (
            f"'{folder_id}' in parents and "
            f"mimeType != 'application/vnd.google-apps.folder' and "
            f"trashed = false"
        )
        files = self._list_files_paginated(
            query,
            fields="files(id, name, parents, appProperties, modifiedTime)"
        )
        target_exact = self.sanitize_name(file_name).lower()
        target_norm = self.normalize_filename(file_name)

        for f in files:
            fname = f.get("name", "")
            if self.sanitize_name(fname).lower() == target_exact or fname.strip().lower() == file_name.strip().lower():
                return f
            if target_norm and self.normalize_filename(fname) == target_norm:
                return f
        return None

    def check_if_file_needs_download(
        self,
        canvas_file: CanvasFile,
        subject_files_index: Optional[Dict[str, Dict[str, dict]]] = None
    ) -> Tuple[str, Optional[dict]]:
        """
        Determina antes de descargar nada de Canvas si el archivo:
        - 'skip': Ya existe en la raíz o en una subcarpeta de la asignatura y no requiere descarga.
        - 'update': Ya existía de una sincronización previa pero el profesor lo ha actualizado en Canvas.
        - 'upload': No existe en absoluto en la asignatura y debe descargarse y subirse.
        """
        existing = self.find_existing_file_in_subject(canvas_file, subject_files_index)
        if not existing:
            return "upload", None

        existing_props = existing.get("appProperties") or {}
        existing_updated_at = existing_props.get("canvas_updated_at", "")
        canvas_updated_str = str(canvas_file.updated_at or "")

        # Si el archivo ya existe en la asignatura pero fue subido por el alumno o no tenía metadatos de Canvas:
        # NUNCA sobrescribirlo ni duplicarlo. Opcionalmente vinculamos metadatos sin tocar su contenido.
        if not existing_updated_at:
            try:
                self.service.files().update(
                    fileId=existing["id"],
                    body={
                        "appProperties": {
                            "canvas_file_id": str(canvas_file.id),
                            "canvas_updated_at": canvas_updated_str
                        }
                    },
                    fields="id",
                    supportsAllDrives=True
                ).execute()
            except Exception:
                pass
            return "skip", existing

        # Si tiene la misma fecha de actualización de Canvas, omitir
        if not canvas_updated_str or existing_updated_at == canvas_updated_str:
            return "skip", existing

        # El profesor actualizó el archivo en Canvas respecto a la versión previamente sincronizada
        return "update", existing

    @staticmethod
    def register_file_in_index(
        subject_files_index: Optional[Dict[str, Dict[str, dict]]],
        canvas_file: CanvasFile,
        file_id: str,
        folder_relpath: str = "(raíz)"
    ) -> None:
        """Actualiza el índice en memoria tras subir un archivo para evitar duplicados dentro del mismo ciclo."""
        if not subject_files_index:
            return
        clean_name = DriveService.sanitize_name(canvas_file.display_name)
        entry = {
            "id": file_id,
            "name": clean_name,
            "folder_relpath": folder_relpath,
            "appProperties": {
                "canvas_file_id": str(canvas_file.id),
                "canvas_updated_at": str(canvas_file.updated_at or "")
            }
        }
        subject_files_index.setdefault("by_canvas_id", {})[str(canvas_file.id)] = entry
        subject_files_index.setdefault("by_exact_name", {})[clean_name.lower()] = entry
        norm_key = DriveService.normalize_filename(clean_name)
        if norm_key:
            subject_files_index.setdefault("by_norm_name", {})[norm_key] = entry

    def upload_file_if_new(
        self,
        target_folder_id: str,
        canvas_file: CanvasFile,
        file_stream: io.BytesIO,
        existing_file: Optional[dict] = None,
        subject_files_index: Optional[Dict[str, Dict[str, dict]]] = None
    ) -> Tuple[str, str]:
        """
        Sube o actualiza un archivo en la carpeta destino designada respetando la idempotencia
        en toda la asignatura (raíz y subcarpetas).
        NUNCA borra ni modifica ningún documento creado por el alumno.
        Retorna (estado, file_id) con estado en ('skipped', 'uploaded', 'updated').
        """
        existing = existing_file or self.find_existing_file_in_subject(canvas_file, subject_files_index)
        if not existing:
            existing = self.find_existing_file_in_folder(target_folder_id, canvas_file.display_name)

        clean_name = self.sanitize_name(canvas_file.display_name)
        file_mime = canvas_file.content_type or "application/octet-stream"
        canvas_updated_str = str(canvas_file.updated_at or "")

        app_props = {
            "canvas_file_id": str(canvas_file.id),
            "canvas_updated_at": canvas_updated_str
        }

        if existing:
            existing_id = existing["id"]
            existing_props = existing.get("appProperties") or {}
            existing_updated_at = existing_props.get("canvas_updated_at", "")

            # Si el archivo no tenía canvas_updated_at (archivo previo del usuario) o la fecha coincide, NO re-subir
            if not existing_updated_at or not canvas_updated_str or existing_updated_at == canvas_updated_str:
                return "skipped", existing_id

            media = MediaIoBaseUpload(file_stream, mimetype=file_mime, resumable=True)
            updated_file = self.service.files().update(
                fileId=existing_id,
                body={"appProperties": app_props},
                media_body=media,
                fields="id",
                supportsAllDrives=True
            ).execute()
            logger.info(f"Archivo actualizado en Drive: '{clean_name}'")
            return "updated", updated_file["id"]

        media = MediaIoBaseUpload(file_stream, mimetype=file_mime, resumable=True)
        file_metadata = {
            "name": clean_name,
            "parents": [target_folder_id],
            "appProperties": app_props
        }

        created_file = self.service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id",
            supportsAllDrives=True
        ).execute()

        logger.info(f"Archivo subido a Drive: '{clean_name}'")
        return "uploaded", created_file["id"]
