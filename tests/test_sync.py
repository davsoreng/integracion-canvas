import pytest
from datetime import datetime, timezone
from src.models.schemas import CanvasCourse, CanvasFile, CanvasAssignment, SyncStats
from src.services.drive_service import DriveService
from src.services.ai_service import AIService
from fastapi.testclient import TestClient
from src.main import app


def test_canvas_models():
    """Valida la instanciación correcta de modelos de datos y estados de entrega."""
    course = CanvasCourse(id=101, name="Matemáticas I", course_code="MAT101")
    assert course.id == 101
    assert course.name == "Matemáticas I"

    file = CanvasFile(
        id=202,
        display_name="Tema 1 - Álgebra.pdf",
        filename="tema1.pdf",
        url="https://canvas.test/files/202/download",
        size=1024,
        updated_at="2026-10-15T09:00:00Z",
        module_name="Unidad 1. Matrices"
    )
    assert file.id == 202
    assert file.display_name.endswith(".pdf")
    assert file.module_name == "Unidad 1. Matrices"

    assignment = CanvasAssignment(
        id=303,
        course_id=101,
        name="Entrega Práctica 1",
        due_at=datetime(2026, 11, 1, 23, 59, tzinfo=timezone.utc),
        points_possible=10.0,
        submission_status="graded",
        is_submitted=True,
        grade="9.5"
    )
    assert assignment.id == 303
    assert assignment.due_at is not None
    assert assignment.is_submitted is True
    assert assignment.grade == "9.5"


def test_drive_sanitize_name():
    """Comprueba que los caracteres no permitidos en nombres de Drive se limpian adecuadamente."""
    dirty_name = 'Álgebra / Geometría: Tema "1" * [2026] <final>?'
    clean = DriveService.sanitize_name(dirty_name)
    assert "/" not in clean
    assert ":" not in clean
    assert '"' not in clean
    assert "*" not in clean
    assert "<" not in clean
    assert ">" not in clean
    assert "?" not in clean


def test_ai_course_matching():
    """Valida el mapeo de cursos Canvas a las carpetas creadas por el alumno en Drive."""
    ai = AIService(api_key=None)  # Prueba el motor heurístico sin necesidad de API key
    candidates = [
        "ÀLGEBRA",
        "ANALISIS MATEMÀTIC",
        "BASES DE L'INFORMÀTICA",
        "FUNDAMENTS DE XARXES",
        "FUNDAMENTS DE PROGRAMACIÓ"
    ]

    # Coincidencias válidas
    match_alg = ai.match_course("ÁLGEBRA (20269687001101M1100)", candidates)
    assert match_alg == "ÀLGEBRA"

    match_redes = ai.match_course("FUNDAMENTOS DE REDES (20269687001105M1100)", candidates)
    assert match_redes == "FUNDAMENTS DE XARXES"

    match_prog = ai.match_course("FUNDAMENTOS DE PROGRAMACIÓN (20269687001104M1100)", candidates)
    assert match_prog == "FUNDAMENTS DE PROGRAMACIÓ"

    # Curso que NO está creado por el alumno en Drive (debe retornar None para omitirse)
    match_poo = ai.match_course("PROGRAMACIÓN ORIENTADA A OBJETOS (20269687001109M1100)", candidates)
    assert match_poo is None


def test_ai_subfolder_classification():
    """Valida la clasificación inteligente de archivos a subcarpetas de temas."""
    ai = AIService(api_key=None)
    subfolders_alg = [
        "T1 - MATRIUS/DET/ECU.LIN",
        "T1 - MATRIUS/DET/ECU.LIN/PIZZARRES",
        "T1 - MATRIUS/DET/ECU.LIN/TEMARI",
        "T1 - MATRIUS/DET/ECU.LIN/EXERCICIS"
    ]

    # Pizarra debe ir a PIZZARRES
    res_pizarra = ai.classify_file_to_subfolder("ÀLGEBRA", "PIZARRA CLASE 1 14092026.pdf", None, subfolders_alg)
    assert res_pizarra == "T1 - MATRIUS/DET/ECU.LIN/PIZZARRES"

    # Clase / teoría debe ir a TEMARI
    res_teoria = ai.classify_file_to_subfolder("ÀLGEBRA", "CLASE 1 y 2 + PRESENTACIÓN.pdf", None, subfolders_alg)
    assert res_teoria == "T1 - MATRIUS/DET/ECU.LIN/TEMARI"

    # Tema general en Redes (T1 vs T2)
    subfolders_redes = ["T1-Fundamentos-de-redes", "T2-Modelos_de_referencia", "T3-Medios"]
    res_redes_t2 = ai.classify_file_to_subfolder("FUNDAMENTS DE XARXES", "Tema 2 Modelos.pptx", "Tema 2", subfolders_redes)
    assert res_redes_t2 == "T2-Modelos_de_referencia"


def test_sync_stats_counters():
    """Comprueba el acumulador de estadísticas."""
    stats = SyncStats()
    stats.courses_processed += 1
    stats.files_uploaded += 2
    stats.files_skipped += 5
    stats.calendar_events_created += 1

    assert stats.courses_processed == 1
    assert stats.files_uploaded == 2
    assert stats.files_skipped == 5
    assert stats.calendar_events_created == 1
    assert len(stats.errors) == 0


