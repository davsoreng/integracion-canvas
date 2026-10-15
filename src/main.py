import os
from typing import Optional
from fastapi import FastAPI, HTTPException, Security, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security.api_key import APIKeyHeader, APIKeyQuery
import uvicorn

from config.settings import settings
from src.core.logger import logger
from src.models.schemas import SyncResponse
from src.services.sync_manager import SyncManager

app = FastAPI(
    title="Asistente Académico Canvas -> Google Cloud",
    description="Sincronizador automático de Canvas LMS con Google Drive y Google Calendar",
    version="1.0.0"
)

# Esquemas de seguridad opcionales
api_key_header = APIKeyHeader(name="X-Sync-Token", auto_error=False)
api_key_query = APIKeyQuery(name="token", auto_error=False)


def verify_sync_token(
    token_header: Optional[str] = Security(api_key_header),
    token_param: Optional[str] = Security(api_key_query)
) -> bool:
    """Verifica el token de seguridad si está configurado en SYNC_SECRET_TOKEN."""
    if not settings.SYNC_SECRET_TOKEN:
        return True  # Si no hay token configurado, permitir ejecución libre (útil en dev o con OIDC de GCP)

    provided_token = token_header or token_param
    if not provided_token or provided_token != settings.SYNC_SECRET_TOKEN:
        raise HTTPException(
            status_code=403,
            detail="Token de autorización inválido o no proporcionado (use ?token=... o cabecera X-Sync-Token)."
        )
    return True


@app.get("/")
def root():
    """Ruta raíz informativa."""
    return {
        "service": "Asistente Académico Canvas",
        "status": "online",
        "canvas_configured": settings.is_canvas_configured(),
        "google_configured": settings.is_google_configured(),
        "endpoints": {
            "health": "/health",
            "trigger_sync_post": "/sync",
            "trigger_sync_manual_get": "/sync/manual?token=..."
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
def trigger_sync_manual(request: Request, token: Optional[str] = Query(None)):
    """
    Endpoint amigable para disparar la sincronización desde el navegador de un móvil o PC
    mediante un marcador o acceso directo. Devuelve un resumen visual en HTML.
    """
    verify_sync_token(token_param=token)

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
                <span>Eventos actualizados:</span>
                <span class="badge">{result.stats.calendar_events_updated}</span>
            </div>
            <a class="btn" href="{request.url}">🔄 Sincronizar de nuevo</a>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", settings.PORT))
    uvicorn.run("src.main:app", host="0.0.0.0", port=port, reload=True)
