FROM python:3.11-slim

WORKDIR /app

ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

COPY backend/requirements.txt /app/backend/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --index-url "${PIP_INDEX_URL}" -r /app/backend/requirements.txt

COPY packages/recruitment_ai_core /app/packages/recruitment_ai_core
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-build-isolation --index-url "${PIP_INDEX_URL}" -e /app/packages/recruitment_ai_core

COPY backend /app/backend
COPY docker/backend-entrypoint.sh /app/docker/backend-entrypoint.sh

ENV PYTHONPATH=/app
EXPOSE 8000

ENTRYPOINT ["sh", "/app/docker/backend-entrypoint.sh"]
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
