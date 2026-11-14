# Asistente Académico Automático (Canvas LMS → Google Workspace)

Sincronizador desatendido de código abierto que conecta **Canvas LMS (by Instructure)** con **Google Drive** y **Google Calendar**, diseñado para ejecutarse de forma gratuita y segura mediante **GitHub Actions** (o en local).

---

## ✨ Características Principales
- **Google Drive como Fuente de Verdad (*Source of Truth*)**:
  - Respeta al 100 % la estructura de carpetas e idioma que hayas creado en tu Google Drive.
  - **Detección recursiva antiduplicados**: Antes de descargar o subir cualquier documento de Canvas, escanea tanto la **raíz de la asignatura** como **todas sus subcarpetas** (por ID y por nombre normalizado). Si el archivo ya existe en cualquier parte de la asignatura, lo omite sin duplicarlo ni moverlo.
  - Clasifica automáticamente nuevos materiales (apuntes, presentaciones, ejercicios, pizarras) en las subcarpetas correspondientes apoyándose opcionalmente en **Gemini AI**.
- **Google Calendar Vivo**:
  - Crea eventos de entregas y tareas con código de colores según su estado (Amarillo = Pendiente, Verde = Entregado/Calificado) e incorpora la nota obtenida (`10/10`, `8.5/10`) cuando el profesor corrige.
  - Vincula automáticamente los exámenes a las sesiones de tu calendario de clases (`| EXAMEN` en color morado).
- **Seguridad y Privacidad para Repositorios Públicos**:
  - **Cero secretos en código ni en logs**: Incluye un filtro sanitizador (`SecretScrubberFilter`) y un modo de privacidad (`PUBLIC_LOG_MODE=true` activo por defecto en GitHub Actions) que impide que se impriman tokens, IDs, URLs o datos sensibles en la consola pública de GitHub Actions.
  - **Principio de Mínimo Privilegio**: Soporte nativo para **Cuentas de Servicio de Google (`Service Account`)**, evitando tokens temporales de 7 días y limitando el acceso exclusivamente a la carpeta y calendarios que compartas con el asistente.

---

## 🔐 Configuración en GitHub Actions (100 % Gratuito y Desatendido)

### 1. Configurar Cuenta de Servicio de Google (Sin caducidad)
1. En [Google Cloud Console](https://console.cloud.google.com/), habilita **Google Drive API** y **Google Calendar API**.
2. Ve a **IAM y administración → Cuentas de servicio** y crea una cuenta de servicio (ej. `asistente-canvas`).
3. En la pestaña **Claves**, crea una clave nueva en formato **JSON** y descárgala.
4. **Comparte únicamente lo necesario** con el correo de esa cuenta de servicio (`...@...iam.gserviceaccount.com`):
   - En **Google Drive**: Comparte tu carpeta raíz de estudio (ej. `INGENIERIA`) con permiso de **Editor**.
   - En **Google Calendar**: En la configuración de tu calendario de entregas y de tu calendario de clases, compártelos con el correo de la cuenta de servicio dándole permiso de **Hacer cambios en eventos** y copia sus **ID de calendario** (`CALENDAR_ID` y `CLASSES_CALENDAR_ID`).
   *(Nota: Si utilizas una cuenta `@gmail.com` personal y Google Drive restringe la cuota de subida de la Cuenta de Servicio, puedes usar igualmente `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` y `GOOGLE_REFRESH_TOKEN` pasando tu pantalla de consentimiento OAuth a estado **"En producción"** para que el token no caduque nunca).*

### 2. Añadir los Secretos en GitHub
En tu repositorio de GitHub, ve a **Settings → Secrets and variables → Actions → New repository secret** y añade:

| Secreto | Descripción |
| :--- | :--- |
| `CANVAS_BASE_URL` | URL de tu institución (ej. `https://tu-universidad.instructure.com`) |
| `CANVAS_API_TOKEN` | Token personal generado en tu perfil de Canvas |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Contenido completo del archivo JSON de la cuenta de servicio |
| `DRIVE_ROOT_FOLDER_ID` | ID de tu carpeta raíz en Google Drive |
| `CALENDAR_ID` | ID del calendario de entregas en Google Calendar |
| `CLASSES_CALENDAR_ID` | ID del calendario de clases en Google Calendar |
| `GEMINI_API_KEY` | *(Opcional)* Clave de API de Google AI Studio (Gemini) |

---

## 📱 Cómo lanzar una sincronización manual desde el móvil (Sin panel web)

Tienes dos formas directas de disparar la sincronización desde tu teléfono sin exponer ningún servidor web:

1. **Desde la App Oficial de GitHub (iOS / Android)**:
   - Entra en tu repositorio → toca en **Actions** → selecciona **Sincronización Académica Canvas** → pulsa el botón **Run workflow**.
2. **Con un botón directo en la pantalla de inicio (Atajos de iOS / HTTP Shortcuts en Android)**:
   - Crea un atajo que realice una petición `POST` a:
     `https://api.github.com/repos/<TU_USUARIO>/<TU_REPO>/actions/workflows/sync.yml/dispatches`
   - Cabeceras:
     - `Accept: application/vnd.github+json`
     - `Authorization: Bearer <TU_GITHUB_FINE_GRAINED_TOKEN>` *(Token de GitHub con permiso exclusivo de lectura/escritura en `Actions` para este repositorio)*.
   - Cuerpo JSON: `{"ref": "main"}`.

---

## 🧪 Ejecución y Pruebas en Local

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Ejecutar suite de tests unitarios
pytest tests/

# Ejecutar sincronización local (leyendo de .env)
python -m src.main
```
