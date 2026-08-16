# The execution machine. No model key, no prompts and no scripts: the container
# cannot call an LLM even if it wanted to (A3.7).
FROM python:3.12-slim

WORKDIR /app

# Dependencies first, so the layer is reused while they do not change.
# No `|| true` here on purpose: a real installation failure must break the build.
# Swallowing it produced an image that started with no dependencies at all and
# died at run time with an error that never mentioned pip.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
 && pip uninstall -y pytest httpx

# The only thing that goes in: the service code and the data files.
COPY src/ ./src/
COPY data/ ./data/

# What does NOT go in, and it is a security decision, not a size one:
#   prompts/   the classification criterion
#   scripts/   what would make the call to the model
#   tests/     not needed at run time
# The two credentials arrive as environment variables from Fly Secrets:
#   CATALOG_API_KEY  ·  DIAGNOSTICS_API_KEY

EXPOSE 8080
CMD ["python", "-m", "uvicorn", "api:app", "--app-dir", "src", "--host", "0.0.0.0", "--port", "8080"]
