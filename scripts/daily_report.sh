#!/bin/bash
# =============================================================================
# daily_report.sh - rapport quotidien d'exploitation (decouvrabilite + revenu)
#
#   QUOI  : compte les refus 402 du journal de decouverte, les validations
#           payees et le revenu des 24 h depuis le registre durable, purge le
#           journal selon DISCOVERY_LOG_RETENTION_DAYS, puis envoie le rapport.
#   QUI   : l'exploitant du service (root ou utilisateur du service).
#   QUAND : une fois par jour. Cron recommande :
#              10 7 * * *  /opt/address-validator/scripts/daily_report.sh
#   OU    : /opt/address-validator/scripts/daily_report.sh
#
# Variables surchargeables : PROJET, PYTHON, REPORT_DIR, REPORT_MAIL_TO.
# Les reglages du service (chemin du journal, retention) sont lus par
# `app.config` : le rapport ne peut donc pas diverger du comportement de l'API.
# Aucun secret n'est ecrit, aucune requete ne sort du serveur.
# =============================================================================
set -euo pipefail

PROJET="${PROJET:-/opt/address-validator}"
PYTHON="${PYTHON:-$PROJET/.venv/bin/python}"
REPORT_DIR="${REPORT_DIR:-/var/log/address-validator/reports}"
REPORT_MAIL_TO="${REPORT_MAIL_TO:-}"

[ -d "$PROJET" ] || { echo "ARRET : projet introuvable : $PROJET" >&2; exit 1; }
[ -x "$PYTHON" ] || { echo "ARRET : interpreteur introuvable : $PYTHON" >&2; exit 1; }

cd "$PROJET"
mkdir -p "$REPORT_DIR"

JOUR="$(date -u +%Y-%m-%d)"
RAPPORT="$REPORT_DIR/rapport-$JOUR.txt"

# Les chiffres viennent des memes fonctions que /metrics/revenue (D31) et que
# la purge du journal (D30) : une valeur du rapport ne peut pas contredire ce
# que l'API publie.
"$PYTHON" - "$JOUR" <<'PY' > "$RAPPORT"
import os
import sys
from datetime import datetime, timedelta, timezone

from app.config import get_settings
from app.observability import count_since, prune, read_events
from app.routes.revenue import revenue_stats

jour = sys.argv[1]
maintenant = datetime.now(timezone.utc)
depuis = maintenant - timedelta(hours=24)

reglages = get_settings()
journal = str(reglages.discovery_log_path or "").strip()
retention = int(reglages.discovery_log_retention_days)


def horodatage(evenement):
    try:
        return datetime.fromisoformat(str(evenement.get("ts", "")).replace("Z", "+00:00"))
    except ValueError:
        return None


evenements = list(read_events(journal)) if journal else []
recents = [e for e in evenements if (horodatage(e) or depuis - timedelta(days=1)) >= depuis]
clients = {e.get("ip") for e in recents if e.get("ip")}

stats = revenue_stats(include_recent=False)
registre = stats.get("ledger", {})
fenetre = stats.get("windows", {}).get("24h", {})

print(f"Rapport Address Validator - {jour} (UTC)")
print("=" * 62)
print()
print("Decouvrabilite : requetes refusees en 402")
if not journal:
    print("  DISCOVERY_LOG_PATH vide : aucune trace locale (journald seul).")
    print("  Ce n'est pas 'zero refus', c'est 'non mesure'.")
else:
    mesure = count_since(journal, depuis)
    print(f"  refus 402 sur 24 h : {mesure}")
    print(f"  refus 402 cumules  : {len(evenements)}")
    print(f"  clients distincts  : {len(clients)} (pseudonymes ; IP en clair seulement")
    print("                       si DISCOVERY_LOG_RAW_IP=true)")
    if mesure != len(recents):
        print(f"  ATTENTION : comptage incoherent ({mesure} vs {len(recents)})")
print()
print("Revenu : registre durable (payment_transactions)")
if registre.get("available"):
    print(f"  validations payees 24 h : {fenetre.get('real_validations_paid', fenetre.get('validations_paid', 0))}")
    print(f"  revenu 24 h             : {fenetre.get('real_revenue_usdc', fenetre.get('revenue_usdc', 0.0))} USDC")
    print(f"  validations payees (cumul) : {registre.get('real_validations_paid', registre.get('validations_paid', 0))}")
    print(f"  revenu (cumul)             : {registre.get('real_revenue_usdc', registre.get('revenue_usdc', 0.0))} USDC")
    # D40 : un essai de recette n'est pas du revenu client. Le rapport separe les
    # deux, sinon il gonflerait le chiffre d'affaires avec nos propres tests.
    print(f"  dont recette (exclue)      : {registre.get('test_validations_paid', 0)} essai(s) / {registre.get('test_revenue_usdc', 0.0)} USDC")
    print(f"  revenu total du registre   : {registre.get('total_revenue_usdc', registre.get('revenue_usdc', 0.0))} USDC")
    print(f"  prix unitaire              : {stats.get('price_per_validation_usdc')} USDC")
else:
    print(f"  REGISTRE INDISPONIBLE : {registre.get('error')}")
    print("  Les chiffres de revenu ne sont PAS mesures : ne pas lire un zero.")
print()
print("Purge : conservation annoncee = conservation appliquee")
if journal:
    restant = prune(journal, retention)
    print(f"  journal         : {journal}")
    print(f"  retention       : {retention} jours")
    print(f"  entrees gardees : {restant}")
else:
    print("  rien a purger : DISCOVERY_LOG_PATH vide")
print()
print(f"Genere le {maintenant.isoformat(timespec='seconds')} par scripts/daily_report.sh")
PY

cat "$RAPPORT"

if [ -n "$REPORT_MAIL_TO" ]; then
  if command -v mail >/dev/null 2>&1; then
    mail -s "[address-validator] rapport du $JOUR" "$REPORT_MAIL_TO" < "$RAPPORT"
    echo "[--] rapport envoye a $REPORT_MAIL_TO"
  elif command -v sendmail >/dev/null 2>&1; then
    {
      printf 'To: %s\nSubject: [address-validator] rapport du %s\n\n' "$REPORT_MAIL_TO" "$JOUR"
      cat "$RAPPORT"
    } | sendmail -t
    echo "[--] rapport envoye a $REPORT_MAIL_TO (sendmail)"
  else
    echo "[!!] ni mail ni sendmail : rapport conserve dans $RAPPORT" >&2
  fi
else
  echo "[--] REPORT_MAIL_TO non defini : rapport conserve dans $RAPPORT"
fi

# Un rapport partiel doit se voir dans le code de retour, sinon cron l'ignore.
if grep -q "REGISTRE INDISPONIBLE" "$RAPPORT"; then
  echo "[!!] registre de paiement indisponible : rapport partiel" >&2
  exit 1
fi