def test_fastapi_endpoints():
    """Comprueba que los endpoints básicos de FastAPI respondan correctamente."""
    client = TestClient(app)
    
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "online"

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "healthy"


def test_is_exam_assignment():
    """Valida la detección de exámenes y pruebas frente a tareas estándar."""
    from src.services.canvas_service import CanvasService

    assert CanvasService.is_exam_assignment("Examen Parcial T1-2-3", quiz_id=123, is_quiz_assignment=True) is True
    assert CanvasService.is_exam_assignment("Parcial Tema 1-2-3", quiz_id=456) is True
    assert CanvasService.is_exam_assignment("Control 1 de Álgebra") is True
    assert CanvasService.is_exam_assignment("Avaluació contínua tema 2") is True
    assert CanvasService.is_exam_assignment("Examen Final Ordinario") is True

    # Tareas normales no son exámenes
    assert CanvasService.is_exam_assignment("Tarea tema 1") is False
    assert CanvasService.is_exam_assignment("FP · w01a · PPG: primera sesión y diagnóstico") is False
    assert CanvasService.is_exam_assignment("Practica 1") is False
    assert CanvasService.is_exam_assignment("ENTREGA 1 (EJERCICIOS DE LA UNIDAD 1)") is False


def test_grade_formatting():
    """Valida el formateo de notas en formato 'nota/total' (ej: 10/10, 7/10, 9.5/10)."""
    from src.services.calendar_service import CalendarService

    # 10/10
    a1 = CanvasAssignment(id=1, course_id=10, name="T1", score=10.0, points_possible=10.0, grade="10")
    assert CalendarService.get_grade_display(a1) == "10/10"

    # 7/10
    a2 = CanvasAssignment(id=2, course_id=10, name="T2", score=7.0, points_possible=10.0, grade="7")
    assert CalendarService.get_grade_display(a2) == "7/10"

    # 9.5/10
    a3 = CanvasAssignment(id=3, course_id=10, name="T3", score=9.5, points_possible=10.0, grade="9.5")
    assert CalendarService.get_grade_display(a3) == "9.5/10"

    # 9.95/10
    a4 = CanvasAssignment(id=4, course_id=10, name="T4", score=9.95, points_possible=10.0, grade="9.95")
    assert CalendarService.get_grade_display(a4) == "9.95/10"

    # Grado de texto 'complete'
    a5 = CanvasAssignment(id=5, course_id=10, name="T5", grade="complete", score=1.0, points_possible=1.0)
    # score=1.0 y points=1.0 daría 1/1
    a5_text_only = CanvasAssignment(id=6, course_id=10, name="T6", grade="complete", points_possible=0.0)
    assert CalendarService.get_grade_display(a5_text_only) == "complete"

    # Sin corregir
    a6 = CanvasAssignment(id=7, course_id=10, name="T7", points_possible=10.0)
    assert CalendarService.get_grade_display(a6) is None


def test_sync_stats_audit_lists():
    """Valida las listas detalladas para auditoría de Cloud Logging."""
    stats = SyncStats()
    stats.files_uploaded_list.append("[ÀLGEBRA] Pizarra.pdf -> T1 (1.2 MB)")
    stats.calendar_events_created_list.append("[PENDIENTE ⏳] Tarea 1")
    stats.calendar_events_updated_list.append("[CALIFICADO: 10/10 ✅] Tarea 2")
    stats.exams_linked_list.append("[BASES DE L'INFORMÀTICA] Examen -> Modificado clase: 'Bases | G20 | EXAMEN'")

    assert len(stats.files_uploaded_list) == 1
    assert len(stats.calendar_events_created_list) == 1
    assert len(stats.calendar_events_updated_list) == 1
    assert len(stats.exams_linked_list) == 1


def test_is_course_match():
    """Valida la correspondencia exacta entre nombres de asignaturas y eventos de clases evitando falsos positivos."""
    from src.services.calendar_service import CalendarService

    # Coincidencias correctas
    assert CalendarService.is_course_match("FUNDAMENTS DE XARXES", "Fundaments de xarxes") is True
    assert CalendarService.is_course_match("FUNDAMENTS DE PROGRAMACIÓ", "Fundaments de programació") is True
    assert CalendarService.is_course_match("BASES DE L'INFORMÀTICA", "Bases de l'informàtica") is True
    assert CalendarService.is_course_match("ANALISIS MATEMÀTIC", "Analisis Matemàtic") is True
    assert CalendarService.is_course_match("ÀLGEBRA", "Àlgebra") is True

    # Evitar falsos positivos entre cursos que comparten la palabra 'Fundaments'
    assert CalendarService.is_course_match("FUNDAMENTS DE XARXES", "Fundaments de programació") is False
    assert CalendarService.is_course_match("FUNDAMENTS DE PROGRAMACIÓ", "Fundaments de xarxes") is False
    assert CalendarService.is_course_match("ÀLGEBRA", "Analisis Matemàtic") is False


