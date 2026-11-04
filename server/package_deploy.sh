#!/usr/bin/env bash
# Creates mtserver-deploy.tar.gz — everything needed to run the backend on a VPS
# (no db, no keys, no uploaded files inside).
#
#   bash server/package_deploy.sh            # -> server/../mtserver-deploy.tar.gz
#   scp mtserver-deploy.tar.gz user@vps-ip:/opt/
#   ssh user@vps-ip 'mkdir -p /opt/mtserver && tar xzf /opt/mtserver-deploy.tar.gz -C /opt/mtserver'
set -eu
cd "$(dirname "$0")"
OUT="${1:-../mtserver-deploy.tar.gz}"
tar czf "$OUT" \
    --exclude='__pycache__' --exclude='*.pyc' \
    --exclude='mtserver.db' --exclude='server_key.pem' --exclude='files' \
    mtserver requirements.txt run_demo.sh Dockerfile
echo "created: $OUT ($(du -h "$OUT" | cut -f1))"
echo "on the VPS:  python3 -m venv venv && ./venv/bin/pip install -r requirements.txt"
echo "             nohup ./venv/bin/python -m mtserver.main --listen-host 0.0.0.0 --listen-port 8443 \\"
echo "               --public-host <SERVER_IP_OR_DOMAIN> --public-port 8443 --dev-code 12345 --http-status-port 8080 &"
