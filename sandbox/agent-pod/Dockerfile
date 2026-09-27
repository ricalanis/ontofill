FROM mcr.microsoft.com/playwright/python:v1.55.0-noble

RUN pip install --no-cache-dir playwright==1.55.0
WORKDIR /app
COPY capture.py /app/capture.py
COPY cdp.py /app/cdp.py
COPY spider_policy.py /app/spider_policy.py
USER 1000:1000
CMD ["python", "/app/capture.py"]
