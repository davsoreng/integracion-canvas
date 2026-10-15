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
