FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
ENV DATA_DIR=/data

# Railway-তে /data-কে Volume হিসেবে mount করলে Telegram session,
# on/off state এবং logs restart-এর পরও থাকবে।
CMD ["python", "run_all.py"]
