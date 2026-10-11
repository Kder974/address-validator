#!/bin/bash
# Déploiement de l'Address Validator API sur un VPS Debian/Ubuntu.
# Décision D20 : ajout de `set -euo pipefail` (arrêt immédiat sur la première
# erreur) — un script d'installation qui continue après un `apt install` échoué
# laisse le serveur dans un état incohérent. Remplacez <ton-repo> ci-dessous.
set -euo pipefail

REPO_URL="${REPO_URL:-<ton-repo>}"

sudo apt update && sudo apt upgrade -y

sudo apt install -y python3.12 python3.12-venv postgresql redis-server nginx certbot python3-certbot-nginx

cd /opt
sudo git clone "$REPO_URL" address-validator
cd address-validator

python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

sudo -u postgres psql -c "CREATE DATABASE address_validator;"
sudo -u postgres psql -c "CREATE USER validator WITH PASSWORD 'change_me';"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE address_validator TO validator;"

sudo systemctl enable redis-server
sudo systemctl start redis-server

sudo cp nginx.conf /etc/nginx/sites-available/address-validator
sudo ln -s /etc/nginx/sites-available/address-validator /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx

# Durcissement (HSTS, nosniff, limite de debit) : fichier conf.d, donc conserve
# meme quand `certbot --nginx` reecrit le vhost ci-dessus.
sudo cp nginx-hardening.conf /etc/nginx/conf.d/address-validator-hardening.conf
sudo nginx -t
sudo systemctl reload nginx

sudo certbot --nginx -d kder-ai.com -d www.kder-ai.com --redirect

sudo cp address-validator.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable address-validator
sudo systemctl start address-validator

echo "Déploiement terminé."
