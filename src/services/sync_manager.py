import traceback
from config.settings import settings
from src.core.logger import logger
from src.models.schemas import SyncStats, SyncResponse
from src.services.canvas_service import CanvasService
from src.services.drive_service import DriveService
from src.services.calendar_service import CalendarService


class SyncManager:
    """Orquestador central para la sincronización entre Canvas, Google Drive y Google Calendar."""

    def __init__(self):
        self.canvas: CanvasService = None
        self.drive: DriveService = None
        self.calendar: CalendarService = None

    def initialize_services(self):
        """Inicializa los servicios comprobando credenciales."""
        if not settings.is_canvas_configured():
            raise ValueError("Canvas no está configurado (faltan CANVAS_BASE_URL o CANVAS_API_TOKEN).")
        if not settings.is_google_configured():
            raise ValueError("Google no está configurado (faltan credenciales OAuth en .env o entorno).")

        self.canvas = CanvasService()
        self.drive = DriveService()
        self.calendar = CalendarService()

    def run_sync(self) -> SyncResponse:
        """Ejecuta el ciclo completo de sincronización de manera idempotente y tolerante a fallos."""
        stats = SyncStats()
        logger.info("=== INICIANDO CICLO DE SINCRONIZACIÓN (CANVAS -> DRIVE & CALENDAR) ===")

        try:
            self.initialize_services()
        except Exception as e:
            msg = f"Error al inicializar servicios: {e}"
            logger.error(msg)
            return SyncResponse(
                success=False,
                message=msg,
                stats=stats
            )

        try:
            courses = self.canvas.get_active_courses()
            stats.courses_processed = len(courses)
        except Exception as e:
            error_msg = f"Error al consultar cursos en Canvas: {e}"
            logger.error(error_msg)
            stats.errors.append(error_msg)
            return SyncResponse(
                success=False,
                message=error_msg,
                stats=stats
            )

        for course in courses:
            logger.info(f"--- Procesando curso: '{course.name}' (ID: {course.id}) ---")

            # 1. Sincronización de Archivos con Google Drive
            try:
                course_folder_id = self.drive.get_course_folder_id(course.name)
                files = self.canvas.get_course_files(course.id)
                logger.info(f"El curso '{course.name}' tiene {len(files)} archivos en Canvas.")

                for f in files:
                    try:
                        # Verificar primero si el archivo ya existe antes de descargarlo
                        existing = self.drive.find_existing_file(course_folder_id, f)
                        if existing:
                            existing_props = existing.get("appProperties") or {}
                            existing_updated = existing_props.get("canvas_updated_at", "")
                            if existing_updated and existing_updated == str(f.updated_at or ""):
                                stats.files_skipped += 1
                                continue

                        # Descargar y subir solo si es nuevo o ha sido modificado
                        logger.info(f"Descargando de Canvas: '{f.display_name}' ({f.size} bytes)...")
                        file_stream = self.canvas.download_file_stream(f.url)
                        status, _ = self.drive.upload_file_if_new(course_folder_id, f, file_stream)

                        if status in ("uploaded", "updated"):
                            stats.files_uploaded += 1
                        else:
                            stats.files_skipped += 1

                    except Exception as fe:
                        err = f"Error con archivo '{f.display_name}' ({course.name}): {fe}"
                        logger.error(err)
                        stats.errors.append(err)

            except Exception as ce:
                err = f"Error procesando archivos del curso '{course.name}': {ce}"
                logger.error(err)
                stats.errors.append(err)

            # 2. Sincronización de Tareas y Entregas con Google Calendar
            try:
                assignments = self.canvas.get_course_assignments(course.id)
                assignments_with_due = [a for a in assignments if a.due_at]
                logger.info(f"El curso '{course.name}' tiene {len(assignments_with_due)} tareas con fecha límite.")

                for a in assignments_with_due:
                    try:
                        status, _ = self.calendar.sync_assignment_event(course.name, a)
                        if status == "created":
                            stats.calendar_events_created += 1
                        elif status == "updated":
                            stats.calendar_events_updated += 1
                        else:
                            stats.calendar_events_skipped += 1
                    except Exception as ae:
                        err = f"Error sincronizando tarea '{a.name}' ({course.name}): {ae}"
                        logger.error(err)
                        stats.errors.append(err)

            except Exception as cae:
                err = f"Error procesando tareas del curso '{course.name}': {cae}"
                logger.error(err)
                stats.errors.append(err)

        logger.info(
            f"=== SINCRONIZACIÓN FINALIZADA ===\n"
            f"  - Cursos procesados: {stats.courses_processed}\n"
            f"  - Archivos subidos/actualizados: {stats.files_uploaded} (Omitidos ya existentes: {stats.files_skipped})\n"
            f"  - Eventos creados: {stats.calendar_events_created} (Actualizados: {stats.calendar_events_updated}, Omitidos: {stats.calendar_events_skipped})\n"
            f"  - Errores encontrados: {len(stats.errors)}"
        )

        return SyncResponse(
            success=len(stats.errors) == 0,
            message="Sincronización completada con éxito" if not stats.errors else "Sincronización completada con advertencias",
            stats=stats
        )
