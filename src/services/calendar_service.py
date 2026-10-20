from typing import Optional, Tuple
from datetime import timedelta, timezone
try:
    from zoneinfo import ZoneInfo
    MADRID_TZ = ZoneInfo("Europe/Madrid")
except Exception:
    MADRID_TZ = timezone(timedelta(hours=2))

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from config.settings import settings
from src.core.logger import logger
from src.models.schemas import CanvasAssignment
from src.services.ai_service import normalize_text


class CalendarService:
    """Cliente para interactuar con Google Calendar API para tareas, entregas y exámenes."""

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
            scopes=["https://www.googleapis.com/auth/calendar"]
        )
        self.service = build("calendar", "v3", credentials=self.creds, cache_discovery=False)
        self.calendar_id: Optional[str] = None
        self.classes_calendar_id: Optional[str] = None

    def get_target_calendar_id(self) -> str:
        """Obtiene el ID del calendario secundario para tareas y entregas (ej: 'Canvas - Universidad')."""
        if self.calendar_id:
            return self.calendar_id

        target_name = settings.CALENDAR_NAME.strip()
        if target_name.lower() == "primary":
            self.calendar_id = "primary"
            return self.calendar_id

        page_token = None
        while True:
            cal_list = self.service.calendarList().list(pageToken=page_token).execute()
            for item in cal_list.get("items", []):
                if item.get("summary", "").strip().lower() == target_name.lower():
                    self.calendar_id = item["id"]
                    logger.info(f"Usando calendario existente: '{target_name}' (ID: {self.calendar_id})")
                    return self.calendar_id
            page_token = cal_list.get("nextPageToken")
            if not page_token:
                break

        new_cal = {
            "summary": target_name,
            "description": "Calendario sincronizado automáticamente con Canvas LMS",
            "timeZone": "Europe/Madrid"
        }
        created = self.service.calendars().insert(body=new_cal).execute()
        self.calendar_id = created["id"]
        logger.info(f"Creado nuevo calendario en Google Calendar: '{target_name}' (ID: {self.calendar_id})")
        return self.calendar_id

    def get_classes_calendar_id(self) -> Optional[str]:
        """Obtiene el ID del calendario de clases personales del alumno (ej: 'CLASES')."""
        if self.classes_calendar_id:
            return self.classes_calendar_id

        target_name = (settings.CLASSES_CALENDAR_NAME or "CLASES").strip()
        page_token = None
        while True:
            cal_list = self.service.calendarList().list(pageToken=page_token).execute()
            for item in cal_list.get("items", []):
                if item.get("summary", "").strip().lower() == target_name.lower():
                    self.classes_calendar_id = item["id"]
                    logger.info(f"Usando calendario de clases: '{target_name}' (ID: {self.classes_calendar_id})")
                    return self.classes_calendar_id
            page_token = cal_list.get("nextPageToken")
            if not page_token:
                break

        logger.warning(f"No se encontró el calendario de clases '{target_name}'.")
        return None

    @staticmethod
    def format_score(score: Optional[float]) -> str:
        """Formatea un número flotante eliminando ceros decimales innecesarios (ej: 10.0 -> 10, 9.5 -> 9.5)."""
        if score is None:
            return ""
        if isinstance(score, (int, float)):
            if float(score).is_integer():
                return str(int(score))
            return f"{score:.2f}".rstrip("0").rstrip(".")
        return str(score)

    @staticmethod
    def get_grade_display(assignment: CanvasAssignment) -> Optional[str]:
        """Devuelve la calificación formateada en formato 'nota/total' (ej: '10/10', '7/10', '9.5/10')."""
        if assignment.grade is None and assignment.score is None:
            return None

        # Caso 1: Score numérico disponible y points_possible > 0
        if assignment.score is not None and assignment.points_possible and assignment.points_possible > 0:
            score_str = CalendarService.format_score(assignment.score)
            max_str = CalendarService.format_score(assignment.points_possible)
            return f"{score_str}/{max_str}"

        # Caso 2: Grade numérico disponible y points_possible > 0
        if assignment.grade is not None and assignment.points_possible and assignment.points_possible > 0:
            try:
                num_val = float(assignment.grade)
                score_str = CalendarService.format_score(num_val)
                max_str = CalendarService.format_score(assignment.points_possible)
                return f"{score_str}/{max_str}"
            except ValueError:
                pass

        # Caso 3: Grade no numérico (ej: "complete", "A")
        if assignment.grade:
            return str(assignment.grade)

        # Caso 4: Solo score numérico
        if assignment.score is not None:
            return CalendarService.format_score(assignment.score)

        return None

    @staticmethod
    def is_course_match(course_name: str, class_subject: str) -> bool:
        """Determina si el nombre de una asignatura coincide con el asunto de un evento del calendario de clases."""
        norm_c = normalize_text(course_name)
        norm_s = normalize_text(class_subject)

        if not norm_c or not norm_s:
            return False

        if norm_c == norm_s or norm_c in norm_s or norm_s in norm_c:
            return True

        # Palabras comunes a ignorar para evitar falsos positivos entre asignaturas
        # (ej: "Fundaments de programació" vs "Fundaments de xarxes")
        generic_words = {
            "fundaments", "fundamentos", "introduccio", "introduccion",
            "teoria", "practica", "practiques", "grau", "grado",
            "ingenieria", "enginyeria", "ciencies", "ciencias", "de", "la", "el", "i", "y"
        }

        words_c = set(w for w in norm_c.split() if len(w) > 3 and w not in generic_words)
        words_s = set(w for w in norm_s.split() if len(w) > 3 and w not in generic_words)

        if words_c and words_s and words_c.intersection(words_s):
            return True

        return False

    def sync_exam_event(self, course_name: str, assignment: CanvasAssignment) -> Tuple[str, Optional[str], Optional[str]]:
        """
        Sincroniza un examen de Canvas vinculándolo y modificando el evento de clase existente de ese día en el calendario 'CLASES'.
        - Añade ' | EXAMEN' al título (ej: 'Bases de l'informàtica | G20 | EXAMEN').
        - Aplica color Uva/Morado (colorId: '3').
        - Preserva intactos los horarios (start/end) ya fijados por el alumno.
        - Elimina cualquier evento entregable previo duplicado en 'Canvas - Universidad'.
        Retorna (estado, event_id, event_title) con estado en ('skipped', 'created', 'updated').
        """
        if not assignment.due_at:
            return "skipped", None, None

        assignment_id_str = str(assignment.id)
        due_at_iso = assignment.due_at.isoformat()

        # 1. Limpieza preventiva: Si existía un evento entregable antiguo para este examen en Canvas - Universidad, eliminarlo
        try:
            target_cal_id = self.get_target_calendar_id()
            prev_evs = self.service.events().list(
                calendarId=target_cal_id,
                privateExtendedProperty=f"canvas_assignment_id={assignment_id_str}",
                maxResults=1
            ).execute().get("items", [])
            for prev_ev in prev_evs:
                self.service.events().delete(calendarId=target_cal_id, eventId=prev_ev["id"]).execute()
                logger.info(f"🗑️ Eliminado evento entregable previo de Canvas - Universidad (es un EXAMEN): '{prev_ev.get('summary')}'")
        except Exception as cle:
            logger.debug(f"Error al verificar evento previo entregable del examen {assignment.name}: {cle}")

        # 2. Localizar el calendario de clases
        classes_cal_id = self.get_classes_calendar_id()
        due_at_local = assignment.due_at.astimezone(MADRID_TZ)
        day_start = due_at_local.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        day_end = due_at_local.replace(hour=23, minute=59, second=59, microsecond=0).isoformat()

        matched_class_event = None
        if classes_cal_id:
            try:
                class_events = self.service.events().list(
                    calendarId=classes_cal_id,
                    timeMin=day_start,
                    timeMax=day_end,
                    singleEvents=True
                ).execute().get("items", [])

                for ev in class_events:
                    summary = ev.get("summary", "")
                    subj = summary.split("|")[0].strip()
                    if CalendarService.is_course_match(course_name, subj):
                        matched_class_event = ev
                        break
            except Exception as se:
                logger.warning(f"Error al buscar eventos de clase en CLASES para {assignment.name}: {se}")

        # Preparar calificación y descripción
        grade_disp = self.get_grade_display(assignment)
        desc_parts = [
            f"📌 Asignatura: {course_name}",
            f"📝 Examen: {assignment.name}"
        ]
        if grade_disp:
            desc_parts.append(f"⭐ Calificación: {grade_disp}")
        elif assignment.points_possible is not None and assignment.points_possible > 0:
            desc_parts.append(f"🎯 Puntuación máxima: {self.format_score(assignment.points_possible)} pts")

        if assignment.summary_ai:
            desc_parts.append(f"\n🧠 Resumen IA:\n{assignment.summary_ai}")
        if assignment.html_url:
            desc_parts.append(f"\n🔗 Enlace Canvas:\n{assignment.html_url}")

        description_text = "\n".join(desc_parts)
        color_id = "3"  # Uva / Morado (Grape)

        # CASO A: Se encontró el evento de clase de ese día en el calendario de clases
        if matched_class_event and classes_cal_id:
            event_id = matched_class_event["id"]
            curr_summary = matched_class_event.get("summary", "").strip()

            # Añadir ' | EXAMEN' si no lo tiene aún
            if "EXAMEN" not in curr_summary.upper():
                new_summary = f"{curr_summary} | EXAMEN"
            else:
                new_summary = curr_summary

            ext_props = matched_class_event.get("extendedProperties", {}).get("private", {})
            saved_aid = ext_props.get("canvas_assignment_id")
            saved_grade = ext_props.get("canvas_grade", "")
            saved_desc = matched_class_event.get("description", "")
            saved_color = matched_class_event.get("colorId")
            saved_title = matched_class_event.get("summary")

            current_grade_str = str(grade_disp or "")

            if (saved_aid == assignment_id_str and
                saved_grade == current_grade_str and
                saved_title == new_summary and
                saved_desc == description_text and
                saved_color == color_id):
                return "skipped", event_id, new_summary

            patch_body = {
                "summary": new_summary,
                "description": description_text,
                "colorId": color_id,
                "extendedProperties": {
                    "private": {
                        "canvas_assignment_id": assignment_id_str,
                        "canvas_is_exam": "true",
                        "canvas_due_at": due_at_iso,
                        "canvas_grade": current_grade_str,
                        "canvas_updated_at": str(assignment.updated_at or "")
                    }
                }
            }

            updated = self.service.events().patch(
                calendarId=classes_cal_id,
                eventId=event_id,
                body=patch_body
            ).execute()
            logger.info(f"🎓 Evento de clase modificado como EXAMEN en CLASES: '{new_summary}'")
            return "updated", updated["id"], new_summary

        # CASO B: Fallback si no hay clase ese día en el calendario CLASES
        cal_id = self.get_target_calendar_id()
        event_title = f"[EXAMEN] [{course_name}] {assignment.name}"
        if grade_disp:
            event_title = f"[EXAMEN: {grade_disp} 🎓] [{course_name}] {assignment.name}"

        events_result = self.service.events().list(
            calendarId=cal_id,
            privateExtendedProperty=f"canvas_assignment_id={assignment_id_str}",
            maxResults=1
        ).execute()
        existing_events = events_result.get("items", [])

        start_dt = assignment.due_at
        end_dt = assignment.due_at + timedelta(hours=2)

        event_body = {
            "summary": event_title,
            "description": description_text,
            "colorId": color_id,
            "start": {"dateTime": start_dt.isoformat()},
            "end": {"dateTime": end_dt.isoformat()},
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": 24 * 60},
                    {"method": "popup", "minutes": 120},
                ]
            },
            "extendedProperties": {
                "private": {
                    "canvas_assignment_id": assignment_id_str,
                    "canvas_is_exam": "true",
                    "canvas_due_at": due_at_iso,
                    "canvas_grade": str(grade_disp or ""),
                    "canvas_updated_at": str(assignment.updated_at or "")
                }
            }
        }

        if existing_events:
            existing = existing_events[0]
            existing_id = existing["id"]
            ext = existing.get("extendedProperties", {}).get("private", {})
            if (ext.get("canvas_due_at") == due_at_iso and
                ext.get("canvas_grade") == str(grade_disp or "") and
                existing.get("summary") == event_title and
                existing.get("description") == description_text and
                existing.get("colorId") == color_id):
                return "skipped", existing_id, event_title

            updated = self.service.events().patch(
                calendarId=cal_id,
                eventId=existing_id,
                body=event_body
            ).execute()
            logger.info(f"🎓 Examen actualizado en Calendar: '{event_title}'")
            return "updated", updated["id"], event_title

        created = self.service.events().insert(
            calendarId=cal_id,
            body=event_body
        ).execute()
        logger.info(f"🎓 Examen creado en Calendar: '{event_title}'")
        return "created", created["id"], event_title

    def sync_assignment_event(self, course_name: str, assignment: CanvasAssignment) -> Tuple[str, Optional[str], Optional[str]]:
        """
        Crea o actualiza un evento para una tarea de Canvas aplicando:
        - Amarillo (colorId: '5') si está PENDIENTE.
        - Verde (colorId: '10') si está ENTREGADA / CALIFICADA.
        - Si está calificada, incluye la nota en formato '10/10' o '7/10' en el título y descripción.
        Retorna (estado, event_id, event_title) con estado en ('skipped', 'created', 'updated').
        """
        if not assignment.due_at:
            return "skipped", None, None

        cal_id = self.get_target_calendar_id()
        assignment_id_str = str(assignment.id)
        due_at_iso = assignment.due_at.isoformat()

        # Determinar estado de entrega y calificación
        grade_disp = self.get_grade_display(assignment)
        is_delivered = assignment.is_submitted or assignment.submission_status in ("submitted", "graded") or (grade_disp is not None)
        color_id = "10" if is_delivered else "5"  # 10: Verde (Basil), 5: Amarillo (Banana)

        if grade_disp:
            status_label = f"CALIFICADO: {grade_disp} ✅"
        elif is_delivered:
            status_label = "ENTREGADO ✅"
        else:
            status_label = "PENDIENTE ⏳"

        event_title = f"[{status_label}] [{course_name}] {assignment.name}"

        # 1. Comprobar si ya existe un evento para esta tarea usando privateExtendedProperty
        events_result = self.service.events().list(
            calendarId=cal_id,
            privateExtendedProperty=f"canvas_assignment_id={assignment_id_str}",
            maxResults=1
        ).execute()

        existing_events = events_result.get("items", [])

        # Calcular duración del evento: desde 1 hora antes hasta la hora de entrega
        start_dt = assignment.due_at - timedelta(hours=1)
        end_dt = assignment.due_at

        # Construir descripción detallada
        desc_parts = [
            f"📌 Asignatura: {course_name}",
            f"📝 Tarea: {assignment.name}",
            f"🚦 Estado: {'Calificada ✅' if grade_disp else ('Entregada ✅' if is_delivered else 'Pendiente de entrega ⏳')}"
        ]

        if grade_disp:
            desc_parts.append(f"⭐ Calificación: {grade_disp}")
        elif assignment.points_possible is not None and assignment.points_possible > 0:
            desc_parts.append(f"🎯 Puntuación máxima: {self.format_score(assignment.points_possible)} pts")

        if assignment.summary_ai:
            desc_parts.append(f"\n🧠 Resumen IA:\n{assignment.summary_ai}")

        if assignment.html_url:
            desc_parts.append(f"\n🔗 Enlace Canvas:\n{assignment.html_url}")

        description_text = "\n".join(desc_parts)

        current_submission_status = assignment.submission_status or ("submitted" if is_delivered else "unsubmitted")
        current_grade_str = str(grade_disp or "")

        event_body = {
            "summary": event_title,
            "description": description_text,
            "colorId": color_id,
            "start": {
                "dateTime": start_dt.isoformat(),
            },
            "end": {
                "dateTime": end_dt.isoformat(),
            },
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": 24 * 60},  # 1 día antes
                    {"method": "popup", "minutes": 120},      # 2 horas antes
                ]
            },
            "extendedProperties": {
                "private": {
                    "canvas_assignment_id": assignment_id_str,
                    "canvas_due_at": due_at_iso,
                    "canvas_submission_status": current_submission_status,
                    "canvas_grade": current_grade_str,
                    "canvas_updated_at": str(assignment.updated_at or "")
                }
            }
        }

        if existing_events:
            existing = existing_events[0]
            existing_id = existing["id"]
            ext_props = existing.get("extendedProperties", {}).get("private", {})
            saved_due_at = ext_props.get("canvas_due_at")
            saved_status = ext_props.get("canvas_submission_status")
            saved_grade = ext_props.get("canvas_grade", "")
            saved_updated_at = ext_props.get("canvas_updated_at", "")
            saved_title = existing.get("summary")
            saved_desc = existing.get("description")
            saved_color = existing.get("colorId")

            # Si nada ha cambiado, omitir
            if (saved_due_at == due_at_iso and
                saved_status == current_submission_status and
                saved_grade == current_grade_str and
                saved_title == event_title and
                saved_desc == description_text and
                saved_color == color_id and
                (not assignment.updated_at or saved_updated_at == str(assignment.updated_at))):
                return "skipped", existing_id, event_title

            # Actualizar con patch
            updated = self.service.events().patch(
                calendarId=cal_id,
                eventId=existing_id,
                body=event_body
            ).execute()
            logger.info(f"Evento actualizado en Calendar: '{event_title}' (Color: {'Verde' if is_delivered else 'Amarillo'})")
            return "updated", updated["id"], event_title

        # Crear nuevo evento
        created = self.service.events().insert(
            calendarId=cal_id,
            body=event_body
        ).execute()

        logger.info(f"Evento creado en Calendar: '{event_title}' (Color: {'Verde' if is_delivered else 'Amarillo'})")
        return "created", created["id"], event_title
