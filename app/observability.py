"""Journal des tentatives non payées — décision D30.

But : savoir si l'API est **trouvée**. Sans ce journal, un agent qui découvre
`/validate` et repart avec un `402` est invisible ; avec lui, on mesure le
fossé entre « découvre » et « paie », qui est exactement le sujet du chantier
de découvrabilité.

Deux principes non négociables :

1. **Minimisation (RGPD).** La ligne ne contient jamais l'adresse validée :
   uniquement des métadonnées de requête. L'IP est pseudonymisée par défaut
   (SHA-256 + sel) ; `DISCOVERY_LOG_RAW_IP=true` la met en clair, ce qui doit
   rester une mesure d'enquête datée, pas un réglage de confort.
2. **Foi dans `X-Forwarded-For` seulement derrière le proxy, et seulement dans
   ce que le proxy y écrit (D56).** L'application écoute sur `127.0.0.1` (D21).
   Nginx **ajoute** l'adresse réelle à la **fin** de l'en-tête reçu
   (`$proxy_add_x_forwarded_for`) : tout ce qui la précède a été écrit par le
   client. On lit donc la **dernière** entrée, jamais la première — lire la
   première laissait un visiteur choisir son IP, donc contourner les plafonds
   par IP de l'essai gratuit (D46) et de l'inscription (D44).

Le journal est un fichier **JSONL** (une ligne = un objet JSON) : il se lit
avec `grep`/`jq`, se compte par jour, et se purge sans réécriture partielle.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Iterator

from fastapi import Request

from app.config import get_settings

logger = logging.getLogger("address_validator.discovery")

#: Handlers déjà posés, par chemin de fichier (idempotence entre appels).
_ATTACHED: set[str] = set()


def _salt() -> bytes:
    """Sel de pseudonymisation : dédié s'il est fourni, dérivé du secret sinon."""
    settings = get_settings()
    if settings.discovery_log_salt:
        return settings.discovery_log_salt.encode()
    return hashlib.sha256(f"ip-salt:{settings.api_secret_key}".encode()).digest()


def client_ip(request: Request) -> str:
    """IP du client : **dernière** entrée de `X-Forwarded-For` (celle que Nginx
    ajoute), sinon la socket. Les entrées précédentes viennent du client (D56)."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        last = forwarded.split(",")[-1].strip()
        if last:
            return last
    return request.client.host if request.client else "unknown"


def pseudonymise(ip: str) -> str:
    """Pseudonyme stable d'une IP (32 premiers caractères hex du SHA-256 salé)."""
    digest = hashlib.sha256(_salt() + b"|" + ip.encode()).hexdigest()
    return digest[:32]


def configure_logging() -> None:
    """Pose le handler fichier si `DISCOVERY_LOG_PATH` est défini (idempotent)."""
    settings = get_settings()
    if not settings.discovery_log_enabled or not settings.discovery_log_path:
        return
    target = str(Path(settings.discovery_log_path))
    if target in _ATTACHED:
        return
    try:
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        _ATTACHED.add(target)
    except OSError as exc:  # pragma: no cover - dépend du systeme de fichiers
        # Un journal impossible a ecrire ne doit JAMAIS faire echouer une
        # reponse payante ou un 402 : on degrade, on ne casse pas.
        logger.warning("journal de decouverte non activable (%s) : %s", target, exc)


def log_unpaid_attempt(
    request: Request, price: float, reason: str = "missing_or_invalid_proof"
) -> dict:
    """Trace une tentative refusée en 402 et renvoie l'entrée écrite."""
    settings = get_settings()
    raw_ip = client_ip(request)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "event": "unpaid_request",
        "endpoint": request.url.path,
        "method": request.method,
        "price_usdc": price,
        "reason": reason,
        "user_agent": request.headers.get("user-agent", "")[:256],
        "referer": request.headers.get("referer", "")[:256],
    }
    entry["ip"] = raw_ip if settings.discovery_log_raw_ip else pseudonymise(raw_ip)
    entry["ip_pseudonymised"] = not settings.discovery_log_raw_ip

    if not settings.discovery_log_enabled:
        return entry

    configure_logging()
    logger.info(json.dumps(entry, ensure_ascii=False, sort_keys=True))
    return entry


def read_events(path: str | Path) -> Iterator[dict]:
    """Lit un journal JSONL en ignorant les lignes illisibles."""
    file = Path(path)
    if not file.is_file():
        return
    with file.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def count_since(path: str | Path, since: datetime) -> int:
    """Nombre d'événements postérieurs à `since` (comparaison sur `ts`)."""
    total = 0
    for event in read_events(path):
        stamp = _parse_ts(event.get("ts", ""))
        if stamp is not None and stamp >= since:
            total += 1
    return total


def prune(path: str | Path, retention_days: int) -> int:
    """Supprime les entrées plus vieilles que `retention_days`. Renvoie le reste.

    Conservation annoncée = conservation appliquée (PRIVACY.md §3) : le rapport
    quotidien appelle cette fonction, la rétention n'est donc pas une intention.
    """
    file = Path(path)
    if not file.is_file():
        return 0
    limit = datetime.now(timezone.utc) - timedelta(days=retention_days)
    kept: list[str] = []
    for event in read_events(file):
        stamp = _parse_ts(event.get("ts", ""))
        if stamp is None or stamp >= limit:
            kept.append(json.dumps(event, ensure_ascii=False, sort_keys=True))
    file.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    return len(kept)


