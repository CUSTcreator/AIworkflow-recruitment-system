FROM python:3.11-slim

WORKDIR /app

ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

COPY importer/requirements.txt /app/importer/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --index-url "${PIP_INDEX_URL}" -r /app/importer/requirements.txt

COPY importer /app/importer
COPY config/importer.yml /app/config/importer.yml

ENV PYTHONPATH=/app
ENV IMPORTER_CONFIG_PATH=/app/config/importer.yml

EXPOSE 8010

CMD ["uvicorn", "importer.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8010"]
