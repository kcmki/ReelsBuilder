FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY dashboard/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY dashboard/ /app/dashboard/

EXPOSE 8000
CMD ["uvicorn", "dashboard.main:app", "--host", "0.0.0.0", "--port", "8000"]
