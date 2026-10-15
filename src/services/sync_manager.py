import traceback
from config.settings import settings
from src.core.logger import logger
from src.models.schemas import SyncStats, SyncResponse
from src.services.canvas_service import CanvasService
from src.services.drive_service import DriveService
from src.services.calendar_service import CalendarService
from src.services.ai_service import AIService


class SyncManager:
    """Orquestador central para la sincronización inteligente entre Canvas, Google Drive y Google Calendar."""

    def __init__(self):
        self.canvas: CanvasService = None
        self.drive: DriveService = None
        self.calendar: CalendarService = None
        self.ai: AIService = None

    def initialize_services(self):
        """Inicializa los servicios comprobando credenciales."""
        if not settings.is_canvas_configured():
            raise ValueError("Canvas no está configurado (faltan CANVAS_BASE_URL o CANVAS_API_TOKEN).")
        if not settings.is_google_configured():
            raise ValueError("Google no está configurado (faltan credenciales OAuth en .env o entorno).")

        self.canvas = CanvasService()
        self.drive = DriveService()
        self.calendar = CalendarService()
        self.ai = AIService()

    def run_sync(self) -> SyncResponse:
        """
        Ejecuta el ciclo de sincronización con descubrimiento automático y Drive como fuente de verdad:
        1. Escanea las asignaturas que el usuario HA CREADO en su Google Drive (INGENIERIA / PRIMER...).
        2. Solo sincroniza aquellas que coinciden con cursos de Canvas. Cursos no creados por el alumno se omiten.
        3. Clasifica los archivos en subcarpetas de temas existentes respetando al 100% sus documentos personales.
        4. Sincroniza entregas en Google Calendar con colores dinámicos (amarillo pendiente / verde entregado).
        """
        stats = SyncStats()
        logger.info("=== INICIANDO CICLO DE SINCRONIZACIÓN INTELIGENTE ===")

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

        # 1. Descubrir asignaturas que el usuario ha creado en su Google Drive
        try:
            drive_subjects = self.drive.discover_existing_subjects()
            candidate_names = list(drive_subjects.keys())
        except Exception as de:
            error_msg = f"Error al escanear asignaturas en Google Drive: {de}"
            logger.error(error_msg)
            stats.errors.append(error_msg)
            return SyncResponse(success=False, message=error_msg, stats=stats)

        # 2. Obtener cursos activos de Canvas
        try:
            courses = self.canvas.get_active_courses()
            logger.info(f"Canvas reporta {len(courses)} cursos activos.")
        except Exception as ce:
            error_msg = f"Error al consultar cursos en Canvas: {ce}"
            logger.error(error_msg)
            stats.errors.append(error_msg)
            return SyncResponse(success=False, message=error_msg, stats=stats)

        for course in courses:
            # 3. Determinar correspondencia con las carpetas creadas por el usuario en Drive
            matched_folder_name = self.ai.match_course(course.name, candidate_names)

            if not matched_folder_name:
                logger.info(
                    f"⏭️ OMITIENDO curso '{course.name}': Aún no has creado una carpeta para esta "
                    "asignatura en tu Google Drive."
                )
                continue

            target_subject_info = drive_subjects[matched_folder_name]
            subject_folder_id = target_subject_info["id"]
            stats.courses_processed += 1
            logger.info(f"✅ Sincronizando: '{course.name}' -> Carpeta Drive: '{matched_folder_name}' ({target_subject_info['year']})")

            # 4. Obtener subcarpetas existentes dentro de la asignatura (ej: T1, TEMARI, PIZZARRES...)
            subfolders_map = self.drive.get_subject_subfolders(subject_folder_id)
            available_subfolders = list(subfolders_map.keys())

            # 5. Sincronización de Archivos a Google Drive
            try:
                files = self.canvas.get_course_files(course.id)
                logger.info(f"El curso '{course.name}' tiene {len(files)} archivos en Canvas.")

                for f in files:
                    try:
                        # Verificar si el archivo ya fue subido anteriormente
                        existing = self.drive.find_existing_file_by_id(f.id)
                        if existing:
                            existing_props = existing.get("appProperties") or {}
                            existing_updated = existing_props.get("canvas_updated_at", "")
                            if existing_updated and existing_updated == str(f.updated_at or ""):
                                stats.files_skipped += 1
                                continue

                        # Determinar en qué subcarpeta (tema) colocarlo
                        dest_subfolder = self.ai.classify_file_to_subfolder(
                            course_name=matched_folder_name,
                            file_name=f.display_name,
                            canvas_module_name=f.module_name,
                            available_subfolders=available_subfolders
                        )

                        if dest_subfolder and dest_subfolder in subfolders_map:
                            target_folder_id = subfolders_map[dest_subfolder]
                            logger.info(f"Clasificando '{f.display_name}' en subcarpeta: '{dest_subfolder}'")
                        else:
                            target_folder_id = subject_folder_id
                            logger.info(f"Ubicando '{f.display_name}' en la raíz de '{matched_folder_name}'")

                        # Descarga por streaming y subida
                        logger.info(f"Descargando de Canvas: '{f.display_name}' ({f.size} bytes)...")
                        file_stream = self.canvas.download_file_stream(f.url)
                        status, _ = self.drive.upload_file_if_new(target_folder_id, f, file_stream)

                        if status in ("uploaded", "updated"):
                            stats.files_uploaded += 1
                        else:
                            stats.files_skipped += 1

                    except Exception as fe:
                        err = f"Error con archivo '{f.display_name}' ({matched_folder_name}): {fe}"
                        logger.error(err)
                        stats.errors.append(err)

            except Exception as f_err:
                err = f"Error al procesar archivos de '{matched_folder_name}': {f_err}"
                logger.error(err)
                stats.errors.append(err)

            # 6. Sincronización de Entregas y Eventos en Google Calendar
            try:
                assignments = self.canvas.get_course_assignments(course.id)
                assignments_with_due = [a for a in assignments if a.due_at]
                logger.info(f"El curso '{matched_folder_name}' tiene {len(assignments_with_due)} tareas con fecha límite.")

                for a in assignments_with_due:
                    try:
                        # Generar resumen con IA si no tiene y hay descripción
                        if a.description and not a.summary_ai:
                            a.summary_ai = self.ai.summarize_assignment(a.name, a.description)

                        status, _ = self.calendar.sync_assignment_event(matched_folder_name, a)
                        if status == "created":
                            stats.calendar_events_created += 1
                        elif status == "updated":
                            stats.calendar_events_updated += 1
                        else:
                            stats.calendar_events_skipped += 1
                    except Exception as ae:
                        err = f"Error sincronizando tarea '{a.name}' ({matched_folder_name}): {ae}"
                        logger.error(err)
                        stats.errors.append(err)

            except Exception as a_err:
                err = f"Error al procesar tareas de '{matched_folder_name}': {a_err}"
                logger.error(err)
                stats.errors.append(err)

        logger.info(
            f"=== SINCRONIZACIÓN FINALIZADA ===\n"
            f"  - Cursos activos del alumno sincronizados: {stats.courses_processed}\n"
            f"  - Archivos subidos/actualizados: {stats.files_uploaded} (Omitidos ya existentes: {stats.files_skipped})\n"
            f"  - Eventos de calendario creados: {stats.calendar_events_created} (Actualizados con estado: {stats.calendar_events_updated}, Omitidos: {stats.calendar_events_skipped})\n"
            f"  - Errores encontrados: {len(stats.errors)}"
        )

        return SyncResponse(
            success=len(stats.errors) == 0,
            message="Sincronización completada con éxito" if not stats.errors else "Sincronización completada con advertencias",
            stats=stats
        )
