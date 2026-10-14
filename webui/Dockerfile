FROM image-studio-api:v1
WORKDIR /webui
COPY server.py ./
COPY static ./static
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/ready',timeout=4)" || exit 1
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-access-log"]
