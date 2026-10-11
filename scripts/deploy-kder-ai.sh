#!/bin/bash
# =============================================================================
# deploy-kder-ai.sh - deploiement de l'API Address Validator
#   VPS    : 87.76.197.38 (OmegaCloud, id vps-cmv38lwfc00iot901kuellxko) Debian 12
#   Domaine: kder-ai.com
#   Projet : /opt/address-validator
#
# ORDRE D'EXECUTION
#   1. depuis la machine locale : transferer le code + le fichier .env
#   2. sur le VPS, en root :  ADMIN_EMAIL=vous@exemple.fr bash deploy-kder-ai.sh
#
# Le script est idempotent (relancable). Il refuse de continuer si un prerequis
# manque, et n'ecrit jamais de secret dans un fichier suivi par Git.
# Variables surchargeables : PROJET, DOMAINE, IP_ATTENDUE, ADMIN_EMAIL.
# =============================================================================
set -euo pipefail

PROJET="${PROJET:-/opt/address-validator}"
DOMAINE="${DOMAINE:-kder-ai.com}"
IP_ATTENDUE="${IP_ATTENDUE:-87.76.197.38}"
ADMIN_EMAIL="${ADMIN_EMAIL:-}"

etape(){ echo; echo "== $*"; }
ok(){ echo "   [OK] $*"; }
ko(){ echo "   [!!] $*"; }
note(){ echo "   [--] $*"; }
die(){ echo; echo "ARRET : $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "a lancer en root (sudo -i)"
[ -n "$ADMIN_EMAIL" ] || die "ADMIN_EMAIL non fourni (certbot l'exige) : ADMIN_EMAIL=... bash $0"

echo "============================================================================="
echo " Deploiement Address Validator - domaine $DOMAINE - $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================================================="

etape "0. Prerequis"
[ -f "$PROJET/requirements.txt" ] || die "$PROJET/requirements.txt absent : le code n'a pas ete transfere"
[ -f "$PROJET/.env" ] || die "$PROJET/.env absent : deposez le fichier prepare en local"
grep -q '^API_SECRET_KEY=' "$PROJET/.env" || die ".env non conforme : API_SECRET_KEY absente (format attendu NOM=valeur)"
[ -f "$PROJET/nginx.conf" ] || die "$PROJET/nginx.conf absent"
[ -f "$PROJET/address-validator.service" ] || die "$PROJET/address-validator.service absent"
[ -f "$PROJET/nginx-hardening.conf" ] || die "$PROJET/nginx-hardening.conf absent (HSTS, nosniff, limite de debit)"
grep -q "$DOMAINE" "$PROJET/nginx.conf" || die "nginx.conf ne mentionne pas $DOMAINE"
if grep -q $'\r' "$PROJET/.env"; then
  die ".env contient des CR (fins de ligne Windows) : toutes les valeurs seraient corrompues"
fi
ok "fichier .env en LF (aucun CR)"
for v in API_HOST API_PORT API_SECRET_KEY DATABASE_URL REDIS_URL SMARTY_AUTH_ID \
         SMARTY_AUTH_TOKEN CDP_API_KEY_ID CDP_API_KEY_SECRET CDP_WALLET_ADDRESS \
         X402_PRICE CONTACT_EMAIL DOMAIN ENVIRONMENT LOG_LEVEL; do
  grep -q "^$v=" "$PROJET/.env" || die ".env : variable $v absente"
done
ok "15 variables attendues presentes"
# SECURITE : 'change_me' est la valeur par defaut d'app/config.py, et avec elle
# n'importe qui forge une preuve de paiement valide ; un secret court se casse
# hors ligne. Le script refuse desormais de deployer dans ces deux cas.
if grep -q '^API_SECRET_KEY=change_me$' "$PROJET/.env"; then
  die "API_SECRET_KEY vaut encore 'change_me' : generer un secret (openssl rand -hex 32) dans $PROJET/.env"
fi
LONGUEUR_SECRET="$(grep '^API_SECRET_KEY=' "$PROJET/.env" | cut -d= -f2- | tr -d '\r\n' | wc -c)"
if [ "$LONGUEUR_SECRET" -lt 32 ]; then
  die "API_SECRET_KEY fait $LONGUEUR_SECRET caracteres : minimum 32 (openssl rand -hex 32)"
fi
ok "API_SECRET_KEY : $LONGUEUR_SECRET caracteres, differente de la valeur par defaut"
# Variables de la couche on-chain (D27), facultatives : si elles manquent, les
# valeurs par defaut du code s'appliquent (verification on-chain desactivee).
for v in X402_ONCHAIN_VERIFY BASE_RPC_URL X402_USDC_CONTRACT X402_MIN_CONFIRMATIONS; do
  if grep -q "^$v=" "$PROJET/.env"; then ok ".env : $v defini"; else note ".env : $v absent (valeur par defaut du code)"; fi
done
WALLET="$(grep '^CDP_WALLET_ADDRESS=' "$PROJET/.env" | cut -d= -f2-)"
if [ -n "$WALLET" ]; then
  ok "portefeuille d'encaissement : $WALLET"
else
  ko "CDP_WALLET_ADDRESS VIDE : la reponse 402 annoncera un portefeuille vide (aucun encaissement)"
fi

IP_REELLE="$(ip -4 -o addr show scope global | awk '{print $4}' | cut -d/ -f1 | head -1)"
if [ "$IP_REELLE" = "$IP_ATTENDUE" ]; then
  ok "IP du serveur confirmee : $IP_REELLE"
else
  ko "IP du serveur = $IP_REELLE (attendue $IP_ATTENDUE) : verifiez la bonne machine"
fi

export DEBIAN_FRONTEND=noninteractive

etape "1. Mise a jour du systeme"
apt-get update -qq
apt-get upgrade -y -qq
ok "paquets systeme a jour"

etape "2. Dependances systeme"
apt-get install -y -qq python3 python3-venv python3-pip postgresql redis-server nginx certbot python3-certbot-nginx git curl ufw unattended-upgrades
# Debian 12 (bookworm) ne fournit que Python 3.11 et python3.12 est absent de
# bookworm-backports (verifie le 11/10/2026 sur ce serveur). Le projet est
# developpe, teste et conteneurise sous 3.12 (Dockerfile -> python:3.12-slim) :
# on compile CPython depuis python.org, dans /opt, sans toucher aux paquets
# Debian (/usr/bin/python3 reste 3.11.2).
PY312_VERSION=3.12.15
PY312_PREFIX=/opt/python3.12
PY312_JOURNAL=/var/log/python312-build.log
if ! command -v python3.12 >/dev/null 2>&1; then
  echo "   python3.12 absent : ajout du depot bookworm-backports puis nouvelle tentative"
  echo "deb http://deb.debian.org/debian bookworm-backports main" > /etc/apt/sources.list.d/bookworm-backports.list
  apt-get update -qq
  apt-get install -y -qq python3.12 python3.12-venv 2>/dev/null || true
fi
if ! command -v python3.12 >/dev/null 2>&1; then
  ko "python3.12 absent des depots Debian 12 : compilation de CPython $PY312_VERSION"
  ko "depuis python.org (journal : $PY312_JOURNAL, duree 3 a 6 min sur 2 vCPU)"
  apt-get install -y -qq build-essential wget ca-certificates libssl-dev zlib1g-dev \
    libbz2-dev libreadline-dev libsqlite3-dev libffi-dev liblzma-dev libncurses-dev uuid-dev
  mkdir -p /usr/local/src && cd /usr/local/src
  [ -f "Python-$PY312_VERSION.tgz" ] || wget -q "https://www.python.org/ftp/python/$PY312_VERSION/Python-$PY312_VERSION.tgz"
  rm -rf "Python-$PY312_VERSION"
  tar xzf "Python-$PY312_VERSION.tgz"
  cd "Python-$PY312_VERSION"
  { ./configure --prefix="$PY312_PREFIX" --with-ensurepip=install \
    && make -j"$(nproc)" && make altinstall; } > "$PY312_JOURNAL" 2>&1 \
    || { echo "   ECHEC de la compilation, 30 dernieres lignes :"; tail -30 "$PY312_JOURNAL"; die "Python 3.12 non compile"; }
  ln -sf "$PY312_PREFIX/bin/python3.12" /usr/local/bin/python3.12
  ln -sf "$PY312_PREFIX/bin/pip3.12" /usr/local/bin/pip3.12
  cd /
fi
command -v python3.12 >/dev/null 2>&1 || die "python3.12 introuvable apres installation"
PY=python3.12
ok "interpreteur 3.12 : $(python3.12 -V 2>&1) -> $(command -v python3.12)"
"$PY" -c 'import sys; print("   interpreteur :", sys.version.split()[0]); sys.exit(0 if sys.version_info >= (3, 11) else 1)' || die "Python >= 3.11 requis"

etape "3. Services et pare-feu"
systemctl enable --now postgresql redis-server nginx
for svc in postgresql redis-server nginx; do
  if systemctl is-active --quiet "$svc"; then ok "$svc actif"; else die "$svc inactif : journalctl -u $svc -n 50"; fi
done
ufw allow 22/tcp >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null
ufw status verbose | head -12
ok "pare-feu actif (SSH laisse ouvert)"

etape "4. Base de donnees PostgreSQL"
DB_USER="$(awk -F'[:@]' '/^DATABASE_URL=/{print $2}' "$PROJET/.env" | tr -d '/')"
DB_PASS="$(awk -F'[:@]' '/^DATABASE_URL=/{print $3}' "$PROJET/.env")"
DB_NAME="$(awk -F'[:@]' '/^DATABASE_URL=/{print $5}' "$PROJET/.env" | cut -d/ -f2)"
[ -n "$DB_USER" ] && [ -n "$DB_PASS" ] && [ -n "$DB_NAME" ] || die "DATABASE_URL illisible dans .env"
echo "   utilisateur=$DB_USER base=$DB_NAME (mot de passe non affiche)"

if sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" | grep -q 1; then
  sudo -u postgres psql -c "ALTER USER $DB_USER WITH PASSWORD '$DB_PASS'" >/dev/null
  ok "role $DB_USER deja present (mot de passe resynchronise sur .env)"
else
  sudo -u postgres psql -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASS'" >/dev/null
  ok "role $DB_USER cree"
fi

if sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" | grep -q 1; then
  ok "base $DB_NAME deja presente"
else
  sudo -u postgres psql -c "CREATE DATABASE $DB_NAME OWNER $DB_USER" >/dev/null
  ok "base $DB_NAME creee"
fi
sudo -u postgres psql -c "ALTER DATABASE $DB_NAME OWNER TO $DB_USER" >/dev/null
sudo -u postgres psql -d "$DB_NAME" -c "GRANT ALL ON SCHEMA public TO $DB_USER" >/dev/null
ok "privileges accordes"

if PGPASSWORD="$DB_PASS" psql -h 127.0.0.1 -U "$DB_USER" -d "$DB_NAME" -tAc 'SELECT 1' >/dev/null 2>&1; then
  ok "connexion applicative verifiee (TCP 127.0.0.1:5432)"
else
  die "connexion impossible avec DATABASE_URL : verifiez pg_hba.conf (md5/scram) et le mot de passe"
fi

etape "5. Environnement Python"
"$PY" -m venv "$PROJET/venv"
"$PROJET/venv/bin/python" -m pip install --upgrade pip -q
"$PROJET/venv/bin/pip" install -q -r "$PROJET/requirements.txt"
ok "venv : $("$PROJET/venv/bin/python" -V 2>&1)"
"$PROJET/venv/bin/python" -c "import fastapi, uvicorn, sqlalchemy, redis, httpx, pydantic, psycopg2; print('   [OK] imports applicatifs')"

etape "6. Permissions du .env et schema de base"
chown root:www-data "$PROJET/.env"
chmod 640 "$PROJET/.env"
ls -l "$PROJET/.env"
echo "   (640 root:www-data et non 600 : le service tourne sous www-data et doit pouvoir lire ses secrets)"
cd "$PROJET"
TABLEAUX="$(sudo -u www-data "$PROJET/venv/bin/python" -c "from app.database import init_db; print('init_db=' + str(init_db()))")"
echo "   $TABLEAUX"
sudo -u postgres psql -d "$DB_NAME" -tAc "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename" | sed 's/^/   table: /'

etape "7. Reverse proxy Nginx"
cp "$PROJET/nginx.conf" /etc/nginx/sites-available/address-validator
ln -sf /etc/nginx/sites-available/address-validator /etc/nginx/sites-enabled/address-validator
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx
ok "nginx recharge (server_name $(awk '/server_name/{print $2, $3}' "$PROJET/nginx.conf"))"
# Durcissement HTTP (HSTS, nosniff, limite de debit) : installe dans conf.d,
# donc dans le contexte http de nginx. Il ne vit volontairement PAS dans le
# vhost ci-dessus : `certbot --nginx` reecrit ce fichier a chaque
# obtention/renouvellement du certificat et effacerait les ajouts manuels.
cp "$PROJET/nginx-hardening.conf" /etc/nginx/conf.d/address-validator-hardening.conf
nginx -t
systemctl reload nginx
ok "durcissement nginx installe (/etc/nginx/conf.d/address-validator-hardening.conf)"

CERT_OK=0
DNS_KO=0
etape "8. Certificat TLS (Certbot)"
DNS_APEX="$(getent ahostsv4 "$DOMAINE" | awk '{printf "%s ", $1}')"
DNS_WWW="$(getent ahostsv4 "www.$DOMAINE" | awk '{printf "%s ", $1}')"
echo "   $DOMAINE     -> ${DNS_APEX:-aucune reponse DNS}"
echo "   www.$DOMAINE -> ${DNS_WWW:-aucune reponse DNS}"
case " $DNS_APEX " in
  *" $IP_ATTENDUE "*) ok "DNS apex propage vers $IP_ATTENDUE" ;;
  *) ko "DNS apex NON propage vers $IP_ATTENDUE" ; DNS_KO=1 ;;
