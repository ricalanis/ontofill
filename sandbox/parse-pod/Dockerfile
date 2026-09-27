FROM python:3.12-slim

RUN pip install --no-cache-dir beautifulsoup4==4.15.0 openpyxl==3.1.5 pypdf==6.19.0 xlrd==2.0.2 \
    && useradd --create-home --uid 1000 parsepod
WORKDIR /app
COPY runner.py /app/runner.py
COPY profiler /app/profiler
USER 1000:1000
CMD ["python", "/app/runner.py"]
