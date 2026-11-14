from datetime import datetime, timezone
from src.models.schemas import CanvasCourse, CanvasFile, CanvasAssignment, SyncStats
from src.services.drive_service import DriveService
from src.services.ai_service import AIService
from src.core.logger import SecretScrubberFilter


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


def test_drive_sanitize_and_normalize_name():
    """Comprueba que los caracteres no permitidos en nombres de Drive se limpian y normalizan adecuadamente."""
    dirty_name = 'Álgebra / Geometría: Tema "1" * [2026] <final>?.pdf'
    clean = DriveService.sanitize_name(dirty_name)
    assert "/" not in clean
    assert ":" not in clean
    assert '"' not in clean
    assert "*" not in clean
    assert "<" not in clean
    assert ">" not in clean
    assert "?" not in clean

    # Dos variantes del mismo nombre (con y sin acentos o guiones bajos) deben normalizar igual
    norm1 = DriveService.normalize_filename("Tema 1: Introducción a las Matrices.PDF")
    norm2 = DriveService.normalize_filename("tema_1__introduccion_a_las_matrices.pdf")
    assert norm1 == norm2


def test_find_existing_file_in_subject_root_and_subfolders():
    """
    Verifica que el servicio detecte archivos existentes tanto en la propia raíz de la asignatura
    como en cualquiera de sus subcarpetas, ya sea por ID de Canvas o por nombre normalizado.
    """
    subject_index = {
        "by_canvas_id": {
            "999": {
                "id": "drive_id_root_1",
                "name": "Guia_Docente.pdf",
                "folder_relpath": "(raíz)",
                "appProperties": {"canvas_file_id": "999", "canvas_updated_at": "2026-09-01T10:00:00Z"}
            }
        },
        "by_exact_name": {
            "guia_docente.pdf": {
                "id": "drive_id_root_1",
                "name": "Guia_Docente.pdf",
                "folder_relpath": "(raíz)",
                "appProperties": {"canvas_file_id": "999", "canvas_updated_at": "2026-09-01T10:00:00Z"}
            },
            "pizarra clase 1.pdf": {
                "id": "drive_id_sub_2",
                "name": "Pizarra Clase 1.pdf",
                "folder_relpath": "T1/PIZZARRES",
                "appProperties": {}  # Subido manualmente por el alumno sin metadatos de Canvas
            }
        },
        "by_norm_name": {
            DriveService.normalize_filename("Guia_Docente.pdf"): {
                "id": "drive_id_root_1",
                "name": "Guia_Docente.pdf",
                "folder_relpath": "(raíz)",
                "appProperties": {"canvas_file_id": "999", "canvas_updated_at": "2026-09-01T10:00:00Z"}
            },
            DriveService.normalize_filename("Pizarra Clase 1.pdf"): {
                "id": "drive_id_sub_2",
                "name": "Pizarra Clase 1.pdf",
                "folder_relpath": "T1/PIZZARRES",
                "appProperties": {}
            }
        }
    }

    drive_dummy = DriveService.__new__(DriveService)

    # 1. Archivo en la raíz con el mismo canvas_file_id y misma fecha -> debe omitirse ('skip')
    f_root = CanvasFile(
        id=999,
        display_name="Guia_Docente.pdf",
        filename="Guia_Docente.pdf",
        url="https://canvas.test/files/999",
        size=1024,
        updated_at="2026-09-01T10:00:00Z"
    )
    action_root, found_root = drive_dummy.check_if_file_needs_download(f_root, subject_index)
    assert action_root == "skip"
    assert found_root["id"] == "drive_id_root_1"

    # 2. Archivo ubicado en una subcarpeta ('T1/PIZZARRES') sin canvas_file_id pero con nombre coincidente -> debe omitirse ('skip')
    f_sub = CanvasFile(
        id=5555,
        display_name="PIZARRA_CLASE_1.pdf",
        filename="pizarra_clase_1.pdf",
        url="https://canvas.test/files/5555",
        size=2048,
        updated_at="2026-10-01T10:00:00Z"
    )
    action_sub, found_sub = drive_dummy.check_if_file_needs_download(f_sub, subject_index)
    assert action_sub == "skip"
    assert found_sub["id"] == "drive_id_sub_2"
    assert found_sub["folder_relpath"] == "T1/PIZZARRES"

    # 3. Archivo que el profesor ha actualizado en Canvas respecto a la versión sincronizada -> 'update'
    f_updated = CanvasFile(
        id=999,
        display_name="Guia_Docente.pdf",
        filename="Guia_Docente.pdf",
        url="https://canvas.test/files/999",
        size=1024,
        updated_at="2026-11-10T18:30:00Z"
    )
    action_upd, found_upd = drive_dummy.check_if_file_needs_download(f_updated, subject_index)
    assert action_upd == "update"
    assert found_upd["id"] == "drive_id_root_1"


def test_secret_scrubber_filter():
    """Garantiza que ningún secreto, token o cuenta de servicio aparezca en los logs."""
    raw_msg = (
        "Conectando con Bearer 7890~abcdef123456 y cuenta "
        "bot-sync@mi-proyecto-123.iam.gserviceaccount.com con API key AIzaSyD12345678901234567890123456789012"
    )
    sanitized = SecretScrubberFilter.sanitize(raw_msg)
    assert "7890~abcdef123456" not in sanitized
    assert "iam.gserviceaccount.com" not in sanitized
    assert "AIzaSyD" not in sanitized
    assert "[REDACTED]" in sanitized