esac
case " $DNS_WWW " in
  *" $IP_ATTENDUE "*) ok "DNS www propage vers $IP_ATTENDUE" ;;
  *) ko "DNS www NON propage vers $IP_ATTENDUE" ; DNS_KO=1 ;;
esac
if [ "$DNS_KO" = "1" ]; then
  ko "Certbot volontairement NON lance : sans DNS il echoue et consomme le quota Let's Encrypt."
  ko "A faire chez le registrar (Namecheap) : A @ -> $IP_ATTENDUE, A www -> $IP_ATTENDUE, puis relancer ce script."
  ko "Les etapes 1 a 7 sont terminees : l'API repond deja en HTTP (port 80)."
else
  certbot --nginx -d "$DOMAINE" -d "www.$DOMAINE" --non-interactive --agree-tos -m "$ADMIN_EMAIL" --redirect
  # certbot applique un delai aleatoire (0 a 480 s) a 'renew' en mode non
  # interactif, MEME avec --force-renewal : constate pendant l'essai n°5
  # ("random delay of 463.937569115934 seconds" dans letsencrypt.log) alors que
  # le commentaire precedent affirmait le contraire, et le 'timeout 300'
  # coupait la simulation. --no-random-sleep-on-renew supprime ce delai ; il
  # n'existe que pour les scripts et les simulations, le certbot.timer de
  # production garde le sien. Le controle dure alors ~1 min et valide toute la
  # chaine sur le serveur de test de Let's Encrypt. Un echec reste non
  # bloquant : le certificat en place est valide et certbot.timer porte le
  # renouvellement.
  if timeout 300 certbot renew --dry-run --force-renewal --no-random-sleep-on-renew 2>&1 | tail -4; then
    ok "renouvellement automatique verifie (simulation complete reussie)"
  else
    ko "simulation de renouvellement non concluante : relancer 'certbot renew --dry-run'"
  fi
  CERT_OK=1
  ok "certificat TLS installe, redirection HTTP -> HTTPS active"
