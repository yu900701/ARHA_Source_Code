FROM python:3.10.13-slim
WORKDIR /app
COPY result.py ./
RUN mkdir -p /information
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir fastapi uvicorn 
CMD ["uvicorn", "result:app", "--host", "0.0.0.0", "--port", "5001"]