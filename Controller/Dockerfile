FROM python:3.10.13-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt
COPY controller.py ./ 
COPY service_yaml ./service_yaml
RUN mkdir -p /information
RUN mkdir -p /logdir
CMD ["uvicorn", "controller:app", "--host", "0.0.0.0", "--port", "5000"]