fi

etape "9. Service systemd"
cp "$PROJET/address-validator.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now address-validator
sleep 3
if systemctl is-active --quiet address-validator; then
  ok "address-validator actif"
  systemctl status address-validator --no-pager | head -12
else
  journalctl -u address-validator -n 40 --no-pager
  die "service en echec (voir journal ci-dessus)"
fi

etape "10. Tests de bout en bout"
echo "   --- attente de disponibilite du port 8000 (max 60 s) ---"
API_PRET=0
for i in $(seq 1 60); do
  if curl -sf -m 2 http://127.0.0.1:8000/health >/dev/null 2>&1; then
    ok "API joignable apres ${i} s"
    API_PRET=1
    break
  fi
  sleep 1
done
if [ "$API_PRET" != "1" ]; then
  journalctl -u address-validator -n 30 --no-pager
  die "l'API ne repond pas sur le port 8000 apres 60 s"
fi
echo "   --- 10.1 /health en local ---"
curl -sS -m 10 http://127.0.0.1:8000/health; echo
echo "   --- 10.2 /manifest en local ---"
REPONSE_MANIFEST="$(curl -sS -m 10 http://127.0.0.1:8000/manifest)"
echo "        ${REPONSE_MANIFEST:0:300}"
echo "   --- 10.3 /validate sans preuve : 402 attendu ---"
curl -sS -m 15 -o /dev/null -w "        HTTP %{http_code}\n" -X POST http://127.0.0.1:8000/validate -H "Content-Type: application/json" -d '{"address":{"street":"1600 Amphitheatre Parkway","city":"Mountain View","state":"CA","zip":"94043","country":"US"}}'
echo "   --- 10.4 /validate avec preuve bidon : 402 attendu (fail-closed) ---"
curl -sS -m 15 -o /dev/null -w "        HTTP %{http_code}\n" -X POST http://127.0.0.1:8000/validate -H "Content-Type: application/json" -H "X-Payment-Proof: test_tx:1234567890:test_signature" -d '{"address":{"street":"1600 Amphitheatre Parkway","city":"Mountain View","state":"CA","zip":"94043","country":"US"}}'
if [ "$CERT_OK" = "1" ]; then
  echo "   --- 10.5 HTTPS public ---"
  curl -sS -m 10 "https://$DOMAINE/health"; echo
  curl -sS -m 10 -o /dev/null -w "        https://$DOMAINE/manifest -> HTTP %{http_code}\n" "https://$DOMAINE/manifest"
