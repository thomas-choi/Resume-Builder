FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
# fund_models: vendored FUND skills mechanism (imported by src.agents.skills).
# skills: per-agent SKILL.md reasoning — prompt content, not secrets; the agents
# fall back to inline scaffolding if absent, so these must ship in the image.
COPY fund_models/ fund_models/
COPY skills/ skills/

EXPOSE 8000
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
