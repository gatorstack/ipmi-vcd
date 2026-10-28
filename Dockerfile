FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir --no-deps . && \
    useradd --system --uid 65532 --home /nonexistent --shell /usr/sbin/nologin ipmi-vcd

USER 65532:65532
EXPOSE 8080/tcp
ENTRYPOINT ["python", "-m", "ipmi_vcd"]

