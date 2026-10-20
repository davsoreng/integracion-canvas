import os
import asyncio
from contextlib import asynccontextmanager
from typing import Optional
from fastapi import FastAPI, HTTPException, Security, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security.api_key import APIKeyHeader, APIKeyQuery
import uvicorn

from config.settings import settings
from src.core.logger import logger
from src.models.schemas import SyncResponse
from src.services.sync_manager import SyncManager

_sync_lock = asyncio.Lock()


async def run_sync_in_background():
    """Ejecuta un ciclo de sincronización en segundo plano de manera segura evitando solapamientos."""
    if _sync_lock.locked():
        logger.warning("⚠️ Ya hay una sincronización en curso. Se omite este ciclo automático.")
        return
    async with _sync_lock:
        try:
            logger.info("⏰ [AUTOMATIZACIÓN] Iniciando ciclo de sincronización en segundo plano...")
            await asyncio.to_thread(SyncManager().run_sync)
        except Exception as e:
            logger.error(f"❌ [AUTOMATIZACIÓN] Error en ciclo de sincronización automático: {e}")


async def sync_scheduler_worker():
    """Bucle periódico en segundo plano para sincronizar Canvas automáticamente."""
    if settings.AUTO_SYNC_ON_STARTUP:
        logger.info("🚀 Sincronización automática de inicio programada en 5 segundos...")
        await asyncio.sleep(5)
        await run_sync_in_background()

    interval_seconds = max(60, settings.AUTO_SYNC_INTERVAL_MINUTES * 60)
    while True:
        await asyncio.sleep(interval_seconds)
        await run_sync_in_background()


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler_task = None
    if settings.AUTO_SYNC_INTERVAL_MINUTES > 0:
        logger.info(f"🚀 Planificador automático activo (intervalo: cada {settings.AUTO_SYNC_INTERVAL_MINUTES} minutos).")
        scheduler_task = asyncio.create_task(sync_scheduler_worker())
    yield
    if scheduler_task:
        scheduler_task.cancel()
        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="Asistente Académico Canvas -> Google Cloud",
    description="Sincronizador automático de Canvas LMS con Google Drive y Google Calendar",
    version="1.0.0",
    lifespan=lifespan
)

# Esquemas de seguridad opcionales
api_key_header = APIKeyHeader(name="X-Sync-Token", auto_error=False)
api_key_query = APIKeyQuery(name="token", auto_error=False)


def verify_sync_token(
    request: Request,
    token_header: Optional[str] = Security(api_key_header),
    token_param: Optional[str] = Security(api_key_query)
) -> bool:
    """Verifica el token de seguridad si está configurado en SYNC_SECRET_TOKEN."""
    client_host = request.client.host if request.client else ""
    if client_host in ("127.0.0.1", "::1", "localhost"):
        return True

    if not settings.SYNC_SECRET_TOKEN:
        return True

    header_val = token_header if isinstance(token_header, str) else None
    param_val = token_param if isinstance(token_param, str) else None
    provided_token = header_val or param_val

    if not provided_token or provided_token != settings.SYNC_SECRET_TOKEN:
        raise HTTPException(
            status_code=403,
            detail="Token de autorización inválido o no proporcionado (use ?token=... o cabecera X-Sync-Token)."
        )
    return True


@app.get("/")
def root():
    """Ruta raíz informativa."""
    manual_url = "/sync/manual" + (f"?token={settings.SYNC_SECRET_TOKEN}" if settings.SYNC_SECRET_TOKEN else "")
    return {
        "service": "Asistente Académico Canvas",
        "status": "online",
        "canvas_configured": settings.is_canvas_configured(),
        "google_configured": settings.is_google_configured(),
        "auto_sync_interval_minutes": settings.AUTO_SYNC_INTERVAL_MINUTES,
        "endpoints": {
            "health": "/health",
            "trigger_sync_post": "/sync",
            "trigger_sync_manual_get": manual_url
        }
    }


@app.get("/health")
def health_check():
    """Comprobación de salud para Cloud Run."""
    return {
        "status": "healthy",
        "canvas_configured": settings.is_canvas_configured(),
        "google_configured": settings.is_google_configured()
    }


@app.post("/sync", response_model=SyncResponse)
def trigger_sync(authorized: bool = Security(verify_sync_token)):
    """
    Endpoint principal de sincronización (usado habitualmente por Cloud Scheduler).
    Ejecuta el ciclo Canvas -> Google Drive y Google Calendar.
    """
    manager = SyncManager()
    response = manager.run_sync()
    if not response.success and response.stats.courses_processed == 0:
        raise HTTPException(status_code=500, detail=response.message)
    return response


