# La máquina de ejecución. Sin clave de modelo, sin prompts y sin scripts:
# el contenedor no puede llamar a un LLM aunque quisiera (A3.7).
FROM python:3.12-slim

WORKDIR /app

# Las dependencias primero, para que la capa se reutilice mientras no cambien.
COPY requirements.txt ./
RUN pip install --no-cache-dir --require-hashes=false -r requirements.txt \
 && pip uninstall -y pytest httpx || true

# Lo único que entra: el código del servicio y los tres ficheros de datos.
COPY src/ ./src/
COPY data/ ./data/

# Lo que NO entra, y es una decisión de seguridad, no de tamaño:
#   prompts/   el criterio de clasificación
#   scripts/   lo que haría la llamada al modelo
#   tests/     no hace falta en ejecución
# Las dos credenciales llegan como variables de entorno desde Fly Secrets:
#   CATALOG_API_KEY  ·  DIAGNOSTICS_API_KEY

EXPOSE 8080
CMD ["python", "-m", "uvicorn", "api:app", "--app-dir", "src", "--host", "0.0.0.0", "--port", "8080"]
