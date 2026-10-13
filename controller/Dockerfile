FROM docker:cli AS cli
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends openssl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=cli /usr/local/bin/docker /usr/local/bin/docker
WORKDIR /app
COPY controller/ /app/
ENV PYTHONUNBUFFERED=1
CMD ["python", "main.py"]
