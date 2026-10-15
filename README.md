# Asistente Académico Automático (Canvas → Google Drive/Calendar)

Servicio Serverless diseñado para sincronizar automáticamente cursos, archivos (PDFs) y tareas de Canvas LMS con **Google Drive** y **Google Calendar**, manteniéndose 100% dentro de la **capa gratuita de Google Cloud**.

---

## 🚀 Características de la Fase 1 (MVP)
- **Sincronización con Google Drive**: Descarga y organiza materiales de estudio en carpetas por asignatura (`Canvas_Academico/[Asignatura]/...`).
- **Sincronización con Google Calendar**: Crea eventos con recordatorios para fechas de entrega y exámenes.
- **Idempotencia nativa**: No duplica archivos ni eventos si se ejecuta múltiples veces gracias al uso de metadatos (`appProperties` en Drive y `extendedProperties` en Calendar).
- **Modo híbrido**: Disparo automático periódico y soporte para disparos manuales bajo demanda desde un navegador móvil o de escritorio (`/sync/manual`).

---

## 🛠️ Requisitos Previos
1. Python 3.11 o superior.
2. Token de acceso personal de Canvas LMS.
3. Proyecto en Google Cloud con las APIs de Google Drive y Google Calendar habilitadas, y credenciales OAuth 2.0 (App de escritorio).

---

## 📦 Instalación y Configuración Local

### 1. Clonar el repositorio y crear entorno virtual
```bash
python -m venv .venv
# En Windows (PowerShell):
.venv\Scripts\Activate.ps1
# En Linux/Mac:
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Configurar variables de entorno
Copia la plantilla `.env.example` a `.env`:
```bash
cp .env.example .env
```
Edita `.env` con:
- `CANVAS_BASE_URL`: URL de tu campus Canvas (ej: `https://tu-universidad.instructure.com`).
- `CANVAS_API_TOKEN`: Tu token personal de Canvas.
- `GOOGLE_CLIENT_ID` y `GOOGLE_CLIENT_SECRET`: Obtenidos de tu consola de GCP.

### 3. Autorizar tu cuenta de Google (Una sola vez)
Ejecuta el asistente interactivo:
```bash
python scripts/auth_setup.py
```
Se abrirá tu navegador para iniciar sesión con tu cuenta de Google (aprovechando tus 2 TB de Google One). El script guardará automáticamente el `GOOGLE_REFRESH_TOKEN` en tu `.env`.

---

## 🧪 Pruebas Locales
Para verificar el funcionamiento y modelos sin llamadas externas:
```bash
pytest tests/
```

Para arrancar el servidor localmente:
```bash
python src/main.py
```
Accede a `http://localhost:8080` o `http://localhost:8080/sync/manual` para probar una sincronización.

---

## ☁️ Despliegue en Google Cloud (Cloud Run)

### 1. Construir y desplegar con Google Cloud SDK:
```bash
gcloud run deploy asistente-canvas \
  --source . \
  --platform managed \
  --region europe-west1 \
  --allow-unauthenticated \
  --set-env-vars CANVAS_BASE_URL="https://tu-universidad.instructure.com",DRIVE_ROOT_FOLDER_NAME="Canvas_Academico"
```

### 2. Programar con Cloud Scheduler (Sondeo periódico cada 30 minutos):
```bash
gcloud scheduler jobs create http sync-canvas-job \
  --location europe-west1 \
  --schedule "*/30 8-20 * * 1-5" \
  --uri "https://<TU-URL-DE-CLOUD-RUN>/sync" \
  --http-method POST
```
*(Esta regla sincroniza cada 30 min de lunes a viernes entre las 8:00 y las 20:00, consumiendo menos del 0,1% de la capa gratuita).*
