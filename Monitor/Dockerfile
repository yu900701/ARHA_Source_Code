FROM python:3.10.13-slim
WORKDIR /app
COPY monitor.py .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir fastapi uvicorn requests kubernetes httpx
RUN mkdir -p /logdir
CMD ["python3", "monitor.py"]