else
  ko "tests HTTPS non executes (certificat absent)"
fi
echo "   --- 10.6 ecoute locale obligatoire (port 8000) ---"
if ss -ltn | grep -q '127.0.0.1:8000'; then
  ok "uvicorn ecoute sur 127.0.0.1:8000"
else
  ko "uvicorn n'ecoute PAS sur 127.0.0.1:8000 : verifier ExecStart dans /etc/systemd/system/address-validator.service"
fi
if ss -ltn | grep -q -e '0.0.0.0:8000' -e '\[::\]:8000'; then
  ko "uvicorn ecoute AUSSI hors boucle locale : le port est joignable en direct"
else
  ok "aucune ecoute de uvicorn hors boucle locale"
fi
if [ "$CERT_OK" = "1" ]; then
  echo "   --- 10.7 en-tetes de securite (HTTPS public) ---"
  ENTETES="$(curl -sS -m 10 -D - -o /dev/null "https://$DOMAINE/health")"
  if echo "$ENTETES" | grep -qi '^strict-transport-security:'; then
    ok "HSTS : $(echo "$ENTETES" | grep -i '^strict-transport-security:' | tr -d '\r')"
  else
    ko "HSTS absent : verifier /etc/nginx/conf.d/address-validator-hardening.conf"
  fi
  if echo "$ENTETES" | grep -qi '^x-content-type-options: *nosniff'; then
    ok "X-Content-Type-Options : nosniff present"
  else
    ko "X-Content-Type-Options : nosniff absent"
  fi
  echo "   --- 10.8 limite de debit (60 appels simultanes sur /manifest) ---"
  # /manifest plutot que /health : aucune sonde base/redis derriere, donc les 60
  # appels tiennent en moins d'une seconde et la limite (10 req/s, rafale 20) se
  # declenche vraiment. Un envoi sequentiel ne depasserait jamais 10 req/s et ne
  # prouverait rien.
  FICHIER_CODES="$(mktemp)"
  for i in $(seq 1 60); do
    curl -s -m 10 -o /dev/null -w '%{http_code}\n' "https://$DOMAINE/manifest" >> "$FICHIER_CODES" &
  done
  wait || true
  NB_APPELS="$(wc -l < "$FICHIER_CODES")"
  NB_200="$(grep -c '^200$' "$FICHIER_CODES" || true)"
  NB_429="$(grep -c '^429$' "$FICHIER_CODES" || true)"
  rm -f "$FICHIER_CODES"
  echo "        $NB_APPELS appels -> $NB_200 x HTTP 200, $NB_429 x HTTP 429"
  if [ "$NB_429" -gt 0 ]; then
    ok "limite de debit active (des 429 apparaissent au-dela de la rafale)"
  else
    ko "aucun 429 sur $NB_APPELS appels simultanes : limit_req ne s'applique pas"
  fi
