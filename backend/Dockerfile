FROM node:22-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv python3-pip git docker.io ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN npm install -g @earendil-works/pi-coding-agent@1.1.0
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY app /app/app
WORKDIR /app
CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8000"]
