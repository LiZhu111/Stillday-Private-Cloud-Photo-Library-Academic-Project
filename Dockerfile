FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home appuser
COPY photo_library ./photo_library
COPY frontend ./frontend
RUN mkdir /data && chown appuser:appuser /data
USER appuser
ENV DATA_DIR=/data
EXPOSE 8080
CMD ["uvicorn", "photo_library.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]

