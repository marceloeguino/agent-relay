FROM python:3.12-slim

# Install uv (fast Python package manager used by this project)
RUN pip install --no-cache-dir uv==0.8.17

WORKDIR /app

# Install dependencies first (separate layer, cached across code-only changes)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Now copy the application code
COPY main.py database.py dashboard.py dashboard.html errors.py schemas.py storage.py worker.py ./

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

# --host 0.0.0.0 is required: uvicorn defaults to 127.0.0.1, which is only
# reachable from inside the container -- with that default, `-p` publishing
# looks "broken" from the host even though the container is fine.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
