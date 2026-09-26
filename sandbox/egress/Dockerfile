FROM python:3.12-alpine

WORKDIR /app
COPY proxy.py /app/proxy.py
COPY relay.py /app/relay.py
USER 65534:65534
EXPOSE 8888
CMD ["python", "-u", "/app/proxy.py"]
