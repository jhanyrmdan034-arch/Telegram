# Custom MTProto backend — one-command deploy on any VPS (e.g. Iranian hosting).
#
#   docker build -t mtserver .
#   docker run -d --name mtserver -p 8443:8443 -p 8080:8080 \
#       -e PUBLIC_HOST=chat.example.ir -e DEV_CODE=12345 mtserver
#
# Then paste the printed CustomBackendConfig.h values into the Android app
# (see BUILD-APK-FA.md) and build the APK.
FROM python:3.11-slim
WORKDIR /app
COPY mtserver/ mtserver/
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
EXPOSE 8443 8080
# DEV_CODE set => TEST MODE: any login code is accepted (for the test version).
# Remove -e DEV_CODE=... for production (real SMS codes will be printed in logs).
CMD sh -c "python3 -m mtserver.main \
    --listen-host 0.0.0.0 --listen-port 8443 \
    --public-host \${PUBLIC_HOST:-127.0.0.1} --public-port \${PUBLIC_PORT:-8443} \
    --dc-id \${DC_ID:-1} --dev-code \${DEV_CODE:-12345} \
    --http-status-port \${HTTP_STATUS_PORT:-8080}"
