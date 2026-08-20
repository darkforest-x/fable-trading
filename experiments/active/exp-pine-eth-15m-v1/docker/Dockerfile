FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/workspace \
    MPLCONFIGDIR=/tmp/matplotlib

WORKDIR /workspace
COPY requirements.txt /tmp/pine-eth15m-requirements.txt
RUN python -m pip install --no-cache-dir -r /tmp/pine-eth15m-requirements.txt

CMD ["python", "scripts/research_pine_eth_15m.py", "--output", "/output"]
