FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt \
    && addgroup --system --gid 10001 xiaowo \
    && adduser --system --uid 10001 --ingroup xiaowo --home /nonexistent --no-create-home xiaowo
COPY __init__.py app.py verify_runtime.py /app/

USER 10001:10001
EXPOSE 11235
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "11235", "--no-access-log"]
