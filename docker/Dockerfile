FROM python:3.11-slim

# libxml2/libxslt são necessárias para compilar/rodar lxml corretamente
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libxml2-dev \
    libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copia só o requirements.txt primeiro para aproveitar cache de layers do Docker
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o restante do projeto (src/, config/, db/ etc.)
# main.py resolve paths relativos à raiz do projeto (BASE_DIR),
# então o WORKDIR precisa ser a raiz, não src/
COPY src/ ./src/
COPY config/ ./config/
COPY db/ ./db/

# logs/ é criado em runtime por setup_logging(), mas deixamos o diretório
# pronto para evitar problema de permissão dependendo do volume montado
RUN mkdir -p logs

CMD ["python", "src/main.py"]