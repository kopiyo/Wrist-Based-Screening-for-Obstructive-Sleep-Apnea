FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY apps/api/requirements.txt /app/requirements.txt
RUN pip install --upgrade pip==25.1.1 \
    && pip install --index-url https://download.pytorch.org/whl/cpu 'torch==2.9.1+cpu' \
    && grep -v '^torch==' /app/requirements.txt > /app/requirements-rest.txt \
    && pip install -r /app/requirements-rest.txt
COPY pipeline.py /app/pipeline.py
COPY model /app/model
COPY apps/api /app/apps/api
EXPOSE 8000
CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
