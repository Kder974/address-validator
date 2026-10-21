#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Veille agentique quotidienne : paiements x402, agents, ecosysteme — cout 0 EUR.

QUI   : l'exploitant du service Address Validator (root ou www-data sur le VPS).
QUOI  : produit un digest markdown (et, sur demande, un email) qui repond a une
        seule question : « la vague agentique nous rapporte-t-elle quelque
        chose, et qu'est-ce qui a change ? ».
COMMENT : agrege quatre sources, sans aucune API payante :
        1. interne — `/metrics/revenue` (meme fonction `revenue_stats` que l'API)
           et le journal de decouverte JSONL (D30) : refus 402, classes d'agents,
           agents distincts, fenetres 24 h ;
        2. acces Nginx (`combined`, optionnel) : `/validate` et `/mcp` par statut ;
        3. ecosysteme — GitHub releases des depots x402 / AP2 / ACP, README du
           x402 Discovery Index, catalogue Bazaar s'il est public ;
        4. on-chain — transferts USDC recus sur la wallet `payTo` via le nœud
           JSON-RPC Base (aucun indexeur payant).
        Un état JSON (`--etat`) permet de ne signaler que les **changements** :
        nouvelle release, nouveau paiement, nouvelle classe d'agent, pic de 402.
QUAND : une fois par jour (timer systemd `address-validator-veille.timer`,
        07:10 UTC), ou a la demande apres une campagne de decouverte.
OU    : `scripts/veille_agentique.py` ; rapport par defaut dans
        `docs/VEILLE-AGENTIQUE-DERNIER.md` (VPS : `/var/log/address-validator/veille.md`).
FAIT QUOI EXACTEMENT : lit les sources, ecrit le rapport markdown, met a jour
        l'etat, envoie le digest si `--email` et si SMTP_* est configure, et
        affiche un resume sur stdout. Aucun secret n'est ecrit ; aucune donnee
        personnelle n'est ajoutee (le journal de decouverte est deja pseudonymise).

Codes de retour : 0 rapport produit (meme si une source manque — l'absence est
une donnee, elle est ecrite) ; 2 usage invalide.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import smtplib
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import observability  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.routes.revenue import revenue_stats  # noqa: E402

#: User-Agent identifiable : la veille s'annonce, comme le geocodeur de l'API.
AGENT_HTTP = "AddressValidator-veille/1.0 (+https://kder-ai.com; address-validator@kder-ai.com)"

#: Depots suivis (GitHub API publique, 60 requetes/h sans jeton : suffisant).
DEPOTS = (
    "coinbase/x402",
    "coinbase/agentkit",
    "x402-index/x402-discovery-index",
    "google-agentic-commerce/AP2",
    "stripe/agentic-commerce-protocol",
)

#: Adresse USDC sur Base et topic ERC-20 `Transfer(from, to, value)`.
USDC_BASE = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
TOPIC_TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

#: Fenetre par defaut du journal et de l'acces Nginx.
FENETRE_DEFAUT = "24h"

#: Rapport local par defaut (le VPS surcharge par VEILLE_SORTIE).
SORTIE_DEFAUT = ROOT / "docs" / "VEILLE-AGENTIQUE-DERNIER.md"


# --------------------------------------------------------------------------- #
# Outils generiques
# --------------------------------------------------------------------------- #


def maintenant() -> datetime:
    """Horodatage UTC courant (seconde pres)."""
    return datetime.now(timezone.utc).replace(microsecond=0)


def depuis_texte(texte: str) -> datetime:
    """Convertit `24h`, `7d`, `30d` (ou ISO 8601) en datetime UTC."""
    valeur = (texte or FENETRE_DEFAUT).strip().lower()
    correspondance = re.fullmatch(r"(\d+)\s*([hdj])", valeur)
    if correspondance:
        nombre = int(correspondance.group(1))
        unite = correspondance.group(2)
        if unite == "d":
            return maintenant() - timedelta(days=nombre)
        if unite == "j":
            return maintenant() - timedelta(days=nombre)
        return maintenant() - timedelta(hours=nombre)
    try:
        moment = datetime.fromisoformat(valeur.replace("z", "+00:00"))
        return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
    except ValueError as erreur:
        raise ValueError(f"fenetre illisible : {texte!r} (attendu 24h, 7d, 30d)") from erreur


def _get(url: str, timeout: float = 15.0) -> tuple[int, str, str]:
    """Lecture HTTP identifiee ; `(code, corps, note)` — 0 et note si echec."""
    demande = urllib.request.Request(
        url,
        headers={
            "User-Agent": AGENT_HTTP,
            "Accept": "application/json, text/plain;q=0.9, text/html;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(demande, timeout=timeout) as reponse:
            corps = reponse.read(600_000).decode("utf-8", errors="replace")
            return int(getattr(reponse, "status", 200)), corps, ""
    except urllib.error.HTTPError as erreur:
        return int(erreur.code), "", f"HTTP {erreur.code}"
    except Exception as erreur:  # noqa: BLE001 - reseau : l'echec est une donnee
        return 0, "", f"{type(erreur).__name__}"


def _get_json(url: str) -> tuple[Any, str]:
    """JSON distant, ou `(None, note)` sans jamais lever."""
    code, corps, note = _get(url)
    if code != 200 or not corps:
        return None, note or f"HTTP {code}"
    try:
        return json.loads(corps), ""
    except json.JSONDecodeError:
        return None, "json_illisible"


def _post_json(url: str, charge: dict, timeout: float = 15.0) -> tuple[Any, str]:
    """POST JSON (nœud RPC Base) ; `(None, note)` si echec."""
    donnees = json.dumps(charge).encode("utf-8")
    demande = urllib.request.Request(
        url,
        data=donnees,
        headers={"User-Agent": AGENT_HTTP, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(demande, timeout=timeout) as reponse:
            return json.loads(reponse.read(600_000).decode("utf-8", errors="replace")), ""
    except Exception as erreur:  # noqa: BLE001
        return None, f"{type(erreur).__name__}"


# --------------------------------------------------------------------------- #
# Sources internes
# --------------------------------------------------------------------------- #


def revenu() -> dict[str, Any]:
    """Revenu reel depuis le registre durable (`revenue_stats`, même source que l'API)."""
    try:
        stats = revenue_stats(include_recent=False)
    except Exception as erreur:  # noqa: BLE001 - base indisponible : le dire
        return {"disponible": False, "erreur": type(erreur).__name__}
    registre = stats.get("ledger") or {}
    fenetres = stats.get("windows") or {}
    return {
        "disponible": bool(registre.get("available")),
        "erreur": registre.get("error"),
        "reels_cumul": int(registre.get("real_validations_paid", 0) or 0),
        "revenu_cumul_usdc": float(registre.get("real_revenue_usdc", 0.0) or 0.0),
        "payeurs": int(registre.get("unique_address_keys", 0) or 0),
        "dernier_paiement": registre.get("last_observed_at"),
        "fenetre_24h": {
            "paiements": int((fenetres.get("24h") or {}).get("real_validations_paid", 0) or 0),
            "revenu_usdc": float((fenetres.get("24h") or {}).get("real_revenue_usdc", 0.0) or 0.0),
        },
        "fenetre_7j": {
            "paiements": int((fenetres.get("7d") or {}).get("real_validations_paid", 0) or 0),
            "revenu_usdc": float((fenetres.get("7d") or {}).get("real_revenue_usdc", 0.0) or 0.0),
        },
    }


def _horodatage(evenement: dict) -> datetime | None:
    """`ts` ISO d'un evenement de decouverte, ou `None`."""
    try:
        moment = datetime.fromisoformat(str(evenement.get("ts", "")).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def analyser_journal(evenements: Iterable[dict], depuis: datetime) -> dict[str, Any]:
    """Agrege le journal de decouverte : refus 402, classes, agents distincts."""
    retenus = [e for e in evenements if (_horodatage(e) or depuis) >= depuis]
    classes: Counter[str] = Counter()
    agents: Counter[str] = Counter()
    endpoints: Counter[str] = Counter()
    for evenement in retenus:
        user_agent = str(evenement.get("user_agent") or "")
        classes[observability.agent_class(user_agent)] += 1
        if user_agent:
            agents[observability.agent_name(user_agent)] += 1
        endpoints[str(evenement.get("endpoint") or "?")] += 1
    return {
        "disponible": True,
        "refus_402_fenetre": len(retenus),
        "refus_402_total": None,  # rempli par l'appelant qui connait tout le journal
        "classes": dict(classes.most_common(6)),
        "agents": dict(agents.most_common(8)),
        "endpoints": dict(endpoints.most_common(5)),
        "agents_distincts": len(agents),
    }


def journal_decouverte(depuis: datetime) -> dict[str, Any]:
    """Lit `DISCOVERY_LOG_PATH` et agrege la fenetre."""
    reglages = get_settings()
    chemin = str(reglages.discovery_log_path or "").strip()
    if not chemin:
        return {"disponible": False, "erreur": "DISCOVERY_LOG_PATH vide (journald seul)"}
    fichier = Path(chemin)
    if not fichier.is_file():
        return {"disponible": False, "erreur": f"journal absent : {fichier}"}
    evenements = list(observability.read_events(fichier))
    resultat = analyser_journal(evenements, depuis)
    resultat["refus_402_total"] = len(evenements)
    resultat["fichier"] = str(fichier)
    return resultat


_LIGNE_NGINX = re.compile(
    r'^(?P<ip>\S+) \S+ \S+ \[(?P<ts>[^\]]+)\] "(?P<methode>\S+) (?P<chemin>\S+)[^"]*" '
    r"(?P<statut>\d{3}) "
)


def analyser_acces(chemin: Path | str, depuis: datetime) -> dict[str, Any]:
    """Compte `/validate` et `/mcp` par statut dans un journal Nginx `combined`."""
    fichier = Path(chemin)
    if not fichier.is_file():
        return {"disponible": False, "erreur": f"journal d'acces absent : {fichier}"}
    compteurs: Counter[str] = Counter()
    for ligne in fichier.read_text(encoding="utf-8", errors="replace").splitlines():
        correspondance = _LIGNE_NGINX.match(ligne)
        if not correspondance:
            continue
        try:
            moment = datetime.strptime(correspondance.group("ts"), "%d/%b/%Y:%H:%M:%S %z")
        except ValueError:
            continue
        if moment < depuis:
            continue
        chemin_appel = correspondance.group("chemin").split("?")[0]
        if chemin_appel not in ("/validate", "/mcp"):
            continue
        compteurs[f"{chemin_appel} {correspondance.group('statut')}"] += 1
    return {"disponible": True, "compteurs": dict(sorted(compteurs.items()))}


# --------------------------------------------------------------------------- #
# Sources externes (gratuites)
# --------------------------------------------------------------------------- #


def releases_github() -> dict[str, Any]:
    """Derniere release de chaque depot suivi (GitHub API publique)."""
    resultat: dict[str, Any] = {}
    for depot in DEPOTS:
        document, note = _get_json(f"https://api.github.com/repos/{depot}/releases/latest")
        if document is None:
            resultat[depot] = {"disponible": False, "erreur": note}
            continue
        resultat[depot] = {
            "disponible": True,
            "version": str(document.get("tag_name") or document.get("name") or "?"),
            "publie_le": document.get("published_at"),
            "url": document.get("html_url"),
        }
    return resultat


def index_x402() -> dict[str, Any]:
    """Nombre d'API annonce par x402 Discovery Index (README public)."""
    code, texte, note = _get("https://raw.githubusercontent.com/x402-index/x402-discovery-index/main/README.md")
    if code != 200 or not texte:
        return {"disponible": False, "erreur": note or f"HTTP {code}"}
    nombres = re.findall(r"([\d][\d,\.]{2,})\+?\s*(?:x402-enabled\s+)?APIs", texte, re.I)
    return {"disponible": True, "api_annoncees": nombres[0] if nombres else "?"}


def bazaar() -> dict[str, Any]:
    """Catalogue Coinbase Bazaar s'il est public, et presence de kder-ai.com."""
    url = os.environ.get("BAZAAR_URL", "https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources")
    code, corps, note = _get(url)
    if code != 200 or not corps:
        return {"disponible": False, "erreur": note or f"HTTP {code}", "url": url}
    try:
        document = json.loads(corps)
    except json.JSONDecodeError:
        return {"disponible": False, "erreur": "json_illisible", "url": url}
    ressources = document.get("resources") if isinstance(document, dict) else None
    return {
        "disponible": True,
        "url": url,
        "entrees": len(ressources) if isinstance(ressources, list) else "?",
        "nous_listes": "kder-ai.com" in corps,
    }


def transferts_usdc(dernier_bloc: int | None) -> dict[str, Any]:
    """Transferts USDC recus sur la wallet `payTo`, via le nœud JSON-RPC Base."""
    reglages = get_settings()
    rpc = str(getattr(reglages, "base_rpc_url", "") or "https://mainnet.base.org")
    wallet = str(getattr(reglages, "cdp_wallet_address", "") or "").strip()
    if not wallet.startswith("0x") or len(wallet) != 42:
        return {"disponible": False, "erreur": "wallet payTo absente de la configuration"}
    bloc_reponse, note = _post_json(rpc, {"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []})
    if not bloc_reponse or "result" not in bloc_reponse:
        return {"disponible": False, "erreur": "eth_blockNumber : " + (note or "reponse inattendue")}
    bloc_courant = int(str(bloc_reponse["result"]), 16)
    debut = int(dernier_bloc) + 1 if dernier_bloc else max(0, bloc_courant - 2000)
    debut = max(0, min(debut, bloc_courant))
    filtre = {
        "address": USDC_BASE,
        "fromBlock": hex(debut),
        "toBlock": hex(bloc_courant),
        "topics": [TOPIC_TRANSFER, None, "0x" + "0" * 24 + wallet[2:].lower()],
    }
    reponse, note = _post_json(
        rpc,
        {"jsonrpc": "2.0", "id": 2, "method": "eth_getLogs", "params": [filtre]},
        timeout=25.0,
    )
    if reponse is None or "result" not in reponse:
        return {"disponible": False, "erreur": "eth_getLogs : " + (note or "reponse inattendue")}
    transferts = []
    for entree in reponse["result"]:
        try:
            montant = int(str(entree.get("data") or "0x0"), 16) / 1_000_000
        except ValueError:
            continue
        transferts.append(
            {
                "tx": str(entree.get("transactionHash") or ""),
                "de": "0x" + str((entree.get("topics") or ["", ""])[1])[-40:],
                "montant_usdc": round(montant, 6),
                "bloc": int(str(entree.get("blockNumber") or "0x0"), 16),
            }
        )
    return {
        "disponible": True,
        "bloc_courant": bloc_courant,
        "bloc_debut": debut,
        "nombre": len(transferts),
        "montant_usdc": round(sum(t["montant_usdc"] for t in transferts), 6),
        "transferts": transferts[-5:],
    }


# --------------------------------------------------------------------------- #
# Etat, changements et rapport
# --------------------------------------------------------------------------- #


def charger_etat(chemin: Path) -> dict[str, Any]:
    """Etat JSON du passage precedent, ou `{}` s'il n'existe pas."""
    try:
        return json.loads(chemin.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def sauver_etat(chemin: Path, etat: dict[str, Any]) -> None:
    """Ecrit l'etat (cree le dossier si besoin)."""
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(json.dumps(etat, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def diff_etat(ancien: dict[str, Any], nouveau: dict[str, Any]) -> list[str]:
    """Changements qui meritent une alerte entre deux passages."""
    alertes: list[str] = []
    if ancien.get("reels_cumul") is not None and nouveau.get("reels_cumul", 0) > int(ancien.get("reels_cumul") or 0):
        alertes.append(
            f"nouveau paiement : {nouveau.get('reels_cumul')} validation(s) payee(s) "
            f"({nouveau.get('revenu_cumul_usdc', 0)} USDC cumules)"
        )
    ancien_paiement = str(ancien.get("dernier_paiement") or "")
    if nouveau.get("dernier_paiement") and nouveau["dernier_paiement"] != ancien_paiement:
        alertes.append(f"dernier paiement observe : {nouveau['dernier_paiement']}")
    for depot, version in (nouveau.get("releases") or {}).items():
        if not version.get("disponible"):
            continue
        ancienne_version = str(((ancien.get("releases") or {}).get(depot) or {}).get("version") or "")
        if str(version.get("version")) != ancienne_version:
            alertes.append(f"nouvelle release {depot} : {version.get('version')}")
    anciennes_classes = set((ancien.get("classes_agents") or {}).keys())
    nouvelles_classes = set(((nouveau.get("journal") or {}).get("classes") or {}).keys())
    if nouvelles_classes - anciennes_classes:
        alertes.append(
            "nouvelle(s) classe(s) d'appelant : " + ", ".join(sorted(nouvelles_classes - anciennes_classes))
        )
    if (nouveau.get("onchain") or {}).get("nombre", 0) > 0:
        alertes.append(
            f"transfert(s) USDC on-chain : {(nouveau.get('onchain') or {}).get('nombre')} "
            f"({(nouveau.get('onchain') or {}).get('montant_usdc')} USDC)"
        )
    return alertes


def formater_markdown(donnees: dict[str, Any]) -> str:
    """Rapport lisible, ~60 lignes, sans secret et sans jargon inutile."""
    revenu_ = donnees.get("revenu") or {}
    journal = donnees.get("journal") or {}
    acces = donnees.get("acces") or {}
    ecosysteme = donnees.get("ecosysteme") or {}
    onchain = donnees.get("onchain") or {}
    alertes = donnees.get("alertes") or []
    indisponibles = donnees.get("sources_indisponibles") or []

    def oui_non(valeur: bool) -> str:
        return "oui" if valeur else "non"

    lignes = [
        f"# Veille agentique — {donnees.get('genere_le')}",
        "",
        "> Cout : 0 EUR (sources publiques + nœud Base + journal local). "
        "Aucun email n'est envoye sans `--email`.",
        "",
        "## 1. Revenu (registre durable)",
    ]
    if revenu_.get("disponible"):
        lignes.extend(
            [
                f"- reel cumule : **{revenu_.get('reels_cumul')} paiement(s)** / "
                f"**{revenu_.get('revenu_cumul_usdc')} USDC** — {revenu_.get('payeurs')} payeur(s)",
                f"- 24 h : {revenu_.get('fenetre_24h', {}).get('paiements')} paiement(s) / "
                f"{revenu_.get('fenetre_24h', {}).get('revenu_usdc')} USDC",
                f"- 7 j : {revenu_.get('fenetre_7j', {}).get('paiements')} paiement(s) / "
                f"{revenu_.get('fenetre_7j', {}).get('revenu_usdc')} USDC",
                f"- dernier paiement observe : {revenu_.get('dernier_paiement') or 'aucun'}",
            ]
        )
    else:
        lignes.append(f"- **registre indisponible** : {revenu_.get('erreur')} (ne pas lire un zero)")
    lignes.extend(["", "## 2. Agents et conversion (journal de decouverte)"])
    if journal.get("disponible"):
        lignes.extend(
            [
                f"- refus 402 sur la fenetre : **{journal.get('refus_402_fenetre')}** "
                f"(cumul journal : {journal.get('refus_402_total')})",
                f"- classes : {journal.get('classes')}",
                f"- agents : {journal.get('agents')}",
                f"- endpoints : {journal.get('endpoints')}",
                f"- agents distincts : {journal.get('agents_distincts')}",
            ]
        )
    else:
        lignes.append(f"- **journal indisponible** : {journal.get('erreur')}")
    lignes.extend(["", "## 3. Acces Nginx (`/validate`, `/mcp`)"])
    if acces.get("disponible"):
        lignes.append(f"- compteurs : {acces.get('compteurs') or 'aucun appel dans la fenetre'}")
    else:
        lignes.append(f"- **acces indisponible** : {acces.get('erreur')}")
    lignes.extend(["", "## 4. Ecosysteme x402 (GitHub / index / Bazaar)"])
    for depot, version in (ecosysteme.get("releases") or {}).items():
        if version.get("disponible"):
            lignes.append(f"- `{depot}` : **{version.get('version')}** ({version.get('publie_le')})")
        else:
            lignes.append(f"- `{depot}` : indisponible ({version.get('erreur')})")
    if (ecosysteme.get("index") or {}).get("disponible"):
        lignes.append(f"- x402 Discovery Index : {ecosysteme['index'].get('api_annoncees')} API annoncees")
    else:
        lignes.append(f"- x402 Discovery Index : indisponible ({ecosysteme.get('index', {}).get('erreur')})")
    bazaar_ = ecosysteme.get("bazaar") or {}
    if bazaar_.get("disponible"):
        lignes.append(f"- Bazaar : {bazaar_.get('entrees')} entree(s), nous listes : {oui_non(bool(bazaar_.get('nous_listes')))}")
    else:
        lignes.append(f"- Bazaar : indisponible ({bazaar_.get('erreur')})")
    lignes.extend(["", "## 5. USDC recus on-chain (Base)"])
    if onchain.get("disponible"):
        lignes.append(
            f"- {onchain.get('nombre')} transfert(s) / {onchain.get('montant_usdc')} USDC "
            f"(blocs {onchain.get('bloc_debut')} -> {onchain.get('bloc_courant')})"
        )
        for transfert in onchain.get("transferts") or []:
            lignes.append(f"  - {transfert.get('montant_usdc')} USDC de {transfert.get('de')} (tx {str(transfert.get('tx'))[:18]}...)")
    else:
        lignes.append(f"- **on-chain indisponible** : {onchain.get('erreur')}")
    lignes.extend(["", "## 6. Alertes"])
    lignes.extend([f"- {alerte}" for alerte in alertes] or ["- aucun changement notable"])
    if indisponibles:
        lignes.extend(["", "## 7. Sources indisponibles (l'absence est une donnee)"])
        lignes.extend([f"- {source}" for source in indisponibles])
    lignes.extend(["", f"_Genere par `scripts/veille_agentique.py` — {donnees.get('genere_le')}._", ""])
    return "\n".join(lignes)


# --------------------------------------------------------------------------- #
# Email (optionnel : SMTP_* deja present dans `.env` du VPS)
# --------------------------------------------------------------------------- #


def envoyer_email(sujet: str, texte: str) -> tuple[bool, str]:
    """Envoie le digest si SMTP_* est configure ; sinon dit pourquoi il n'envoie pas."""
    hote = os.environ.get("SMTP_HOST", "").strip()
    port = os.environ.get("SMTP_PORT", "").strip() or "587"
    utilisateur = os.environ.get("SMTP_USER", "").strip()
    mot_de_passe = os.environ.get("SMTP_PASSWORD", "").strip()
    expediteur = os.environ.get("SMTP_FROM", "").strip() or utilisateur
    destinataire = (
        os.environ.get("VEILLE_MAIL_TO", "").strip()
        or os.environ.get("CONTACT_EMAIL", "").strip()
        or expediteur
    )
    if not (hote and utilisateur and mot_de_passe and expediteur and destinataire):
        return False, "SMTP_* incomplet (SMTP_HOST/SMTP_USER/SMTP_PASSWORD/SMTP_FROM) : pas d'email"
    message = EmailMessage()
    message["Subject"] = sujet
    message["From"] = expediteur
    message["To"] = destinataire
    message.set_content(texte)
    try:
        with smtplib.SMTP(hote, int(port), timeout=20) as serveur:
            serveur.starttls()
            serveur.login(utilisateur, mot_de_passe)
            serveur.send_message(message)
        return True, f"email envoye a {destinataire}"
    except Exception as erreur:  # noqa: BLE001 - l'echec est dit, pas masque
        return False, f"email non envoye : {type(erreur).__name__}"


# --------------------------------------------------------------------------- #
# Programme
# --------------------------------------------------------------------------- #


def construire_donnees(
    *, sans_reseau: bool, fenetre_texte: str, chemin_acces: str | None, dernier_bloc: int | None = None
) -> dict[str, Any]:
    """Reunit toutes les sources ; une source en echec est notee, pas fatale."""
    depuis = depuis_texte(fenetre_texte)
    donnees: dict[str, Any] = {"genere_le": maintenant().isoformat(timespec="seconds"), "fenetre": fenetre_texte}
    indisponibles: list[str] = []
    donnees["revenu"] = revenu()
    if not donnees["revenu"].get("disponible"):
        indisponibles.append(f"revenu : {donnees['revenu'].get('erreur')}")
    donnees["journal"] = journal_decouverte(depuis)
    if not donnees["journal"].get("disponible"):
        indisponibles.append(f"journal : {donnees['journal'].get('erreur')}")
    acces = analyser_acces(chemin_acces, depuis) if chemin_acces else {"disponible": False, "erreur": "non demande"}
    donnees["acces"] = acces
    if chemin_acces and not acces.get("disponible"):
        indisponibles.append(f"acces : {acces.get('erreur')}")
    if sans_reseau:
        donnees["ecosysteme"] = {"releases": {}, "index": {"disponible": False, "erreur": "mode --sans-reseau"}, "bazaar": {"disponible": False, "erreur": "mode --sans-reseau"}}
        donnees["onchain"] = {"disponible": False, "erreur": "mode --sans-reseau"}
        donnees["sources_indisponibles"] = indisponibles + ["ecosysteme : mode --sans-reseau", "on-chain : mode --sans-reseau"]
        return donnees
    donnees["ecosysteme"] = {"releases": releases_github(), "index": index_x402(), "bazaar": bazaar()}
    if not donnees["ecosysteme"]["index"].get("disponible"):
        indisponibles.append(f"index x402 : {donnees['ecosysteme']['index'].get('erreur')}")
    if not donnees["ecosysteme"]["bazaar"].get("disponible"):
        indisponibles.append(f"bazaar : {donnees['ecosysteme']['bazaar'].get('erreur')}")
    donnees["onchain"] = transferts_usdc(dernier_bloc)
    if not donnees["onchain"].get("disponible"):
        indisponibles.append(f"on-chain : {donnees['onchain'].get('erreur')}")
    donnees["sources_indisponibles"] = indisponibles
    return donnees


def preparer_etat(donnees: dict[str, Any], ancien: dict[str, Any]) -> dict[str, Any]:
    """Etat minimal pour le prochain diff (pas de donnee personnelle)."""
    return {
        "maj": donnees.get("genere_le"),
        "reels_cumul": (donnees.get("revenu") or {}).get("reels_cumul"),
        "revenu_cumul_usdc": (donnees.get("revenu") or {}).get("revenu_cumul_usdc"),
        "dernier_paiement": (donnees.get("revenu") or {}).get("dernier_paiement"),
        "releases": {depot: {"version": version.get("version")} for depot, version in ((donnees.get("ecosysteme") or {}).get("releases") or {}).items() if version.get("disponible")},
        "classes_agents": (donnees.get("journal") or {}).get("classes"),
        "dernier_bloc_base": (donnees.get("onchain") or {}).get("bloc_courant") or ancien.get("dernier_bloc_base"),
        "refus_402_total": (donnees.get("journal") or {}).get("refus_402_total"),
    }


def main(argv: list[str] | None = None) -> int:
    """Genere le digest, met a jour l'etat, envoie l'email si demande."""
    analyseur = argparse.ArgumentParser(
        description="Veille agentique quotidienne (x402, agents, revenu) — cout 0 EUR.",
    )
    analyseur.add_argument("--sortie", type=Path, help=f"rapport markdown (defaut : {SORTIE_DEFAUT})")
    analyseur.add_argument("--etat", type=Path, help="etat JSON (defaut : data/veille_agentique_etat.json)")
    analyseur.add_argument("--depuis", default=FENETRE_DEFAUT, help="fenetre du journal : 24h, 7d, 30d")
    analyseur.add_argument("--acces", default="/var/log/nginx/access.log", help="journal Nginx combined ('' pour ignorer)")
    analyseur.add_argument("--sans-reseau", action="store_true", help="sources internes seulement")
    analyseur.add_argument("--sans-acces", action="store_true", help="ne pas lire le journal Nginx")
    analyseur.add_argument("--email", action="store_true", help="envoyer le digest si SMTP_* est configure")
    analyseur.add_argument("--json", action="store_true", help="afficher les donnees brutes au lieu du texte")
    args = analyseur.parse_args(argv)

    sortie = args.sortie or Path(os.environ.get("VEILLE_SORTIE", str(SORTIE_DEFAUT)))
    etat_chemin = args.etat or Path(os.environ.get("VEILLE_ETAT", str(ROOT / "data" / "veille_agentique_etat.json")))
    chemin_acces = None if args.sans_acces else (args.acces or None)

    ancien = charger_etat(etat_chemin)
    donnees = construire_donnees(
        sans_reseau=args.sans_reseau,
        fenetre_texte=args.depuis,
        chemin_acces=chemin_acces,
        dernier_bloc=(ancien.get("dernier_bloc_base") if isinstance(ancien.get("dernier_bloc_base"), int) else None),
    )
    donnees["alertes"] = diff_etat(ancien, donnees)
    rapport = formater_markdown(donnees)

    if not args.json:
        print(rapport)
    else:
        print(json.dumps(donnees, ensure_ascii=False, indent=2))

    try:
        sortie.parent.mkdir(parents=True, exist_ok=True)
        sortie.write_text(rapport + "\n", encoding="utf-8")
        print(f"\n[--] rapport ecrit : {sortie}", file=sys.stderr)
    except OSError as erreur:
        print(f"[!!] rapport non ecrit : {erreur}", file=sys.stderr)

    try:
        sauver_etat(etat_chemin, preparer_etat(donnees, ancien))
        print(f"[--] etat ecrit : {etat_chemin}", file=sys.stderr)
    except OSError as erreur:
        print(f"[!!] etat non ecrit : {erreur}", file=sys.stderr)

    if args.email:
        envoye, detail = envoyer_email(
            f"[address-validator] veille agentique {donnees['genere_le'][:10]}",
            rapport,
        )
        print(f"[--] {detail}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