else
  ko "controles de durcissement HTTPS non executes (certificat absent)"
fi
echo "   --- 10.9 dernieres lignes de journal ---"
journalctl -u address-validator -n 12 --no-pager

etape "11. Sauvegardes et mises a jour automatiques"
cat > "$PROJET/backup.sh" <<'FINDESCRIPT'
#!/bin/bash
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR=/opt/backups
mkdir -p $BACKUP_DIR
chmod 700 $BACKUP_DIR
sudo -u postgres pg_dump address_validator > $BACKUP_DIR/db_$DATE.sql
chmod 600 $BACKUP_DIR/db_$DATE.sql
cp /opt/address-validator/.env $BACKUP_DIR/env_$DATE.backup
chmod 600 $BACKUP_DIR/env_$DATE.backup
find $BACKUP_DIR -type f -mtime +30 -delete
echo "Sauvegarde terminee : $DATE"
FINDESCRIPT
chmod 700 "$PROJET/backup.sh"
# Debian 12 minimal n'installe pas le paquet 'cron' (constate sur ce VPS :
# "crontab: command not found"). La sauvegarde est donc planifiee par minuteur
# systemd : aucun paquet supplementaire, meme mecanisme que le renouvellement
# Certbot de cette machine, et les sorties restent consultables via journalctl.
cat > /etc/systemd/system/address-validator-backup.service <<'FINDESERVICE'
[Unit]
Description=Sauvegarde quotidienne Address Validator (base + .env)
After=postgresql.service

