from typing import Optional, Tuple
from datetime import timedelta
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from config.settings import settings
from src.core.logger import logger
from src.models.schemas import CanvasAssignment


class CalendarService:
    """Cliente para interactuar con Google Calendar API con colores dinámicos por estado de entrega."""

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

    def get_target_calendar_id(self) -> str:
        """Obtiene el ID del calendario a usar (creándolo si no existe o usando 'primary')."""
        if self.calendar_id:
            return self.calendar_id

        target_name = settings.CALENDAR_NAME.strip()
        if target_name.lower() == "primary":
            self.calendar_id = "primary"
            return self.calendar_id

        # Buscar si ya existe un calendario con ese nombre
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

        # Si no existe, crear un nuevo calendario secundario
        new_cal = {
            "summary": target_name,
            "description": "Calendario sincronizado automáticamente con Canvas LMS",
            "timeZone": "Europe/Madrid"
        }
        created = self.service.calendars().insert(body=new_cal).execute()
        self.calendar_id = created["id"]
        logger.info(f"Creado nuevo calendario en Google Calendar: '{target_name}' (ID: {self.calendar_id})")
        return self.calendar_id

    def sync_assignment_event(self, course_name: str, assignment: CanvasAssignment) -> Tuple[str, Optional[str]]:
        """
        Crea o actualiza un evento para una tarea de Canvas aplicando:
        - Amarillo (colorId: '5') si está PENDIENTE.
        - Verde (colorId: '10') si está ENTREGADA / CALIFICADA.
        Retorna (estado, event_id) con estado en ('skipped', 'created', 'updated').
        """
        if not assignment.due_at:
            return "skipped", None

        cal_id = self.get_target_calendar_id()
        assignment_id_str = str(assignment.id)
        due_at_iso = assignment.due_at.isoformat()

        # Determinar estado de entrega y color
        is_delivered = assignment.is_submitted or assignment.submission_status in ("submitted", "graded")
        color_id = "10" if is_delivered else "5"  # 10: Verde (Basil), 5: Amarillo (Banana)
        status_label = "ENTREGADO ✅" if is_delivered else "PENDIENTE ⏳"
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
            f"🚦 Estado: {'Entregada ✅' if is_delivered else 'Pendiente de entrega ⏳'}"
        ]

        if assignment.grade is not None:
            desc_parts.append(f"⭐ Calificación: {assignment.grade}")
        elif assignment.points_possible is not None:
            desc_parts.append(f"🎯 Puntuación máxima: {assignment.points_possible} pts")

        if assignment.summary_ai:
            desc_parts.append(f"\n🧠 Resumen IA:\n{assignment.summary_ai}")

        if assignment.html_url:
            desc_parts.append(f"\n🔗 Enlace Canvas:\n{assignment.html_url}")

        description_text = "\n".join(desc_parts)

        current_submission_status = assignment.submission_status or ("submitted" if is_delivered else "unsubmitted")
        current_grade_str = str(assignment.grade or "")

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
                    "canvas_grade": current_grade_str
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
            saved_color = existing.get("colorId")

            # Si nada relevante ha cambiado (ni fecha, ni estado de entrega, ni nota, ni color), omitir
            if (saved_due_at == due_at_iso and
                saved_status == current_submission_status and
                saved_grade == current_grade_str and
                saved_color == color_id):
                return "skipped", existing_id

            # Si el alumno entregó la tarea o el profesor cambió la fecha/nota, actualizar con patch
            updated = self.service.events().patch(
                calendarId=cal_id,
                eventId=existing_id,
                body=event_body
            ).execute()
            logger.info(f"Evento actualizado en Calendar: '{event_title}' (Color: {'Verde' if is_delivered else 'Amarillo'})")
            return "updated", updated["id"]

        # Crear nuevo evento
        created = self.service.events().insert(
            calendarId=cal_id,
            body=event_body
        ).execute()

        logger.info(f"Evento creado en Calendar: '{event_title}' (Color: {'Verde' if is_delivered else 'Amarillo'})")
        return "created", created["id"]
