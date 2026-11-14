# Guía de Contexto para Agentes (`agents.md`)

Este documento define el contexto, propósito y principios fundamentales del proyecto para cualquier agente de IA o colaborador que intervenga en el código.

---

## 1. Naturaleza del Proyecto
- **Proyecto personal de código abierto (Open Source)**: Nació para resolver una necesidad personal real de organización académica universitaria, pero el repositorio es **público en GitHub** y está disponible para cualquier persona.
- **Reutilizable y agnóstico de usuario**: El código no debe depender de nombres de asignaturas, rutas, identificadores de carpetas o calendarios codificados de forma rígida (*hardcoded*) de una única persona o universidad. Todo dato particular del usuario se inyecta mediante variables de entorno o **GitHub Secrets**.
- **Principio de Limpieza Minimalista**: En el repositorio solo deben existir los archivos estrictamente indispensables para la ejecución del sincronizador, sus pruebas y el flujo de GitHub Actions. No se mantienen servidores web innecesarios, configuraciones de plataformas descartadas (como Google Cloud Run / Docker) ni scripts auxiliares obsoletos.

---

## 2. Seguridad y Privacidad Estricta (Repositorio y Logs Públicos)
Dado que el repositorio y las ejecuciones de **GitHub Actions** son públicos, la seguridad tiene **máxima prioridad**:

- **Archivos permitidos en el repositorio**:
  - Código fuente (`src/`, `config/`).
  - Definición de dependencias (`requirements.txt`).
  - Flujo de automatización (`.github/workflows/sync.yml`).
  - Plantilla de configuración limpia (`.env.example`).
  - Pruebas unitarias (`tests/`) con datos simulados (*mocks*).
  - Documentación (`README.md`, `agents.md`).
- **Cero secretos en el código**:
  - Prohibido incluir tokens (`CANVAS_API_TOKEN`, `GEMINI_API_KEY`, JSON de cuentas de servicio, `GOOGLE_REFRESH_TOKEN`) o identificadores reales (`DRIVE_ROOT_FOLDER_ID`, URLs privadas de instituciones) en el código fuente.
- **Cero filtraciones en los logs de ejecución (GitHub Actions)**:
  - En un repositorio público, cualquier persona puede abrir la pestaña *Actions* y leer la salida de consola (`stdout`/`stderr`).
  - El sistema de logging debe aplicar un **filtro de sanitización automática** que enmascare cualquier token, clave, URL firmada o identificador de carpeta/calendario antes de imprimir en consola.
  - Cuando se ejecuta en entorno público (`GITHUB_ACTIONS=true` o `PUBLIC_LOG_MODE=true`), los logs nunca deben exponer URLs de descarga, IDs internos de Google Drive/Calendar ni datos privados sensibles.
- **Principio de Mínimo Privilegio en Google Workspace**:
  - Se prioriza el uso de **Cuentas de Servicio de Google (`Service Account`)** de forma desatendida (sin tokens temporales que caduquen cada 7 días), compartiendo con la cuenta de servicio **únicamente** la carpeta raíz de estudio en Google Drive y los calendarios específicos en Google Calendar.

---

## 3. Contexto y Propósito Final
### ¿De qué va el proyecto?
Es un **asistente de sincronización académica automatizado** que conecta **Canvas LMS (by Instructure)** con **Google Workspace (Google Drive y Google Calendar)** apoyándose opcionalmente en IA (Gemini) para clasificación semántica.

### ¿Cuál es su propósito final?
Ayudar a cualquier persona que utilice **Canvas by Instructure** y **Google Workspace** a mantener su entorno de estudio organizado de forma desatendida y segura:

1. **Google Drive como fuente de verdad (*Source of Truth*)**:
   - Respeta al 100 % la estructura de carpetas e idioma que el usuario haya creado en su Google Drive.
   - Nunca borra ni sobrescribe documentos propios del estudiante.
   - **Verificación exhaustiva en toda la asignatura**: Antes de descargar o subir cualquier archivo de Canvas, comprueba siempre si el documento ya existe dentro de la carpeta de la asignatura —tanto en su **propia raíz** como en **cualquiera de sus subcarpetas** (en todos los niveles)—, por ID de Canvas y por nombre normalizado, evitando duplicidades incluso si el usuario ha movido o reorganizado los archivos manualmente.
   - Clasifica automáticamente nuevos materiales (apuntes, presentaciones, ejercicios, pizarras) en las subcarpetas correspondientes de cada asignatura.

2. **Google Calendar sincronizado y vivo**:
   - Crea y actualiza eventos de entregas y tareas con códigos de color según su estado (pendiente, entregado, calificado) e incluye la calificación obtenida cuando el profesor corrige la tarea.
   - Detecta cambios realizados por el profesor (aplazamientos de fecha límite, cambios de título o descripción).
   - Identifica exámenes o pruebas evaluables y los vincula al horario de clases del estudiante.

3. **Ejecución automatizada en GitHub Actions + Disparo desde el móvil sin panel web**:
   - Se ejecuta de forma periódica mediante programación (`cron`) en **GitHub Actions**.
   - Permite lanzar una sincronización manual bajo demanda directamente desde la **app móvil de GitHub** (o mediante un atajo nativo del móvil vía API de GitHub `workflow_dispatch`) sin necesidad de mantener ni exponer ningún servidor o panel web.
