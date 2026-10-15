import io
import re
from typing import List, Optional, Generator
from datetime import datetime
import requests
from config.settings import settings
from src.core.logger import logger
from src.models.schemas import CanvasCourse, CanvasFile, CanvasAssignment


class CanvasService:
    """Cliente para interactuar con la REST API de Canvas LMS."""

    def __init__(self, base_url: Optional[str] = None, token: Optional[str] = None):
        self.base_url = (base_url or settings.CANVAS_BASE_URL).rstrip("/")
        self.token = token or settings.CANVAS_API_TOKEN
        self.session = requests.Session()
        if self.token:
            self.session.headers.update({
                "Authorization": f"Bearer {self.token}",
                "User-Agent": "CanvasSyncAssistant/1.0"
            })

    def _paginate(self, url: str, params: Optional[dict] = None) -> List[dict]:
        """Gestiona la paginación de la API de Canvas mediante la cabecera 'Link'."""
        results: List[dict] = []
        current_url = url
        current_params = params or {}

        while current_url:
            response = self.session.get(current_url, params=current_params, timeout=30)
            if response.status_code == 401:
                raise PermissionError("Token de Canvas inválido o expirado (401 Unauthorized).")
            response.raise_for_status()

            data = response.json()
            if isinstance(data, list):
                results.extend(data)
            else:
                results.append(data)
                break

            # Siguiente página en el header Link
            link_header = response.headers.get("Link", "")
            next_url = None
            if link_header:
                links = link_header.split(",")
                for link in links:
                    parts = link.split(";")
                    if len(parts) >= 2 and 'rel="next"' in parts[1]:
                        match = re.search(r"<(.*)>", parts[0])
                        if match:
                            next_url = match.group(1)
                            break

            current_url = next_url
            current_params = None  # Los parámetros ya vienen incluidos en next_url

        return results

    def get_active_courses(self) -> List[CanvasCourse]:
        """Obtiene la lista de cursos en los que el estudiante está matriculado actualmente."""
        logger.info("Consultando cursos activos en Canvas...")
        url = f"{self.base_url}/api/v1/courses"
        params = {
            "enrollment_state": "active",
            "per_page": 50,
            "include[]": "term"
        }
        raw_courses = self._paginate(url, params=params)

        courses: List[CanvasCourse] = []
        for c in raw_courses:
            # Los cursos válidos tienen nombre y ID numérico
            name = c.get("name")
            course_id = c.get("id")
            if not name or not course_id:
                continue

            # Evitar cursos de acceso restringido o sin acceso
            if c.get("access_restricted_by_date"):
                continue

            courses.append(CanvasCourse(
                id=course_id,
                name=name.strip(),
                course_code=c.get("course_code"),
                enrollment_state=c.get("enrollment_state", "active")
            ))

        logger.info(f"Se encontraron {len(courses)} cursos activos en Canvas.")
        return courses

    def get_course_files(self, course_id: int) -> List[CanvasFile]:
        """Obtiene la lista de archivos disponibles en un curso."""
        url = f"{self.base_url}/api/v1/courses/{course_id}/files"
        params = {"per_page": 100}
        try:
            raw_files = self._paginate(url, params=params)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code in (403, 404):
                logger.warning(f"No se pudo acceder a archivos para el curso {course_id} (posiblemente oculto por el profesor).")
                return []
            raise

        files: List[CanvasFile] = []
        for f in raw_files:
            file_id = f.get("id")
            download_url = f.get("url")
            display_name = f.get("display_name") or f.get("filename")

            if not file_id or not download_url or not display_name:
                continue

            files.append(CanvasFile(
                id=file_id,
                display_name=display_name.strip(),
                filename=f.get("filename", display_name).strip(),
                url=download_url,
                size=f.get("size", 0),
                updated_at=f.get("updated_at") or f.get("created_at"),
                content_type=f.get("content-type") or f.get("mime_class")
            ))

        return files

    def get_course_assignments(self, course_id: int) -> List[CanvasAssignment]:
        """Obtiene las tareas y entregas del curso con fecha límite."""
        url = f"{self.base_url}/api/v1/courses/{course_id}/assignments"
        params = {"per_page": 50}
        try:
            raw_assignments = self._paginate(url, params=params)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code in (403, 404):
                logger.warning(f"No se pudo acceder a tareas para el curso {course_id}.")
                return []
            raise

        assignments: List[CanvasAssignment] = []
        for a in raw_assignments:
            assignment_id = a.get("id")
            name = a.get("name")
            if not assignment_id or not name:
                continue

            due_at_str = a.get("due_at")
            due_at_dt: Optional[datetime] = None
            if due_at_str:
                try:
                    # Canvas usa formato ISO: "2026-10-25T21:59:00Z"
                    due_at_dt = datetime.fromisoformat(due_at_str.replace("Z", "+00:00"))
                except ValueError:
                    pass

            assignments.append(CanvasAssignment(
                id=assignment_id,
                course_id=course_id,
                name=name.strip(),
                description=a.get("description", ""),
                due_at=due_at_dt,
                html_url=a.get("html_url"),
                points_possible=a.get("points_possible")
            ))

        return assignments

    def download_file_stream(self, file_url: str) -> io.BytesIO:
        """Descarga el contenido de un archivo en memoria (BytesIO) para subirlo a Drive."""
        response = self.session.get(file_url, stream=True, timeout=60)
        response.raise_for_status()

        buffer = io.BytesIO()
        for chunk in response.iter_content(chunk_size=1024 * 1024):  # 1MB chunks
            if chunk:
                buffer.write(chunk)
        buffer.seek(0)
        return buffer
