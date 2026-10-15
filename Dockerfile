FROM python:3.11-slim

# Evitar buffers de stdout y escritura de archivos .pyc
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

WORKDIR /app

# Instalar dependencias del sistema mínimas si fueran necesarias
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Instalar dependencias de Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el código de la aplicación
COPY . .

# Puerto expuesto por defecto en Cloud Run
EXPOSE 8080

# Ejecutar con Uvicorn escuchando en el puerto asignado dinámicamente por Cloud Run
CMD ["sh", "-c", "uvicorn src.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