@app.get("/sync/manual", response_class=HTMLResponse)
def trigger_sync_manual(request: Request, authorized: bool = Security(verify_sync_token)):
    """
    Endpoint amigable para disparar la sincronización desde el navegador de un móvil o PC
    mediante un marcador o acceso directo. Devuelve un resumen visual en HTML.
    """
    manager = SyncManager()
    result = manager.run_sync()

    status_icon = "✅" if result.success else "⚠️"
    status_color = "#2e7d32" if result.success else "#c62828"

    html_content = f"""
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Sincronización Canvas</title>
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
                background-color: #f4f6f8;
                color: #333;
                display: flex;
                justify-content: center;
                align-items: center;
                min-height: 100vh;
                margin: 0;
                padding: 16px;
            }}
            .card {{
                background: white;
                border-radius: 12px;
                padding: 24px;
                max-width: 480px;
                width: 100%;
                box-shadow: 0 4px 16px rgba(0,0,0,0.08);
            }}
            h1 {{
                font-size: 1.4rem;
                margin-top: 0;
                display: flex;
                align-items: center;
                gap: 8px;
                color: {status_color};
            }}
            .metric {{
                display: flex;
                justify-content: space-between;
                padding: 10px 0;
                border-bottom: 1px solid #eee;
            }}
            .metric:last-child {{
                border-bottom: none;
            }}
            .badge {{
                background: #e8f0fe;
                color: #1967d2;
                padding: 3px 8px;
                border-radius: 6px;
                font-weight: 600;
            }}
            .btn {{
                display: block;
                text-align: center;
                background: #1a73e8;
                color: white;
                text-decoration: none;
                padding: 12px;
                border-radius: 8px;
                font-weight: 500;
                margin-top: 20px;
            }}
        </style>
    </head>
    <body>
        <div class="card">
            <h1>{status_icon} {result.message}</h1>
            <p><strong>Fecha:</strong> {result.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}</p>
            <div class="metric">
                <span>Cursos procesados:</span>
                <span class="badge">{result.stats.courses_processed}</span>
            </div>
            <div class="metric">
                <span>Archivos nuevos/actualizados en Drive:</span>
                <span class="badge">{result.stats.files_uploaded}</span>
            </div>
            <div class="metric">
                <span>Archivos ya existentes (omitidos):</span>
                <span class="badge">{result.stats.files_skipped}</span>
            </div>
            <div class="metric">
                <span>Eventos creados en Calendar:</span>
                <span class="badge">{result.stats.calendar_events_created}</span>
            </div>
            <div class="metric">
                <span>Eventos actualizados (notas/entregas):</span>
                <span class="badge">{result.stats.calendar_events_updated}</span>
            </div>
            <div class="metric">
                <span>Exámenes vinculados a clases:</span>
                <span class="badge">{result.stats.exams_linked}</span>
            </div>

            {"<details style='margin-top:16px;'><summary style='cursor:pointer;font-weight:600;color:#1a73e8;'>📄 Ver detalles de archivos subidos (" + str(len(result.stats.files_uploaded_list)) + ")</summary><ul style='font-size:0.85rem;padding-left:20px;color:#555;'>" + "".join(f"<li>{item}</li>" for item in result.stats.files_uploaded_list) + "</ul></details>" if result.stats.files_uploaded_list else ""}
            {"<details style='margin-top:10px;'><summary style='cursor:pointer;font-weight:600;color:#1a73e8;'>🎓 Ver exámenes vinculados (" + str(len(result.stats.exams_linked_list)) + ")</summary><ul style='font-size:0.85rem;padding-left:20px;color:#555;'>" + "".join(f"<li>{item}</li>" for item in result.stats.exams_linked_list) + "</ul></details>" if result.stats.exams_linked_list else ""}
            {"<details style='margin-top:10px;'><summary style='cursor:pointer;font-weight:600;color:#1a73e8;'>🔄 Ver eventos actualizados con notas (" + str(len(result.stats.calendar_events_updated_list)) + ")</summary><ul style='font-size:0.85rem;padding-left:20px;color:#555;'>" + "".join(f"<li>{item}</li>" for item in result.stats.calendar_events_updated_list) + "</ul></details>" if result.stats.calendar_events_updated_list else ""}
            {"<details style='margin-top:10px;'><summary style='cursor:pointer;font-weight:600;color:#1a73e8;'>📅 Ver eventos creados (" + str(len(result.stats.calendar_events_created_list)) + ")</summary><ul style='font-size:0.85rem;padding-left:20px;color:#555;'>" + "".join(f"<li>{item}</li>" for item in result.stats.calendar_events_created_list) + "</ul></details>" if result.stats.calendar_events_created_list else ""}

            <a class="btn" href="{request.url}">🔄 Sincronizar de nuevo</a>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", settings.PORT))
    uvicorn.run("src.main:app", host="0.0.0.0", port=port, reload=True)