def test_ai_course_matching():
    """Valida el mapeo de cursos Canvas a las carpetas creadas por el alumno en Drive."""
    ai = AIService(api_key=None)
    candidates = [
        "ÀLGEBRA",
        "ANALISIS MATEMÀTIC",
        "BASES DE L'INFORMÀTICA",
        "FUNDAMENTS DE XARXES",
        "FUNDAMENTS DE PROGRAMACIÓ"
    ]

    match_alg = ai.match_course("ÁLGEBRA (20269687001101M1100)", candidates)
    assert match_alg == "ÀLGEBRA"

    match_redes = ai.match_course("FUNDAMENTOS DE REDES (20269687001105M1100)", candidates)
    assert match_redes == "FUNDAMENTS DE XARXES"

    match_prog = ai.match_course("FUNDAMENTOS DE PROGRAMACIÓN (20269687001104M1100)", candidates)
    assert match_prog == "FUNDAMENTS DE PROGRAMACIÓ"

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

    res_pizarra = ai.classify_file_to_subfolder("ÀLGEBRA", "PIZARRA CLASE 1 14092026.pdf", None, subfolders_alg)
    assert res_pizarra == "T1 - MATRIUS/DET/ECU.LIN/PIZZARRES"

    res_teoria = ai.classify_file_to_subfolder("ÀLGEBRA", "CLASE 1 y 2 + PRESENTACIÓN.pdf", None, subfolders_alg)
    assert res_teoria == "T1 - MATRIUS/DET/ECU.LIN/TEMARI"

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


def test_is_exam_assignment():
    """Valida la detección de exámenes y pruebas frente a tareas estándar."""
    from src.services.canvas_service import CanvasService

    assert CanvasService.is_exam_assignment("Examen Parcial T1-2-3", quiz_id=123, is_quiz_assignment=True) is True
    assert CanvasService.is_exam_assignment("Parcial Tema 1-2-3", quiz_id=456) is True
    assert CanvasService.is_exam_assignment("Control 1 de Álgebra") is True
    assert CanvasService.is_exam_assignment("Avaluació contínua tema 2") is True
    assert CanvasService.is_exam_assignment("Examen Final Ordinario") is True

    assert CanvasService.is_exam_assignment("Tarea tema 1") is False
    assert CanvasService.is_exam_assignment("FP · w01a · PPG: primera sesión y diagnóstico") is False
    assert CanvasService.is_exam_assignment("Practica 1") is False
    assert CanvasService.is_exam_assignment("ENTREGA 1 (EJERCICIOS DE LA UNIDAD 1)") is False


def test_grade_formatting():
    """Valida el formateo de notas en formato 'nota/total' (ej: 10/10, 7/10, 9.5/10)."""
    from src.services.calendar_service import CalendarService

    a1 = CanvasAssignment(id=1, course_id=10, name="T1", score=10.0, points_possible=10.0, grade="10")
    assert CalendarService.get_grade_display(a1) == "10/10"

    a2 = CanvasAssignment(id=2, course_id=10, name="T2", score=7.0, points_possible=10.0, grade="7")
    assert CalendarService.get_grade_display(a2) == "7/10"

    a3 = CanvasAssignment(id=3, course_id=10, name="T3", score=9.5, points_possible=10.0, grade="9.5")
    assert CalendarService.get_grade_display(a3) == "9.5/10"

    a4 = CanvasAssignment(id=4, course_id=10, name="T4", score=9.95, points_possible=10.0, grade="9.95")
    assert CalendarService.get_grade_display(a4) == "9.95/10"

    a5_text_only = CanvasAssignment(id=6, course_id=10, name="T6", grade="complete", points_possible=0.0)
    assert CalendarService.get_grade_display(a5_text_only) == "complete"

    a6 = CanvasAssignment(id=7, course_id=10, name="T7", points_possible=10.0)
    assert CalendarService.get_grade_display(a6) is None


def test_is_course_match():
    """Valida la correspondencia exacta entre nombres de asignaturas y eventos de clases evitando falsos positivos."""
    from src.services.calendar_service import CalendarService

    assert CalendarService.is_course_match("FUNDAMENTS DE XARXES", "Fundaments de xarxes") is True
    assert CalendarService.is_course_match("FUNDAMENTS DE PROGRAMACIÓ", "Fundaments de programació") is True
    assert CalendarService.is_course_match("BASES DE L'INFORMÀTICA", "Bases de l'informàtica") is True
    assert CalendarService.is_course_match("ANALISIS MATEMÀTIC", "Analisis Matemàtic") is True
    assert CalendarService.is_course_match("ÀLGEBRA", "Àlgebra") is True

    assert CalendarService.is_course_match("FUNDAMENTS DE XARXES", "Fundaments de programació") is False
    assert CalendarService.is_course_match("FUNDAMENTS DE PROGRAMACIÓ", "Fundaments de xarxes") is False
    assert CalendarService.is_course_match("ÀLGEBRA", "Analisis Matemàtic") is False
