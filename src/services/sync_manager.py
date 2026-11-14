from datetime import datetime, timezone
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
            raise ValueError("Google no está configurado (faltan credenciales de Cuenta de Servicio u OAuth).")

        self.canvas = CanvasService()
        self.drive = DriveService()
        self.calendar = CalendarService()
        self.ai = AIService()

    def run_sync(self) -> SyncResponse:
        """
        Ejecuta el ciclo de sincronización con descubrimiento automático y Drive como fuente de verdad:
        1. Escanea las asignaturas que el usuario ha creado en su Google Drive.
        2. Solo sincroniza aquellas que coinciden con cursos de Canvas. Cursos no creados por el alumno se omiten.
        3. Escanea recursivamente la raíz y todas las subcarpetas de cada asignatura para evitar cualquier duplicidad.
        4. Clasifica únicamente los archivos nuevos en las subcarpetas correspondientes.
        5. Sincroniza entregas, calificaciones y exámenes en Google Calendar.
        """
        stats = SyncStats()
        sync_start_time = datetime.now(timezone.utc)
        public_mode = settings.is_public_log_mode()
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
                if not public_mode:
                    logger.info(f"⏭️ Omitiendo curso '{course.name}': sin carpeta equivalente en Drive.")
                continue

            target_subject_info = drive_subjects[matched_folder_name]
            subject_folder_id = target_subject_info["id"]
            stats.courses_processed += 1
            if not public_mode:
                logger.info(f"✅ Sincronizando: '{course.name}' -> Carpeta Drive: '{matched_folder_name}'")

            # 4. Obtener todas las subcarpetas existentes y el índice completo de archivos de la asignatura (raíz + subcarpetas)
            subfolders_map = self.drive.get_subject_subfolders(subject_folder_id)
            available_subfolders = list(subfolders_map.keys())
            subject_files_index = self.drive.get_subject_files_index(subject_folder_id, subfolders_map)

            # 5. Sincronización de Archivos a Google Drive
            try:
                files = self.canvas.get_course_files(course.id)
                if not public_mode:
                    logger.info(f"El curso '{matched_folder_name}' tiene {len(files)} archivos en Canvas.")

                for f in files:
                    try:
                        # Comprobar primero en el índice completo de la asignatura (raíz y todas las subcarpetas)
                        action, existing_file = self.drive.check_if_file_needs_download(f, subject_files_index)
                        if action == "skip":
                            stats.files_skipped += 1
                            continue

                        if action == "update" and existing_file:
                            target_folder_id = (existing_file.get("parents") or [subject_folder_id])[0]
                            target_folder_label = f"{matched_folder_name}/{existing_file.get('folder_relpath', '(raíz)')}"
                        else:
                            dest_subfolder = self.ai.classify_file_to_subfolder(
                                course_name=matched_folder_name,
                                file_name=f.display_name,
                                canvas_module_name=f.module_name,
                                available_subfolders=available_subfolders
                            )
                            if dest_subfolder and dest_subfolder in subfolders_map:
                                target_folder_id = subfolders_map[dest_subfolder]
                                target_folder_label = f"{matched_folder_name}/{dest_subfolder}"
                                rel_assigned = dest_subfolder
                            else:
                                target_folder_id = subject_folder_id
                                target_folder_label = matched_folder_name
                                rel_assigned = "(raíz)"

                        file_stream = self.canvas.download_file_stream(f.url)
                        status, uploaded_id = self.drive.upload_file_if_new(
                            target_folder_id=target_folder_id,
                            canvas_file=f,
                            file_stream=file_stream,
                            existing_file=existing_file,
                            subject_files_index=subject_files_index
                        )

                        if status in ("uploaded", "updated"):
                            self.drive.register_file_in_index(
                                subject_files_index,
                                f,
                                uploaded_id,
                                folder_relpath=existing_file.get("folder_relpath", "(raíz)") if existing_file else rel_assigned
                            )
                            stats.files_uploaded += 1
                            size_str = f"{f.size / 1024:.1f} KB" if f.size < 1024 * 1024 else f"{f.size / (1024 * 1024):.1f} MB"
                            detail = f"[{matched_folder_name}] {f.display_name} -> {target_folder_label} ({size_str})"
                            stats.files_uploaded_list.append(detail)
                            if not public_mode:
                                logger.info(f"📥 [DRIVE] {'Subido' if status == 'uploaded' else 'Actualizado'}: {detail}")
                        else:
                            stats.files_skipped += 1

                    except Exception as fe:
                        err = f"Error con archivo en asignatura ({ stats.courses_processed }): {type(fe).__name__}" if public_mode else f"Error con archivo '{f.display_name}' ({matched_folder_name}): {fe}"
                        logger.error(err)
                        stats.errors.append(err)

            except Exception as f_err:
                err = f"Error al procesar archivos de asignatura: {type(f_err).__name__}" if public_mode else f"Error al procesar archivos de '{matched_folder_name}': {f_err}"
                logger.error(err)
                stats.errors.append(err)

            # 6. Sincronización de Entregas y Eventos en Google Calendar
            try:
                assignments = self.canvas.get_course_assignments(course.id)
                assignments_with_due = [a for a in assignments if a.due_at]

                for a in assignments_with_due:
                    try:
                        if a.description and not a.summary_ai:
                            a.summary_ai = self.ai.summarize_assignment(a.name, a.description)

                        if a.is_exam:
                            status, _, event_title = self.calendar.sync_exam_event(matched_folder_name, a)
                            due_date_str = a.due_at.strftime('%Y-%m-%d') if a.due_at else ''
                            if status in ("updated", "created"):
                                stats.exams_linked += 1
                                exam_detail = f"[{matched_folder_name}] {a.name} ({due_date_str})"
                                stats.exams_linked_list.append(exam_detail)
                            else:
                                stats.calendar_events_skipped += 1
                        else:
                            status, _, event_title = self.calendar.sync_assignment_event(matched_folder_name, a)
                            due_str = a.due_at.strftime('%Y-%m-%d %H:%M') if a.due_at else ''
                            if status == "created":
                                stats.calendar_events_created += 1
                                ev_detail = f"[{matched_folder_name}] {a.name} ({due_str})"
                                stats.calendar_events_created_list.append(ev_detail)
                            elif status == "updated":
                                stats.calendar_events_updated += 1
                                ev_detail = f"[{matched_folder_name}] {a.name}"
                                stats.calendar_events_updated_list.append(ev_detail)
                            else:
                                stats.calendar_events_skipped += 1

                    except Exception as ae:
                        err = f"Error sincronizando evento en Calendar: {type(ae).__name__}" if public_mode else f"Error sincronizando tarea/examen '{a.name}' ({matched_folder_name}): {ae}"
                        logger.error(err)
                        stats.errors.append(err)

            except Exception as a_err:
                err = f"Error al procesar tareas de asignatura: {type(a_err).__name__}" if public_mode else f"Error al procesar tareas de '{matched_folder_name}': {a_err}"
                logger.error(err)
                stats.errors.append(err)

        # 7. Informe de ejecución (respetando modo de privacidad en logs públicos de GitHub Actions)
        sync_duration_secs = (datetime.now(timezone.utc) - sync_start_time).total_seconds()

        if public_mode:
            summary_report = (
                f"\n"
                f"================================================================================\n"
                f"📊 RESUMEN DE EJECUCIÓN (MODO PÚBLICO PROTEGIDO)\n"
                f"================================================================================\n"
                f"⏰ Duración: {sync_duration_secs:.2f}s | Cursos sincronizados: {stats.courses_processed}\n"
                f"📂 Drive: {stats.files_uploaded} archivos subidos/actualizados | {stats.files_skipped} omitidos (ya existentes)\n"
                f"📅 Calendar: {stats.calendar_events_created} creados | {stats.calendar_events_updated} actualizados | {stats.exams_linked} exámenes vinculados\n"
                f"⚠️ Errores: {len(stats.errors)}\n"
                f"================================================================================"
            )
        else:
            files_list_text = "\n".join(f"    • {item}" for item in stats.files_uploaded_list) if stats.files_uploaded_list else "    (Ningún archivo nuevo en esta ejecución)"
            cal_created_text = "\n".join(f"    • {item}" for item in stats.calendar_events_created_list) if stats.calendar_events_created_list else "    (Ningún evento nuevo creado)"
            cal_updated_text = "\n".join(f"    • {item}" for item in stats.calendar_events_updated_list) if stats.calendar_events_updated_list else "    (Ningún evento requirió actualización)"
            exams_list_text = "\n".join(f"    • {item}" for item in stats.exams_linked_list) if stats.exams_linked_list else "    (No se modificaron exámenes en esta ejecución)"
            errors_text = "\n".join(f"    ❌ {err}" for err in stats.errors) if stats.errors else "    (Sin errores)"

            summary_report = (
                f"\n"
                f"================================================================================\n"
                f"📊 INFORME DE EJECUCIÓN - SINCRONIZACIÓN CANVAS -> GOOGLE WORKSPACE\n"
                f"================================================================================\n"
                f"⏰ Duración: {sync_duration_secs:.2f}s | Cursos procesados: {stats.courses_processed}\n\n"
                f"📂 ARCHIVOS EN GOOGLE DRIVE (Total: {stats.files_uploaded} nuevos/actualizados, {stats.files_skipped} omitidos):\n"
                f"{files_list_text}\n\n"
                f"📅 EVENTOS DE CALENDARIO CREADOS (Total: {stats.calendar_events_created}):\n"
                f"{cal_created_text}\n\n"
                f"🔄 EVENTOS DE CALENDARIO ACTUALIZADOS (Total: {stats.calendar_events_updated}):\n"
                f"{cal_updated_text}\n\n"
                f"🎓 EXÁMENES VINCULADOS A CALENDARIO DE CLASES (Total: {stats.exams_linked}):\n"
                f"{exams_list_text}\n\n"
                f"⚠️ ERRORES ENCONTRADOS (Total: {len(stats.errors)}):\n"
                f"{errors_text}\n"
                f"================================================================================"
            )

        logger.info(summary_report)

        return SyncResponse(
            success=len(stats.errors) == 0,
            message="Sincronización completada con éxito" if not stats.errors else "Sincronización completada con advertencias",
            stats=stats
        )