[Service]
Type=oneshot
ExecStart=/opt/address-validator/backup.sh
FINDESERVICE
cat > /etc/systemd/system/address-validator-backup.timer <<'FINDETIMER'
[Unit]
Description=Declenche la sauvegarde Address Validator chaque nuit a 03h00

[Timer]
OnCalendar=*-*-* 03:00:00
Persistent=true
RandomizedDelaySec=600

[Install]
WantedBy=timers.target
FINDETIMER
systemctl daemon-reload
systemctl enable --now address-validator-backup.timer
systemctl list-timers address-validator-backup.timer --no-pager
ok "sauvegarde planifiee : minuteur systemd address-validator-backup (03h00 UTC)"
systemctl enable --now unattended-upgrades >/dev/null 2>&1 || true
systemctl is-active --quiet unattended-upgrades && ok "mises a jour de securite automatiques actives"
"$PROJET/backup.sh"
ls -l /opt/backups
ok "sauvegarde de controle executee (voir /opt/backups)"

if [ "$CERT_OK" = "1" ]; then HTTP_SCHEME="s"; else HTTP_SCHEME=""; fi

echo
echo "============================================================================="
echo " RECAPITULATIF DU DEPLOIEMENT"
echo "============================================================================="
echo " API         : http://127.0.0.1:8000 (boucle locale uniquement)"
echo " Sante       : http${HTTP_SCHEME}://$DOMAINE/health"
echo " Documentation : http${HTTP_SCHEME}://$DOMAINE/docs"
echo " Manifest    : http${HTTP_SCHEME}://$DOMAINE/manifest"
echo " Service     : systemctl status address-validator"
echo " Logs        : journalctl -u address-validator -f"
echo " Redemarrer  : systemctl restart address-validator"
echo " Mettre a jour le code : transferer puis systemctl restart address-validator"
echo " Sauvegardes : /opt/backups (quotidien a 03h00, purge a 30 jours)"
echo "============================================================================="
echo " POINTS DE VIGILANCE"
echo " 1. SmartyStreets : identifiants VALIDES (mesure du 11/10/2026 : HTTP 200,"
echo "    dpv_match_code=Y, rdi=Commercial ; /validate -> validated=true,"
echo "    deliverability=residential, confidence=1.0, degraded=false)."
echo "    Si /validate repond degraded=true, ce n'est donc pas une fatalite : 401 ="
echo "    identifiants refuses, 402 = abonnement/credits inactifs, timeout = reseau."
echo "    Mesurer sans ambiguite avec scripts/e2e_check.py (preuve HMAC valide) :"
echo "    une preuve bidon (test_tx:1234567890:test_signature) est refusee en 402"
echo "    par l'API elle-meme (controle 10.4), pas par le fournisseur."
echo " 2. CDP_WALLET_ADDRESS : si la variable est vide dans .env, la reponse 402"
echo "    annonce un portefeuille vide -> aucun encaissement possible."
echo " 3. Paiement : preuve HMAC a secret partage (D15). La confirmation on-chain"
echo "    du transfert USDC sur Base (D27) existe mais reste desactivee par defaut :"
echo "    qui connait API_SECRET_KEY peut encore forger une preuve valide. Pour"
echo "    l'activer : X402_ONCHAIN_VERIFY=true dans .env puis systemctl restart."
echo " 4. '.env' en 640 root:www-data : ne pas le repasser en 600 root, le service"
echo "    tourne sous www-data et perdrait la lecture de ses secrets."
echo " 5. uvicorn ecoute sur 127.0.0.1:8000 (ExecStart) : seul Nginx publie l'API."
echo "    Ne pas remettre 0.0.0.0 dans address-validator.service."
echo " 6. Durcissement Nginx : HSTS, nosniff, 10 requetes/s par IP (429 au-dela)."
echo "    Ces reglages vivent dans /etc/nginx/conf.d/address-validator-hardening.conf ;"
echo "    les ecrire dans le vhost serait perdu au prochain renouvellement Certbot."
echo "============================================================================="
echo "DEPLOIEMENT-KDER-AI: TERMINE"
