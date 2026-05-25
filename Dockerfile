FROM python:3.12-slim

# ── Single-container image: uvicorn (backend) + nginx (frontend) ─────────────

RUN apt-get update \
    && apt-get install -y --no-install-recommends nginx wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Backend
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app/ ./app/

# Frontend static assets
COPY frontend/index.html        /usr/share/nginx/html/index.html
COPY frontend/styles.css        /usr/share/nginx/html/styles.css
COPY frontend/app.jsx           /usr/share/nginx/html/app.jsx
COPY frontend/components.jsx    /usr/share/nginx/html/components.jsx
COPY frontend/tweaks-panel.jsx  /usr/share/nginx/html/tweaks-panel.jsx

# Bundle vendor JS locally — no CDN dependency at runtime
RUN wget -q -O /usr/share/nginx/html/vendor-react.js \
        https://unpkg.com/react@18.3.1/umd/react.development.js \
    && wget -q -O /usr/share/nginx/html/vendor-react-dom.js \
        https://unpkg.com/react-dom@18.3.1/umd/react-dom.development.js \
    && wget -q -O /usr/share/nginx/html/vendor-babel.js \
        https://unpkg.com/@babel/standalone@7.29.0/babel.min.js \
    && wget -q -O /usr/share/nginx/html/vendor-leaflet.js \
        https://unpkg.com/leaflet@1.9.4/dist/leaflet.js \
    && wget -q -O /usr/share/nginx/html/vendor-leaflet.css \
        https://unpkg.com/leaflet@1.9.4/dist/leaflet.css

# nginx config (replace the default Debian site)
RUN rm -f /etc/nginx/sites-enabled/default /etc/nginx/sites-available/default
COPY nginx.conf /etc/nginx/conf.d/obd.conf

# Entrypoint runs both processes and dies if either exits
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV OBD_FILES_DIR=/data/obd \
    MYOP_FILES_DIR=/data/myop \
    DB_PATH=/data/db/trips.db \
    VEHICLE_NAME="Peugeot 308 SW" \
    VEHICLE_ECU="MD1CS003 — 1.5 BlueHDi" \
    VEHICLE_ADAPTER="BTLE IOS-Vlink"

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD wget -qO- http://localhost/api/v1/health || exit 1

CMD ["/entrypoint.sh"]