def _parse_ts(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


# --- Agents IA : les rendre observables plutot que les bloquer (D67) ---------
#
# Le trafic des agents IA (GPTBot, ClaudeBot, PerplexityBot, Google-Extended...)
# est une **distribution** avant d'etre une charge : un agent qui lit la page de
# tarifs est un prospect sans navigateur. Le bloquer coupe la decouverte ; ne
# pas le voir laisse croire que personne ne vient. Trois obligations en
# decoulent, tenues ici :
#
#   1. **classer** chaque appelant (`agent_class`, `agent_name`) ;
#   2. **journaliser** les agents IA et les annuaires MCP dans le journal JSONL
#      de D30 (`record_request`) — jamais les visites humaines, qui n'y
#      ajouteraient que du bruit ;
#   3. **publier** les cinq mesures (`agents`, `top_ip`, `top_agents`,
#      `top_pages`, `conversion`) en direct sur `GET /metrics/agents` et sur
#      disque via `scripts/analyser_agents.py`.
#
# La minimisation RGPD ne bouge pas : aucune adresse validee n'est journalisee,
# et l'adresse d'un agent IA reste pseudonymisee (sha256 + sel) tant que
# `DISCOVERY_LOG_RAW_IP` reste a `false`.
#
# Les noms et domaines ci-dessous sont ceux que les editeurs **publient**
# (`docs/AGENTS-IA.md`) : une liste blanche publiee est verifiable par l'agent
# lui-meme, une liste de blocage cachee ne l'est pas. Aucune de ces valeurs ne
# sert a refuser un appel : le classement est un fait, pas une defense.

#: Classes d'appelants rendues par `agent_class`. L'ordre est celui de la
#: decision : un agent IA reste un agent IA, meme lorsque l'editeur ajoute son
#: jeton a un `Mozilla/5.0` de navigateur.
CLASSES_APPELANTS = (
    "agent_ia",
    "annuaire_mcp",
    "moteur",
    "outil",
    "navigateur",
    "inconnu",
)

#: Agents IA d'entrainement et d'assistance : jeton de User-Agent ->
#: (nom publie, editeur, domaine de l'editeur).
AGENTS_IA = {
    "gptbot": ("GPTBot", "OpenAI", "openai.com"),
    "oai-searchbot": ("OAI-SearchBot", "OpenAI", "openai.com"),
    "chatgpt-user": ("ChatGPT-User", "OpenAI", "openai.com"),
    "claudebot": ("ClaudeBot", "Anthropic", "anthropic.com"),
    "claude-user": ("Claude-User", "Anthropic", "anthropic.com"),
    "claude-searchbot": ("Claude-SearchBot", "Anthropic", "anthropic.com"),
    "anthropic-ai": ("anthropic-ai", "Anthropic", "anthropic.com"),
    "perplexitybot": ("PerplexityBot", "Perplexity", "perplexity.ai"),
    "perplexity-user": ("Perplexity-User", "Perplexity", "perplexity.ai"),
    "google-extended": ("Google-Extended", "Google", "google.com"),
    "applebot-extended": ("Applebot-Extended", "Apple", "apple.com"),
    "ccbot": ("CCBot", "Common Crawl", "commoncrawl.org"),
    "bytespider": ("Bytespider", "ByteDance", "bytedance.com"),
    "amazonbot": ("Amazonbot", "Amazon", "amazon.com"),
    "meta-externalagent": ("meta-externalagent", "Meta", "meta.com"),
    "facebookbot": ("FacebookBot", "Meta", "meta.com"),
    "cohere-ai": ("cohere-ai", "Cohere", "cohere.com"),
    "youbot": ("YouBot", "You.com", "you.com"),
    "mistralai-user": ("MistralAI-User", "Mistral AI", "mistral.ai"),
    "duckassistbot": ("DuckAssistBot", "DuckDuckGo", "duckduckgo.com"),
    "ai2bot": ("AI2Bot", "Allen Institute for AI", "allenai.org"),
    "imagesiftbot": ("ImagesiftBot", "Imagesift", "imagesift.com"),
    "diffbot": ("Diffbot", "Diffbot", "diffbot.com"),
    "timpibot": ("Timpibot", "Timpi", "timpi.io"),
    "omgili": ("omgili", "Webz.io", "webz.io"),
}

#: Annuaires et sondes MCP : pas des agents d'entrainement, mais ils annoncent
#: le service a des clients MCP — donc ils font la decouverte. Releves dans
#: `AUDIT_TRAFIC_VPS.md` §2-3 ; l'editeur est celui que le User-Agent annonce
#: lui-meme, « non publie » quand il ne dit rien.
ANNUAIRES_MCP = {
    "mcpbeat": ("mcpbeat", "MCPBeat", "mcpbeat.com"),
    "agentrankprobe": ("AgentRankProbe", "non publie", "spark.tail6ec125.ts.net"),
    "rokmcp-collector": ("rokmcp-collector", "non publie", ""),
    "mcpwatch": ("MCPWatch", "recherche securite MCP", ""),
    "everymcp-probe": ("EveryMCP-Probe", "EveryMCP", ""),
    "protogrid-probe": ("protogrid-probe", "Protogrid", ""),
    "golemreachtrustbot": ("GolemreachTrustBot", "Golemreach", ""),
}

#: Moteurs de recherche classiques : indexation, pas d'entrainement. Les voir
#: a part evite de compter Googlebot comme un agent IA.
MOTEURS = {
    "googlebot": "Googlebot",
    "bingbot": "Bingbot",
    "msnbot": "MSNBot",
    "yandexbot": "YandexBot",
    "duckduckbot": "DuckDuckBot",
    "baiduspider": "Baiduspider",
    "applebot": "Applebot",
    "slurp": "Yahoo Slurp",
    "petalbot": "PetalBot",
    "sogou": "Sogou",
    "exabot": "Exabot",
    "seznambot": "SeznamBot",
    "qwantify": "Qwantify",
    "mojeekbot": "MojeekBot",
}

#: Programmes clients : outils de ligne de commande, SDK, aspirateurs SEO,
#: scanners. Le nom publie sert au top des appelants, pas a un refus.
OUTILS = {
    "curl": "curl",
    "wget": "wget",
    "python-requests": "python-requests",
    "python-urllib": "Python-urllib",
    "python/": "Python",
    "aiohttp": "aiohttp",
    "httpx": "httpx",
    "go-http-client": "Go-http-client",
    "node": "node",
    "undici": "undici",
    "axios": "axios",
    "okhttp": "OkHttp",
    "postmanruntime": "Postman",
    "insomnia": "Insomnia",
    "powershell": "PowerShell",
    "uptimerobot": "UptimeRobot",
    "kube-probe": "kube-probe",
    "prometheus": "Prometheus",
    "blackbox": "blackbox_exporter",
    "semrushbot": "SemrushBot",
    "ahrefsbot": "AhrefsBot",
    "mj12bot": "MJ12bot",
    "dotbot": "DotBot",
    "dataforseo": "DataForSEO",
    "blexbot": "BLEXBot",
    "serpstatbot": "SerpstatBot",
    "screaming frog": "Screaming Frog",
    "sitebulb": "Sitebulb",
    "zgrab": "zgrab",
    "masscan": "masscan",
    "nmap": "Nmap",
    "nikto": "Nikto",
    "sqlmap": "sqlmap",
    "nuclei": "Nuclei",
    "gobuster": "gobuster",
    "dirbuster": "DirBuster",
    "censys": "Censys",
    "internetmeasurement": "InternetMeasurement",
}

#: Jetons qui trahissent un vrai navigateur. `Mozilla/5.0` seul ne dit rien :
#: tous les robots l'ecrivent pour rester compatibles.
JETONS_NAVIGATEUR = (
    "applewebkit",
    "gecko/",
    "chrome/",
    "chromium/",
    "safari/",
    "firefox/",
    "edg/",
    "opr/",
    "trident/",
)

#: Routes dont un `200` signifie « le paiement a ete accepte ». C'est la seule
#: porte de conversion du service : `/validate` refuse en `402` sans preuve.
ROUTES_PAYANTES = ("/validate",)


def _table_detection() -> tuple[tuple[str, str, str], ...]:
    """Table `(jeton, classe, nom)` triee du jeton le plus long au plus court.

    Le tri garantit qu'un jeton precis (`applebot-extended`) l'emporte sur son
    prefixe (`applebot`) quel que soit l'ordre de declaration — un prefixe qui
    gagne ferait dire « Applebot » a un robot d'entrainement.
    """
    lignes: list[tuple[str, str, str]] = []
    for jeton, (nom, _, _) in AGENTS_IA.items():
        lignes.append((jeton, "agent_ia", nom))
    for jeton, (nom, _, _) in ANNUAIRES_MCP.items():
        lignes.append((jeton, "annuaire_mcp", nom))
    for jeton, nom in MOTEURS.items():
        lignes.append((jeton, "moteur", nom))
    for jeton, nom in OUTILS.items():
        lignes.append((jeton, "outil", nom))
    return tuple(sorted(lignes, key=lambda ligne: -len(ligne[0])))


_TABLE = _table_detection()

#: Table inverse `nom publie -> (editeur, domaine)` : nommer un agent dans un
#: rapport ne doit pas obliger a relire la table de detection.
_PAR_NOM = {
    nom: (editeur, domaine)
    for nom, editeur, domaine in (
        list(AGENTS_IA.values()) + list(ANNUAIRES_MCP.values())
    )
}


def _detecter(user_agent: str) -> tuple[str, str]:
    """`(classe, nom publie)` d'un User-Agent. Sans exception, sans `None`."""
    ua = (user_agent or "").lower()
    for jeton, classe, nom in _TABLE:
        if jeton in ua:
            return (classe, nom)
    if any(jeton in ua for jeton in JETONS_NAVIGATEUR):
        return ("navigateur", "navigateur")
    return ("inconnu", "inconnu")


def agent_class(user_agent: str) -> str:
    """Classe d'appelant, parmi `CLASSES_APPELANTS`."""
    return _detecter(user_agent)[0]


def agent_name(user_agent: str) -> str:
    """Nom publie de l'appelant (« GPTBot », « curl », « navigateur »...)."""
    return _detecter(user_agent)[1]


def agent_editeur(nom: str) -> str:
    """Editeur publie d'un agent reconnu, chaine vide pour les autres."""
    return _PAR_NOM.get(nom, ("", ""))[0]


def is_ai_agent(user_agent: str) -> bool:
    """Vrai si l'appelant est un agent IA reconnu (liste blanche publiee)."""
    return agent_class(user_agent) == "agent_ia"


def ai_agent_whitelist() -> list[dict]:
    """Liste blanche publiee : qui est reconnu, par qui, et ou le lire.

    Elle est *publiee* volontairement (`GET /metrics/agents`, `llms.txt`) : un
    agent qui se demande s'il est reconnu n'a pas a lire notre code source.
    """
    return [
        {"jeton": jeton, "agent": nom, "editeur": editeur, "domaine": domaine}
        for jeton, (nom, editeur, domaine) in sorted(
            AGENTS_IA.items(), key=lambda kv: kv[1][0].lower()
        )
    ]


# --- Lecture des journaux : fenetre, format Nginx, mesures (D67) ------------


def depuis(texte: str) -> datetime | None:
    """Instant de depart lu dans `--since` : duree (`24h`, `7d`) ou date ISO.

    Une duree est comptee a rebours depuis maintenant, une date ISO sans fuseau
    est prise en UTC (c'est le fuseau du journal, D30). Un texte illisible rend
    `None` : l'appelant lit alors tout, et le rapport l'ecrit — une fenetre
    silencieusement ignoree ferait lire 30 jours en croyant lire 24 heures.
    """
    texte = (texte or "").strip()
    if not texte:
        return None
    duree = re.fullmatch(r"(\d+)\s*([smhdj])", texte.lower())
    if duree:
        valeur = int(duree.group(1))
        unites = {
            "s": timedelta(seconds=valeur),
            "m": timedelta(minutes=valeur),
            "h": timedelta(hours=valeur),
            "d": timedelta(days=valeur),
            "j": timedelta(days=valeur),
        }
        return datetime.now(timezone.utc) - unites[duree.group(2)]
    try:
        moment_lu = datetime.fromisoformat(texte.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment_lu.tzinfo is None:
        moment_lu = moment_lu.replace(tzinfo=timezone.utc)
    return moment_lu


def moment(record: dict) -> datetime | None:
    """Instant d'une entree de journal (`ts`) ou d'une ligne Nginx (`moment`).

    Un `ts` sans fuseau est repute UTC : comparer un instant naif a un instant
    conscient leverait `TypeError`, et une lecture qui leve ne mesure rien.
    """
    valeur = record.get("moment")
    if isinstance(valeur, datetime):
        return valeur if valeur.tzinfo else valeur.replace(tzinfo=timezone.utc)
    lu = _parse_ts(record.get("ts", ""))
    if lu is None:
        return None
    return lu if lu.tzinfo else lu.replace(tzinfo=timezone.utc)


def filtrer_depuis(events: Iterable[dict], depuis_le: datetime | None) -> Iterator[dict]:
    """Evenements posterieurs a `depuis_le` (sans borne, tout passe).

    Un evenement sans horodatage lisible est conserve plutot qu'ecarte : le
    journal est ecrit par l'application elle-meme, une ligne sans `ts` est une
    anomalie a voir, pas une ligne a cacher.
    """
    for event in events:
        if depuis_le is None:
            yield event
            continue
        stamp = moment(event)
        if stamp is None or stamp >= depuis_le:
            yield event


#: Format `combined`, celui de `nginx.conf` :
#: `$remote_addr - $remote_user [$time_local] "$request" $status
#: $body_bytes_sent "$http_referer" "$http_user_agent"`.
LIGNE_ACCES = re.compile(
    r'^(?P<ip>\S+) \S+ \S+ \[(?P<moment>[^\]]+)\] '
    r'"(?P<requete>[^"]*)" (?P<statut>\d{3}) (?P<octets>\d+|-) '
    r'"(?P<referer>[^"]*)" "(?P<agent>[^"]*)"'
)

#: Mois anglais du journal Nginx. `strptime('%b')` depend de la locale de la
#: machine : un rapport ne doit pas changer de sens selon la langue du poste.
MOIS_NGINX = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _moment_nginx(texte: str) -> datetime | None:
    """`15/Oct/2026:10:23:45 +0200` -> instant UTC (`None` si illisible)."""
    trouve = re.match(
        r"(\d{2})/(\w{3})/(\d{4}):(\d{2}):(\d{2}):(\d{2})\s*([+-]\d{4})?",
        texte.strip(),
    )
    if not trouve:
        return None
    jour, mois, annee, heure, minute, seconde, decalage = trouve.groups()
    numero = MOIS_NGINX.get(mois.lower())
    if numero is None:
        return None
    fuseau = timezone.utc
    if decalage:
        signe = 1 if decalage[0] == "+" else -1
        fuseau = timezone(
            signe * timedelta(hours=int(decalage[1:3]), minutes=int(decalage[3:5]))
        )
    return datetime(
        int(annee), numero, int(jour), int(heure), int(minute), int(seconde),
        tzinfo=fuseau,
    ).astimezone(timezone.utc)


def parse_nginx_line(ligne: str) -> dict | None:
    """Une ligne d'acces Nginx -> mesure d'appelant, `None` si illisible.

    La classe et le nom sont poses ici, par le meme `_detecter` que le trafic en
    direct : le rapport relu sur disque et l'endpoint en memoire ne peuvent donc
    pas se contredire sur ce qu'est un agent. Une ligne dont la date est
    illisible est ecartee : sans instant, elle ne peut entrer dans aucune
    fenetre, et la compter produirait un total irreproductible.
    """
    trouve = LIGNE_ACCES.match(ligne.strip())
    if not trouve:
        return None
    champs = trouve.groupdict()
    instant = _moment_nginx(champs["moment"])
    if instant is None:
        return None
    parties = champs["requete"].split()
    user_agent = champs["agent"]
    return {
        "ip": champs["ip"],
        "moment": instant,
        "methode": parties[0] if len(parties) >= 2 else "-",
        "chemin": parties[1].split("?")[0] if len(parties) >= 2 else champs["requete"],
        "statut": int(champs["statut"]),
        "octets": int(champs["octets"]) if champs["octets"].isdigit() else 0,
        "referer": champs["referer"],
        "user_agent": user_agent,
        "classe": agent_class(user_agent),
        "agent": agent_name(user_agent),
    }


def lire_access_log(
    chemin: str | Path, depuis_le: datetime | None = None
) -> Iterator[dict]:
    """Lit un journal d'acces Nginx en ignorant les lignes illisibles.

    La fenetre est appliquee a la lecture : un `access.log` de plusieurs
    centaines de Mo ne doit pas etre charge en memoire pour en garder une heure.
    La lecture se fait en `errors="replace"` : un octet invalide dans un
    User-Agent ne doit pas priver de tout le reste du journal.
    """
    fichier = Path(chemin)
    if not fichier.is_file():
        return
    with fichier.open(encoding="utf-8", errors="replace") as flux:
        for ligne in flux:
            ligne = ligne.strip()
            if not ligne:
                continue
            mesure = parse_nginx_line(ligne)
            if mesure is None:
                continue
            if depuis_le is not None:
                stamp = mesure["moment"]
                if stamp is not None and stamp < depuis_le:
                    continue
            yield mesure


# --- Agregation : les cinq mesures demandees (D67) --------------------------


def _mesure(record: dict) -> tuple[str, str, str, str, int | None]:
    """Normalise une entree de journal ou une ligne d'acces en mesure.

    Deux ecritures coexistent : le journal de D30 nomme ses cles `endpoint` et
    `status`, les mesures d'appelant les nomment `chemin` et `statut`. Une
    normalisation unique evite qu'une meme ligne soit comptee deux fois — ou
    jamais — selon sa provenance.
    """
    user_agent = str(record.get("user_agent") or "")
    classe = str(record.get("classe") or agent_class(user_agent))
    nom = str(record.get("agent") or agent_name(user_agent))
    adresse = str(record.get("ip") or "inconnue")
    chemin = str(record.get("chemin") or record.get("endpoint") or "-")
    brut = record.get("statut", record.get("status"))
    try:
        statut = int(brut) if brut is not None else None
    except (TypeError, ValueError):
        statut = None
    return (classe, nom, adresse, chemin, statut)


def _trier(comptes: dict[str, int], limite: int) -> list[tuple[str, int]]:
    """Top d'un compteur : decroissant, puis alphabetique a egalite.

    Les ex aequo sont tranches par le nom et non par l'ordre d'arrivee : un
    rapport doit etre identique lu deux fois, sinon il n'est pas comparable
    d'une semaine a l'autre.
    """
    return sorted(comptes.items(), key=lambda paire: (-paire[1], paire[0]))[: max(0, limite)]


def conversion(requetes: int, payees: int) -> dict:
    """Taux de conversion des agents IA : `200` paye / requetes d'agent IA.

    `/validate` est la seule porte payante : un `200` y signifie que le paiement
    a ete accepte, un `402` qu'il ne l'a pas ete. Le taux est donc un rapport
    mesure, pas une estimation — et il vaut 0 sans requete plutot que de lever.
    """
    return {
        "definition": (
            "rapport entre les reponses 200 sur une route payante (/validate) et "
            "les requetes des agents IA : un 200 y signifie que le paiement a ete "
            "accepte, un 402 qu'il ne l'a pas ete."
        ),
        "requetes": requetes,
        "payees": payees,
        "taux": round(payees / requetes, 4) if requetes else 0.0,
    }


def agents_report(events: Iterable[dict], limite: int = 10) -> dict:
    """Les cinq mesures demandees, agregees sur un flux d'evenements.

    * `agents`     : requetes par classe d'appelant (les six classes, meme a 0) ;
    * `top_ip`     : adresses des agents IA, pseudonymisees des l'entree (D30) ;
    * `top_agents` : agents IA et annuaires MCP, du plus actif au moins actif ;
    * `top_pages`  : pages demandees par ces memes agents ;
    * `conversion` : paiements acceptes / requetes d'agent IA.

    Les quatre tops ne portent que sur `agent_ia` et `annuaire_mcp` : ce sont
    eux qui decouvrent le service. Un seul aspirateur SEO suffirait a ecraser un
    classement ouvert a toutes les classes.
    """
    par_classe = {classe: 0 for classe in CLASSES_APPELANTS}
    par_agent: dict[str, int] = {}
    par_adresse: dict[str, int] = {}
    par_page: dict[str, int] = {}
    requetes_ia = 0
    payees_ia = 0

    for record in events:
        classe, nom, adresse, chemin, statut = _mesure(record)
        par_classe[classe] = par_classe.get(classe, 0) + 1
        if classe not in ("agent_ia", "annuaire_mcp"):
            continue
        par_agent[nom] = par_agent.get(nom, 0) + 1
        par_adresse[adresse] = par_adresse.get(adresse, 0) + 1
        par_page[chemin] = par_page.get(chemin, 0) + 1
        if classe == "agent_ia":
            requetes_ia += 1
            if statut == 200 and chemin in ROUTES_PAYANTES:
                payees_ia += 1

    return {
        "agents": par_classe,
        "top_ip": [
            {"ip": cle, "requetes": valeur} for cle, valeur in _trier(par_adresse, limite)
        ],
        "top_agents": [
            {"agent": cle, "editeur": agent_editeur(cle), "requetes": valeur}
            for cle, valeur in _trier(par_agent, limite)
        ],
        "top_pages": [
            {"chemin": cle, "requetes": valeur} for cle, valeur in _trier(par_page, limite)
        ],
        "conversion": conversion(requetes_ia, payees_ia),
    }


# --- Compteurs du processus et journalisation (D67) -------------------------


class _CompteursAppelants:
    """Compteurs d'appelants depuis le demarrage, en memoire et sous verrou.

    Meme esprit que `app/routes/metrics.py` (D17) : aucune dependance externe, un
    `snapshot()` lisible a tout instant. Le verrou n'est pas decoratif — le
    middleware s'execute dans les threads du serveur, et un rapport qui lit un
    compteur a moitie ecrit est faux une fois, ce qui suffit a ne plus s'y fier.
    """

    def __init__(self) -> None:
        self._verrou = threading.Lock()
        self._classes = {classe: 0 for classe in CLASSES_APPELANTS}
        self._agents: dict[str, int] = {}
        self._adresses: dict[str, int] = {}
        self._pages: dict[str, int] = {}
        self._requetes_ia = 0
        self._payees_ia = 0

    def enregistrer(
        self, classe: str, nom: str, adresse: str, chemin: str, payee: bool
    ) -> None:
        """Compte une requete : toutes les classes, mais des tops pour les robots."""
        with self._verrou:
            self._classes[classe] = self._classes.get(classe, 0) + 1
            if classe not in ("agent_ia", "annuaire_mcp"):
                return
            self._agents[nom] = self._agents.get(nom, 0) + 1
            self._adresses[adresse] = self._adresses.get(adresse, 0) + 1
            self._pages[chemin] = self._pages.get(chemin, 0) + 1
            if classe == "agent_ia":
                self._requetes_ia += 1
                if payee:
                    self._payees_ia += 1

    def snapshot(self, limite: int = 10) -> dict:
        """Copie coherente des compteurs, dans la forme des cinq mesures."""
        with self._verrou:
            classes = dict(self._classes)
            agents = dict(self._agents)
            adresses = dict(self._adresses)
            pages = dict(self._pages)
            requetes_ia, payees_ia = self._requetes_ia, self._payees_ia
        return {
            "agents": classes,
            "top_ip": [
                {"ip": cle, "requetes": n} for cle, n in _trier(adresses, limite)
            ],
            "top_agents": [
                {"agent": cle, "editeur": agent_editeur(cle), "requetes": n}
                for cle, n in _trier(agents, limite)
            ],
            "top_pages": [
                {"chemin": cle, "requetes": n} for cle, n in _trier(pages, limite)
            ],
            "conversion": conversion(requetes_ia, payees_ia),
        }


#: Compteurs du processus, lus par `GET /metrics/agents` et par la supervision.
compteurs = _CompteursAppelants()


def log_agent_request(
    request: Request, classe: str, nom: str, status: int, adresse: str
) -> dict:
    """Ecrit une ligne `agent_ia` / `annuaire_mcp` dans le journal de D30.

    Les cles sont celles que `_mesure` relit (`classe`, `agent`, `endpoint`,
    `status`) : une seule forme d'ecriture, donc un rapport qui relit ce journal
    ne peut ni compter deux fois la meme visite ni en oublier une.
    """
    settings = get_settings()
    entree = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "event": classe,
        "classe": classe,
        "agent": nom,
        "editeur": agent_editeur(nom),
        "endpoint": request.url.path,
        "method": request.method,
        "status": status,
        "user_agent": request.headers.get("user-agent", "")[:256],
        "referer": request.headers.get("referer", "")[:256],
        "ip": adresse,
        "ip_pseudonymised": not settings.discovery_log_raw_ip,
    }
    if settings.discovery_log_enabled:
        configure_logging()
        logger.info(json.dumps(entree, ensure_ascii=False, sort_keys=True))
    return entree


def record_request(request: Request, status: int) -> dict | None:
    """Classe, compte et journalise une requete. **Ne leve jamais.**

    Renvoie l'entree ecrite pour un agent IA ou un annuaire MCP, `None` pour le
    reste : un navigateur est compte, jamais journalise — le journal de D30 doit
    rester lisible par un humain, pas devenir un journal de trafic. Toute erreur
    de mesure est signalee dans le journal applicatif et reste invisible cote
    client : une panne d'observabilite ne doit pas devenir une panne de service.
    """
    try:
        settings = get_settings()
        user_agent = request.headers.get("user-agent", "") or ""
        classe = agent_class(user_agent)
        nom = agent_name(user_agent)
        chemin = request.url.path
        brute = client_ip(request)
        adresse = brute if settings.discovery_log_raw_ip else pseudonymise(brute)
        compteurs.enregistrer(
            classe=classe,
            nom=nom,
            adresse=adresse,
            chemin=chemin,
            payee=chemin in ROUTES_PAYANTES and status == 200,
        )
        if classe in ("agent_ia", "annuaire_mcp"):
            return log_agent_request(request, classe, nom, status, adresse)
    except Exception as exc:  # noqa: BLE001 - une mesure ne casse pas une reponse
        logger.warning("mesure d'appelant impossible : %s", exc)
    return None


def agents_snapshot(limite: int = 10) -> dict:
    """Les cinq mesures vues depuis le demarrage, plus la liste blanche (D67).

    Deux precisions voyagent avec le chiffre pour qu'il ne soit pas surinterprete :
    la source (memoire du processus, donc remise a zero au redemarrage) et l'etat
    du journal sur disque. Le rapport complet, lui, vit dans
    `scripts/analyser_agents.py` — l'endpoint dit « en ce moment », le script dit
    « ce qui est ecrit ».
    """
    settings = get_settings()
    return {
        "source": "compteurs du processus (remis a zero au redemarrage)",
        "rapport_complet": "scripts/analyser_agents.py --json",
        "journal": {
            "actif": settings.discovery_log_enabled,
            "chemin": settings.discovery_log_path or None,
            "ip_en_clair": settings.discovery_log_raw_ip,
        },
        "whitelist": ai_agent_whitelist(),
        **compteurs.snapshot(limite),
    }


# --- Rapport de visites : les sept sections de `analyser_visites.py` (D68) ---


#: Classes qui ne sont pas des humains. Les compter a part evite de lire
#: « 90 % de robots » alors que la moitie du total est un `curl` de recette :
#: un outil n'est pas un agent, mais ce n'est pas un visiteur non plus.
CLASSES_ROBOTS = ("agent_ia", "annuaire_mcp", "moteur")

#: Chemins que sondent les scanners (`AUDIT_TRAFIC_VPS.md` §4). Reconnus par
#: motif et non par liste fermee : un scanner qui essaie
#: `/wp/.../vendor/phpunit/.../eval-stdin.php` doit tomber dans le meme seau que
#: `/.git/HEAD`. Aucun de ces chemins n'est une route du service.
MOTIFS_SENSIBLES = (
    ".env",
    ".git/",
    ".svn/",
    ".hg/",
    ".aws/",
    ".ssh/",
    "wp-login.php",
    "wp-admin",
    "wp-json",
    "xmlrpc.php",
    "phpmyadmin",
    "cgi-bin",
    "phpinfo",
    "info.php",
    "config.json",
    "eval-stdin.php",
    "vendor/phpunit",
    "actuator",
    "server-status",
    "/admin",
)


def chemin_sensible(chemin: str) -> bool:
    """Vrai si le chemin est de ceux qu'un scanner essaie."""
    minuscule = (chemin or "").lower()
    return any(motif in minuscule for motif in MOTIFS_SENSIBLES)


def famille_statut(statut: int | None) -> str:
    """`200` -> `2xx`, `404` -> `4xx`, `None` -> `inconnu`."""
    if statut is None or not 100 <= statut <= 599:
        return "inconnu"
    return f"{statut // 100}xx"


def _section(identifiant: str, titre: str, donnees: dict, lignes: list[str]) -> dict:
    """Une section : un identifiant stable, ses chiffres, et leur lecture.

    Les deux voyagent ensemble parce qu'un chiffre seul ne se lit pas : un `4xx`
    a 1 027 n'est pas une panne, c'est un mur de scanners — c'est la ligne
    affichee qui le dit, et `--json` garde la structure pour un script.
    """
    return {"id": identifiant, "titre": titre, "donnees": donnees, "lignes": lignes}


def visites_report(
    requetes: Iterable[dict], limite: int = 10, ip_en_clair: bool = False
) -> dict:
    """Sept sections de lecture du trafic, agregees sur des mesures d'acces.

    Sept questions, dans l'ordre ou on se les pose devant un journal :

    1. `chiffres_cles` — combien de reponses, quels octets, quelle part de robots ;
    2. `par_jour`      — comment le trafic se repartit dans le temps ;
    3. `top_pages`     — quelles portes sont demandees, et pour quel statut ;
    4. `top_ip`        — qui insiste, pseudonymise par defaut (D30) ;
    5. `codes_statut`  — ce que le service repond (2xx, 3xx, 4xx, 5xx) ;
    6. `clients`       — quels clients, nommes ou inconnus ;
    7. `anomalies`     — sondes, chemins sensibles, et **fuites** s'il y en a.

    La source est le journal d'acces Nginx (`combined`) : lui seul porte le
    User-Agent de **tout** le trafic, humains compris. Le journal de D30 ne voit
    que les agents IA (`agents_report`) : les deux rapports se completent, ils ne
    se remplacent pas.
    """
    mesures = list(requetes)
    classes: Counter = Counter({classe: 0 for classe in CLASSES_APPELANTS})
    methodes: Counter = Counter()
    jours: Counter = Counter()
    heures: Counter = Counter()
    pages: Counter = Counter()
    pages_octets: Counter = Counter()
    pages_familles: dict[str, Counter] = defaultdict(Counter)
    adresses: Counter = Counter()
    adresses_octets: Counter = Counter()
    adresses_classes: dict[str, Counter] = defaultdict(Counter)
    adresses_agents: dict[str, Counter] = defaultdict(Counter)
    adresses_sensibles: Counter = Counter()
    codes: Counter = Counter()
    familles: Counter = Counter()
    clients: Counter = Counter()
    clients_classes: dict[str, str] = {}
    inconnus: Counter = Counter()
    sensibles: Counter = Counter()
    sensibles_familles: dict[str, Counter] = defaultdict(Counter)
    fuites: Counter = Counter()
    octets = 0
    instants: list[datetime] = []

    for mesure in mesures:
        classe, nom, adresse, chemin, statut = _mesure(mesure)
        famille = famille_statut(statut)
        taille = int(mesure.get("octets") or 0)
        instant = moment(mesure)
        affichee = adresse if ip_en_clair else pseudonymise(adresse)

        classes[classe] += 1
        methodes[str(mesure.get("methode") or "-")] += 1
        codes[str(statut) if statut is not None else "inconnu"] += 1
        familles[famille] += 1
        clients[nom] += 1
        clients_classes[nom] = classe
        octets += taille
        if instant is not None:
            instants.append(instant)
            jours[instant.date().isoformat()] += 1
            heures[instant.strftime("%Y-%m-%d %Hh")] += 1
        if classe == "inconnu":
            inconnus[str(mesure.get("user_agent") or "")[:80]] += 1

        pages[chemin] += 1
        pages_octets[chemin] += taille
        pages_familles[chemin][famille] += 1

        adresses[affichee] += 1
        adresses_octets[affichee] += taille
        adresses_classes[affichee][classe] += 1
        adresses_agents[affichee][nom] += 1

        if chemin_sensible(chemin):
            sensibles[chemin] += 1
            sensibles_familles[chemin][famille] += 1
            adresses_sensibles[affichee] += 1
            # Une fuite, c'est du **contenu servi** : un `301` renvoie ailleurs
            # (et la cible repond `404`), un `204` ne porte rien. Seul un `2xx`
            # prouve qu'un fichier sensible a ete rendu — compter les 25
            # redirections du §4 de `AUDIT_TRAFIC_VPS.md` comme des fuites
            # ferait crier au loup sur ce qui n'est qu'une reecriture d'URL.
            if famille == "2xx":
                fuites[chemin] += 1

    total = len(mesures)
    robots = sum(classes[classe] for classe in CLASSES_ROBOTS)
    debut = min(instants).isoformat(timespec="seconds") if instants else None
    fin = max(instants).isoformat(timespec="seconds") if instants else None
    part_robots = round(robots / total, 4) if total else 0.0
    pointe = _trier(heures, 1)
    resume_classes = ", ".join(f"{nom}={classes[nom]}" for nom in CLASSES_APPELANTS)
    resume_methodes = ", ".join(
        f"{methode}={nombre}"
        for methode, nombre in _trier(methodes, len(methodes) or 1)
    )

    pages_donnees: list[dict] = []
    pages_lignes: list[str] = []
    for chemin, nombre in _trier(pages, limite):
        familles_page = dict(pages_familles[chemin])
        dominante = _trier(pages_familles[chemin], 1)
        # Un chemin sert souvent plusieurs statuts : `/validate` repond `200`
        # quand le paiement passe et `402` quand il ne passe pas. N'afficher que
        # le dominant cacherait la moitie du fait, donc des qu'il y en a plus
        # d'un, la ligne les donne tous.
        affichage = (
            " ".join(
                f"{famille}={nombre_statuts}"
                for famille, nombre_statuts in _trier(
                    familles_page, len(familles_page)
                )
            )
            if len(familles_page) > 1
            else (dominante[0][0] if dominante else "inconnu")
        )
        pages_donnees.append(
            {
                "chemin": chemin,
                "requetes": nombre,
                "octets": pages_octets[chemin],
                "famille_dominante": dominante[0][0] if dominante else "inconnu",
                "familles": familles_page,
            }
        )
        pages_lignes.append(
            f"{nombre:>7}  {chemin}  [{affichage}, {pages_octets[chemin]} octet(s)]"
        )

    ip_donnees: list[dict] = []
    ip_lignes: list[str] = []
    for adresse, nombre in _trier(adresses, limite):
        classe_dominante = _trier(adresses_classes[adresse], 1)
        agent_dominant = _trier(adresses_agents[adresse], 1)
        ip_donnees.append(
            {
                "ip": adresse,
                "requetes": nombre,
                "octets": adresses_octets[adresse],
                "classe_dominante": (
                    classe_dominante[0][0] if classe_dominante else "inconnu"
                ),
                "agent_dominant": agent_dominant[0][0] if agent_dominant else "inconnu",
                "sensibles": adresses_sensibles[adresse],
                "sensibles_en_pourcent": (
                    round(adresses_sensibles[adresse] / nombre, 4) if nombre else 0.0
                ),
            }
        )
        ip_lignes.append(
            f"{nombre:>7}  {adresse}  ["
            f"{ip_donnees[-1]['classe_dominante']} / "
            f"{ip_donnees[-1]['agent_dominant']}, "
            f"{adresses_sensibles[adresse]} sensible(s)]"
        )

    sections: list[dict] = [
        _section(
            "chiffres_cles",
            "Chiffres cles",
            {
                "requetes": total,
                "octets": octets,
                "robots": robots,
                "part_robots": part_robots,
                "classes": dict(classes),
                "methodes": dict(methodes),
                "plage": {"debut": debut, "fin": fin},
                "ip_pseudonymised": not ip_en_clair,
            },
            [
                f"{total} reponse(s), {octets} octet(s) servi(s)",
                f"robots {robots} ({part_robots:.1%}), navigateurs "
                f"{classes['navigateur']}, inconnus {classes['inconnu']}",
                f"plage mesuree : {debut} -> {fin}",
                f"classes : {resume_classes}",
                f"methodes : {resume_methodes}",
                "adresses IP "
                + ("en clair" if ip_en_clair else "pseudonymisees (SHA-256 + sel, D30)"),
            ],
        ),
        _section(
            "par_jour",
            "Repartition dans le temps",
            {
                "par_jour": [
                    {"jour": jour, "requetes": nombre}
                    for jour, nombre in sorted(jours.items())
                ],
                "heure_de_pointe": (
                    {"heure": pointe[0][0], "requetes": pointe[0][1]} if pointe else None
                ),
            },
            [f"{jour} : {nombre} reponse(s)" for jour, nombre in sorted(jours.items())]
            + ([f"heure de pointe : {pointe[0][0]} ({pointe[0][1]})"] if pointe else []),
        ),
        _section(
            "top_pages",
            f"Chemins les plus demandes (top {limite} sur {len(pages)} distincts)",
            {"entrees": pages_donnees},
            pages_lignes or ["aucune page demandee dans la fenetre"],
        ),
        _section(
            "top_ip",
            f"Adresses les plus actives (top {limite} sur {len(adresses)} distinctes)",
            {"entrees": ip_donnees},
            ip_lignes or ["aucune adresse dans la fenetre"],
        ),
    ]

    # Sections 5 a 7 : ce que le service repond, a qui, et ce qui sort de l'ordinaire.
    familles_lignes = [
        f"{famille} : {familles[famille]}"
        for famille in ("2xx", "3xx", "4xx", "5xx", "inconnu")
    ]
    clients_donnees = [
        {"agent": nom, "classe": clients_classes[nom], "requetes": nombre}
        for nom, nombre in _trier(clients, limite)
    ]
    clients_lignes = [
        f"{nombre:>7}  {nom}  [{clients_classes[nom]}]"
        for nom, nombre in _trier(clients, limite)
    ] + [
        f"{nombre:>7}  {agent or 'User-Agent vide'}  [inconnu]"
        for agent, nombre in _trier(inconnus, limite)
    ]
    sensibles_donnees = [
        {
            "chemin": chemin,
            "requetes": nombre,
            "familles": dict(sensibles_familles[chemin]),
        }
        for chemin, nombre in _trier(sensibles, limite)
    ]
    sensibles_lignes = [
        f"{nombre:>7}  {chemin}  {dict(sensibles_familles[chemin])}"
        for chemin, nombre in _trier(sensibles, limite)
    ]
    sondeurs_lignes = [
        f"{adresse} ({nombre})"
        for adresse, nombre in _trier(adresses_sensibles, limite)
    ]
    fuites_lignes = [
        f"FUITE : {chemin} repondu {nombre} fois en 2xx"
        for chemin, nombre in _trier(fuites, len(fuites) or 1)
    ] or ["aucune fuite : aucun chemin sensible n'a recu de 2xx"]

    sections += [
        _section(
            "codes_statut",
            "Codes de statut",
            {
                "par_famille": {
                    famille: familles[famille]
                    for famille in ("2xx", "3xx", "4xx", "5xx", "inconnu")
                },
                "par_code": [
                    {"code": code, "requetes": nombre}
                    for code, nombre in _trier(codes, len(codes) or 1)
                ],
            },
            familles_lignes
            + [
                "codes : "
                + ", ".join(
                    f"{code}={nombre}"
                    for code, nombre in _trier(codes, len(codes) or 1)
                )
            ],
        ),
        _section(
            "clients",
            "Clients : qui demande",
            {
                "par_classe": dict(classes),
                "top_clients": clients_donnees,
                "user_agents_inconnus": [
                    {"user_agent": agent, "requetes": nombre}
                    for agent, nombre in _trier(inconnus, limite)
                ],
            },
            clients_lignes or ["aucun client dans la fenetre"],
        ),
        _section(
            "anomalies",
            "Anomalies : sondes, chemins sensibles, fuites",
            {
                "erreurs": {
                    famille: familles[famille] for famille in ("4xx", "5xx")
                },
                "chemins_sensibles": {
                    "requetes": sum(sensibles.values()),
                    "chemins": len(sensibles),
                    "entrees": sensibles_donnees,
                    "sondeurs": [
                        {"ip": adresse, "requetes": nombre}
                        for adresse, nombre in _trier(adresses_sensibles, limite)
                    ],
                },
                "fuites": [
                    {"chemin": chemin, "requetes": nombre}
                    for chemin, nombre in _trier(fuites, len(fuites) or 1)
                ],
            },
            [
                f"erreurs : 4xx {familles['4xx']}, 5xx {familles['5xx']}",
                f"chemins sensibles sondes : {sum(sensibles.values())} requete(s) "
                f"sur {len(sensibles)} chemin(s) distinct(s)",
            ]
            + sensibles_lignes
            + [f"sondeurs : {', '.join(sondeurs_lignes) or 'aucun'}"]
            + fuites_lignes,
        ),
    ]

    return {
        "requetes": total,
        "robots": robots,
        "humains": classes["navigateur"],
        "ip_pseudonymised": not ip_en_clair,
        "sections": sections,
    }

