import pytest
from datetime import datetime, timezone
from src.models.schemas import CanvasCourse, CanvasFile, CanvasAssignment, SyncStats
from src.services.drive_service import DriveService
from fastapi.testclient import TestClient
from src.main import app


def test_canvas_models():
    """Valida la instanciación correcta de modelos de datos."""
    course = CanvasCourse(id=101, name="Matemáticas I", course_code="MAT101")
    assert course.id == 101
    assert course.name == "Matemáticas I"

    file = CanvasFile(
        id=202,
        display_name="Tema 1 - Álgebra.pdf",
        filename="tema1.pdf",
        url="https://canvas.test/files/202/download",
        size=1024,
        updated_at="2026-10-15T09:00:00Z"
    )
    assert file.id == 202
    assert file.display_name.endswith(".pdf")

    assignment = CanvasAssignment(
        id=303,
        course_id=101,
        name="Entrega Práctica 1",
        due_at=datetime(2026, 11, 1, 23, 59, tzinfo=timezone.utc),
        points_possible=10.0
    )
    assert assignment.id == 303
    assert assignment.due_at is not None


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
