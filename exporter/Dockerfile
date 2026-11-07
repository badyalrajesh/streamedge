# Built on the Varnish image so that varnishstat is available
FROM varnish:7.5
USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 \
 && rm -rf /var/lib/apt/lists/*
COPY exporter.py /app/exporter.py
ENTRYPOINT ["python3", "-u", "/app/exporter.py"]
