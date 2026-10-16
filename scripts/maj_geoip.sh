#!/bin/bash
# =============================================================================
# maj_geoip.sh - base des pays du suivi d'audience (D67)
#
#   QUOI  : telecharge la base des pays du mois (DB-IP Lite, format MMDB,
#           licence CC BY 4.0), la verifie (taille, puis 8.8.8.8 = US avec le
#           venv du service), puis remplace l'ancienne d'un seul coup (`mv`
#           dans le meme dossier). L'application relit le nouveau fichier toute
#           seule : sa date fait partie de la cle du lecteur (app/audience.py).
#   QUI   : root.
#   QUAND : a l'installation, puis le 3 de chaque mois par le minuteur systemd
#           (le serveur n'a pas cron) :
#              cp address-validator-geoip.service address-validator-geoip.timer /etc/systemd/system/
#              systemctl daemon-reload && systemctl enable --now address-validator-geoip.timer
#   OU    : GEOIP_DB_PATH, par defaut /var/lib/address-validator/dbip-country.mmdb
#
# Sans ce fichier, le service marche : le pays vaut seulement « inconnu ».
# Attribution exigee par la licence : « IP Geolocation by DB-IP » (db-ip.com),
# affichee au pied du tableau de bord.
# =============================================================================
set -euo pipefail

CIBLE="${GEOIP_DB_PATH:-/var/lib/address-validator/dbip-country.mmdb}"
PYTHON="${PYTHON:-/opt/address-validator/venv/bin/python}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

telecharger() {
    curl -fsSL --max-time 300 -o "$TMP/base.mmdb.gz" \
        "https://download.db-ip.com/free/dbip-country-lite-$1.mmdb.gz"
}

MOIS="$(date -u +%Y-%m)"
if ! telecharger "$MOIS"; then
    # Tout debut de mois : la base du mois n'est pas encore publiee.
    MOIS="$(date -u -d "$(date -u +%Y-%m-01) -1 day" +%Y-%m)"
    telecharger "$MOIS"
fi
gunzip "$TMP/base.mmdb.gz"

TAILLE="$(stat -c %s "$TMP/base.mmdb")"
if [ "$TAILLE" -lt 1000000 ]; then
    echo "base DB-IP trop petite ($TAILLE octets) : rien n'est remplace" >&2
    exit 1
fi
if [ -x "$PYTHON" ]; then
    "$PYTHON" -c 'import sys, maxminddb
r = maxminddb.open_database(sys.argv[1])
assert (r.get("8.8.8.8") or {}).get("country", {}).get("iso_code") == "US"' "$TMP/base.mmdb"
fi

mkdir -p "$(dirname "$CIBLE")"
chmod 644 "$TMP/base.mmdb"
mv "$TMP/base.mmdb" "$CIBLE.nouveau"
mv "$CIBLE.nouveau" "$CIBLE"
echo "base des pays a jour : $CIBLE (DB-IP Lite $MOIS, $TAILLE octets)"
