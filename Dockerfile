FROM python:3.11-slim

WORKDIR /app

# libreoffice-writer supplies the headless `soffice` used by
# src/tools/docx_renderer.convert_to_pdf to turn rendered .docx into PDF.
# Without it the API still returns .docx (PDF conversion degrades with a
# warning) — set RENDER_PDF=false to skip it deliberately.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libreoffice-writer \
    && rm -rf /var/lib/apt/lists/*

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
