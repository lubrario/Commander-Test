FROM python:3.12-slim
WORKDIR /app
COPY app.py index.html styles.css app.js favicon.svg ./
ENV PYTHONUNBUFFERED=1 HOST=0.0.0.0 PORT=10000 TABERNA_DB=/tmp/taberna_inventory.sqlite3
EXPOSE 10000
CMD ["python", "app.py"]
