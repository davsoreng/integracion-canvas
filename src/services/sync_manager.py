import json
import traceback
from datetime import datetime
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
        4. Sincroniza entregas y tareas en Google Calendar con notas formateadas (10/10, 7/10).
        5. Modifica directamente los eventos de clases existentes en caso de exámenes (ej: 'Bases de l'informàtica | G20 | EXAMEN').
        6. Emite registros exhaustivos para auditoría en Google Cloud Logging.
        """
        stats = SyncStats()
        sync_start_time = datetime.utcnow()
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
                            target_folder_label = f"{matched_folder_name}/{dest_subfolder}"
                            logger.info(f"Clasificando '{f.display_name}' en subcarpeta: '{dest_subfolder}'")
                        else:
                            target_folder_id = subject_folder_id
                            target_folder_label = matched_folder_name
                            logger.info(f"Ubicando '{f.display_name}' en la raíz de '{matched_folder_name}'")

                        # Descarga por streaming y subida
                        logger.info(f"Descargando de Canvas: '{f.display_name}' ({f.size} bytes)...")
                        file_stream = self.canvas.download_file_stream(f.url)
                        status, _ = self.drive.upload_file_if_new(target_folder_id, f, file_stream)

                        if status in ("uploaded", "updated"):
                            stats.files_uploaded += 1
                            size_str = f"{f.size / 1024:.1f} KB" if f.size < 1024 * 1024 else f"{f.size / (1024 * 1024):.1f} MB"
                            detail = f"[{matched_folder_name}] {f.display_name} -> {target_folder_label} ({size_str})"
                            stats.files_uploaded_list.append(detail)
                            logger.info(f"📥 [DRIVE] {'Subido' if status == 'uploaded' else 'Actualizado'}: {detail}")
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
                logger.info(f"El curso '{matched_folder_name}' tiene {len(assignments_with_due)} tareas o exámenes con fecha límite.")

                for a in assignments_with_due:
                    try:
                        # Generar resumen con IA si no tiene y hay descripción
                        if a.description and not a.summary_ai:
                            a.summary_ai = self.ai.summarize_assignment(a.name, a.description)

                        if a.is_exam:
                            status, _, event_title = self.calendar.sync_exam_event(matched_folder_name, a)
                            due_date_str = a.due_at.strftime('%Y-%m-%d') if a.due_at else ''
                            if status == "updated":
                                stats.exams_linked += 1
                                exam_detail = f"[{matched_folder_name}] {a.name} -> Modificado evento de clase: '{event_title}' ({due_date_str})"
                                stats.exams_linked_list.append(exam_detail)
                                logger.info(f"🎓 [EXAMEN] {exam_detail}")
                            elif status == "created":
                                stats.exams_linked += 1
                                exam_detail = f"[{matched_folder_name}] {a.name} -> Creado evento examen: '{event_title}' ({due_date_str})"
                                stats.exams_linked_list.append(exam_detail)
                                logger.info(f"🎓 [EXAMEN] {exam_detail}")
                            else:
                                stats.calendar_events_skipped += 1
                        else:
                            status, _, event_title = self.calendar.sync_assignment_event(matched_folder_name, a)
                            due_str = a.due_at.strftime('%Y-%m-%d %H:%M') if a.due_at else ''
                            grade_disp = self.calendar.get_grade_display(a)
                            if status == "created":
                                stats.calendar_events_created += 1
                                ev_detail = f"[{matched_folder_name}] {event_title} ({due_str})"
                                stats.calendar_events_created_list.append(ev_detail)
                                logger.info(f"📅 [CALENDAR] Creado: {ev_detail}")
                            elif status == "updated":
                                stats.calendar_events_updated += 1
                                ev_detail = f"[{matched_folder_name}] {event_title} [Nota: {grade_disp or 'N/A'}]"
                                stats.calendar_events_updated_list.append(ev_detail)
                                logger.info(f"🔄 [CALENDAR] Actualizado: {ev_detail}")
                            else:
                                stats.calendar_events_skipped += 1

                    except Exception as ae:
                        err = f"Error sincronizando tarea/examen '{a.name}' ({matched_folder_name}): {ae}"
                        logger.error(err)
                        stats.errors.append(err)

            except Exception as a_err:
                err = f"Error al procesar tareas de '{matched_folder_name}': {a_err}"
                logger.error(err)
                stats.errors.append(err)

        # 7. Informe detallado para consola y Google Cloud Logging
        sync_duration_secs = (datetime.utcnow() - sync_start_time).total_seconds()

        files_list_text = "\n".join(f"    • {item}" for item in stats.files_uploaded_list) if stats.files_uploaded_list else "    (Ningún archivo nuevo en esta ejecución)"
        cal_created_text = "\n".join(f"    • {item}" for item in stats.calendar_events_created_list) if stats.calendar_events_created_list else "    (Ningún evento nuevo creado)"
        cal_updated_text = "\n".join(f"    • {item}" for item in stats.calendar_events_updated_list) if stats.calendar_events_updated_list else "    (Ningún evento requirió actualización)"
        exams_list_text = "\n".join(f"    • {item}" for item in stats.exams_linked_list) if stats.exams_linked_list else "    (No se modificaron exámenes en esta ejecución)"
        errors_text = "\n".join(f"    ❌ {err}" for err in stats.errors) if stats.errors else "    (Sin errores)"

        summary_report = (
            f"\n"
            f"================================================================================\n"
            f"📊 INFORME DE AUDITORÍA Y EJECUCIÓN - SINCRONIZACIÓN CANVAS -> GOOGLE CLOUD\n"
            f"================================================================================\n"
            f"⏰ Duración: {sync_duration_secs:.2f}s | Cursos procesados: {stats.courses_processed}\n\n"
            f"📂 ARCHIVOS DESCARGADOS Y SUBIDOS A GOOGLE DRIVE (Total: {stats.files_uploaded} nuevos, {stats.files_skipped} omitidos):\n"
            f"{files_list_text}\n\n"
            f"📅 EVENTOS DE CALENDARIO CREADOS (Total: {stats.calendar_events_created}):\n"
            f"{cal_created_text}\n\n"
            f"🔄 EVENTOS DE CALENDARIO ACTUALIZADOS CON CALIFICACIÓN / ESTADO (Total: {stats.calendar_events_updated}):\n"
            f"{cal_updated_text}\n\n"
            f"🎓 EXÁMENES VINCULADOS A CALENDARIO DE CLASES (Total: {stats.exams_linked}):\n"
            f"{exams_list_text}\n\n"
            f"⚠️ ERRORES ENCONTRADOS (Total: {len(stats.errors)}):\n"
            f"{errors_text}\n"
            f"================================================================================"
        )
        logger.info(summary_report)

        # Emisión de registro estructurado JSON para Cloud Logging en Google Cloud
        try:
            cloud_log_entry = {
                "severity": "WARNING" if stats.errors else "INFO",
                "message": (
                    f"Canvas Sync completado: {stats.files_uploaded} archivos subidos, "
                    f"{stats.calendar_events_created} eventos creados, "
                    f"{stats.calendar_events_updated} actualizados, "
                    f"{stats.exams_linked} exámenes vinculados"
                ),
                "sync_audit": {
                    "duration_seconds": sync_duration_secs,
                    "courses_processed": stats.courses_processed,
                    "files_uploaded_count": stats.files_uploaded,
                    "files_skipped_count": stats.files_skipped,
                    "files_uploaded": stats.files_uploaded_list,
                    "calendar_events_created_count": stats.calendar_events_created,
                    "calendar_events_created": stats.calendar_events_created_list,
                    "calendar_events_updated_count": stats.calendar_events_updated,
                    "calendar_events_updated": stats.calendar_events_updated_list,
                    "exams_linked_count": stats.exams_linked,
                    "exams_linked": stats.exams_linked_list,
                    "errors_count": len(stats.errors),
                    "errors": stats.errors
                }
            }
            print(json.dumps(cloud_log_entry, ensure_ascii=False))
        except Exception as j_err:
            logger.debug(f"No se pudo serializar JSON log de Cloud Logging: {j_err}")

        return SyncResponse(
            success=len(stats.errors) == 0,
            message="Sincronización completada con éxito" if not stats.errors else "Sincronización completada con advertencias",
            stats=stats
        )
