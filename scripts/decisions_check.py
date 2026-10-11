#!/usr/bin/env python
"""Oracle mecanique des decisions D1..D40, adosse au ledger GATES.md.

Les decisions de `DECISIONS.md` n'etaient verifiables que par lecture humaine :
chaque ligne du ledger demandait au relecteur de comparer deux fichiers a l'oeil.
Ce script transforme chaque decision en **oracle executable** : il observe le
depot (pins, DDL, shebang) *et* le comportement reel du code (HMAC de la preuve
de paiement, disjoncteur sous 8 threads, cooldown du cache Redis, /health borne,
ORM compare au DDL, degradation du geocodeur, signature EIP-712 x402).

    .venv\\Scripts\\python.exe scripts\\decisions_check.py D7      # une decision
    .venv\\Scripts\\python.exe scripts\\decisions_check.py --all   # les 40
    .venv\\Scripts\\python.exe scripts\\decisions_check.py --list

Sortie (ASCII, une ligne par porte) :

    DECISION OK D7
    DECISION FAIL D7: <raison>          (code 1)

Codes de retour : 0 vert, 1 porte rouge, 2 usage ou identifiant inconnu.

Aucun acces a Internet n'est effectue : les seules I/O sortantes sont des sondes
du chemin degrade vers la boucle locale (init_db au demarrage, /health,
/metrics/revenue, /dashboard, /mcp), qui sont justement ce que D14/D16/D22/D31/
D32/D34-D37 mesurent. Les fournisseurs d'adresses (SmartyStreets, Nominatim) ne
sont jamais appeles : les oracles qui traversent /validate coupent explicitement
la couche fournisseur, le geocodeur et le cache. Le facilitateur x402 est
remplace par un double (`app.x402._post_facilitator`), donc aucune transaction
n'est envoyee.

Les portes D28..D33 couvrent le chantier de decouvrabilite : fichiers publies
(llms.txt, AGENTS.md, security.txt), registre de paiement identifie par la
preuve, journal des refus 402, revenu agrege, tableau de bord ferme par defaut,
et manifeste enrichi.

Les portes D34..D39 couvrent le Lot B : paiement x402 v1 reel (corps 402,
en-tete X-PAYMENT, reglement par facilitateur, X-PAYMENT-RESPONSE), verification
EIP-712 hors ligne adossee a un vecteur externe, refus de tout echec de
reglement, serveur MCP et sa carte de decouverte, fichiers d'annuaire publies
dans un format source, et retention de 30 jours appliquee par une tache
planifiee.

La porte D40 couvre le correctif de metriques : le registre distingue un
paiement encaisse d'un essai de recette (`test_mode`), /metrics/revenue publie
`real_*` hors recette et `total_*` en tout, et le DDL comme l'ORM portent la
colonne.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import importlib.metadata as md
import inspect
import os
import re
import sys
import threading
import time
import unicodedata
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Configuration d'oracle, posee **avant** tout import de `app` : les portes
# D34-D37 refusent une adresse d'encaissement malformee *avant* tout appel
# reseau, donc une adresse absente rendrait le contrat x402 inobservable. La
# valeur reelle du VPS, elle, est verifiee par la recette de deploiement
# (`scripts/test_x402_payment.py`), pas ici.
os.environ.setdefault("CDP_WALLET_ADDRESS", "0x" + "42" * 20)
os.environ.setdefault("API_SECRET_KEY", "oracle_secret_key")

APP_DIR = ROOT / "app"


class CheckFailed(Exception):
    """Une decision n'est pas tenue."""


def guard(condition, message: str) -> None:
    if not condition:
        raise CheckFailed(message)


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def fold(text: str) -> str:
    """Texte sans accents ni majuscules, pour comparer de la prose francaise."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def app_py_files() -> list[Path]:
    """Fichiers .py de l'application, hors caches d'interpretation."""
    return sorted(
        path
        for path in APP_DIR.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def app_source() -> str:
    return "\n".join(read(str(p.relative_to(ROOT))) for p in app_py_files())


def requirement_lines(filename: str) -> list[str]:
    """Lignes actives d'un fichier de dependances (commentaires retires)."""
    lines = []
    for raw in read(filename).splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            lines.append(line)
    return lines


def requirement_pins(filename: str) -> dict[str, str]:
    """{paquet: version} des dependances epinglees avec `==`."""
    pins: dict[str, str] = {}
    for line in requirement_lines(filename):
        match = re.match(r"^([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?\s*==\s*([^\s;]+)", line)
        if match:
            pins[match.group(1).lower()] = match.group(2)
    return pins


def installed(name: str) -> str | None:
    """Version installee d'une distribution, ou None si absente."""
    try:
        return md.version(name)
    except md.PackageNotFoundError:
        return None


def fake_request(headers: dict[str, str]):
    """Requete Starlette minimale, sans serveur ni socket."""
    from starlette.requests import Request

    raw = [(key.lower().encode(), value.encode()) for key, value in headers.items()]
    return Request({"type": "http", "method": "POST", "path": "/validate", "headers": raw})


def boom() -> None:
    raise RuntimeError("panne simulee")


def in_threads(worker, count: int) -> None:
    """Lance `worker` dans `count` threads et propage la premiere exception."""
    errors: list[BaseException] = []

    def run() -> None:
        try:
            worker()
        except BaseException as exc:  # noqa: BLE001 - remonte apres le join
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if errors:
        raise errors[0]


def d1() -> None:
    """Le pin cdp-sdk est celui de la release publiee, et il est installe."""
    pins = requirement_pins("requirements.txt")
    guard(pins.get("cdp-sdk") == "1.0.1", f"requirements.txt epingle cdp-sdk={pins.get('cdp-sdk')}")
    guard(
        not any("cdp-sdk==1.0.0" in line for line in requirement_lines("requirements.txt")),
        "l'ancien pin cdp-sdk==1.0.0 est encore actif dans requirements.txt",
    )
    guard(
        "cdp-sdk==1.0.0" in read("requirements.txt"),
        "la raison du remplacement (1.0.0 absent de PyPI) n'est plus consignee",
    )
    got = installed("cdp-sdk")
    guard(got == "1.0.1", f"distribution installee cdp-sdk={got}")


def d2() -> None:
    """Aucun paquet `smarty-street-python`, et l'API HTTP est appelee en direct."""
    guard(
        "smarty-street-python" not in requirement_pins("requirements.txt"),
        "smarty-street-python est toujours declare en dependance",
    )
    importers = [
        str(path.relative_to(ROOT))
        for path in app_py_files()
        if re.search(r"^\s*(?:import|from)\s+smarty", read(str(path.relative_to(ROOT))), re.M)
    ]
    guard(not importers, f"du code importe un paquet smarty : {importers}")
    verifier = read("app/validation/verifier.py")
    guard(
        "SMARTY_URL" in verifier and "httpx.Client" in verifier,
        "verifier.py n'interroge plus l'API SmartyStreets en HTTP direct",
    )
    guard(
        "smarty-street-python" in read("requirements.txt"),
        "requirements.txt ne consigne plus le retrait du paquet",
    )


def d3() -> None:
    """pydantic-settings est declare, importable, et porte la configuration."""
    pins = requirement_pins("requirements.txt")
    guard(
        pins.get("pydantic-settings") == "2.7.1",
        f"requirements.txt epingle pydantic-settings={pins.get('pydantic-settings')}",
    )
    guard(installed("pydantic-settings") is not None, "pydantic-settings n'est pas installe")
    import pydantic_settings

    from app.config import Settings, get_settings

    guard(
        issubclass(Settings, pydantic_settings.BaseSettings),
        "Settings n'herite pas de pydantic_settings.BaseSettings",
    )
    guard(get_settings() is not None, "get_settings() ne renvoie rien")


def d4() -> None:
    """pydantic est releve a 2.10.3, version exigee par cdp-sdk."""
    pins = requirement_pins("requirements.txt")
    guard(pins.get("pydantic") == "2.10.3", f"requirements.txt epingle pydantic={pins.get('pydantic')}")
    got = installed("pydantic")
    guard(got == "2.10.3", f"distribution installee pydantic={got}")
    try:
        requires = list(md.requires("cdp-sdk") or [])
    except md.PackageNotFoundError:
        requires = []
    guard(requires, "les metadonnees de cdp-sdk sont illisibles : le conflit n'est pas verifiable")
    guard(
        any(re.match(r"pydantic\s*==\s*2\.10\.3", item) for item in requires),
        f"cdp-sdk ne fixe plus pydantic==2.10.3 : {requires}",
    )


def d5() -> None:
    """La route payante sert le cache avant tout appel fournisseur."""
    source = read("app/routes/validate.py")
    guard("get_cached_result(" in source, "la route n'appelle jamais get_cached_result")
    guard("store_in_cache(" in source, "la route n'appelle jamais store_in_cache")
    guard("cache_hits" in source and "cache_misses" in source, "les compteurs de cache ne sont plus alimentes")
    from app.routes.validate import cache_key

    guard(
        cache_key({"street": "A", "city": "B"}) == cache_key({"city": "B", "street": "A"}),
        "cache_key depend de l'ordre des cles JSON",
    )
    key = cache_key({"street": "A", "city": "B"})
    guard(len(key) == 64 and re.fullmatch(r"[0-9a-f]{64}", key), f"cache_key n'est pas un sha256 hex : {key}")
    guard((ROOT / "tests/test_cache.py").is_file(), "tests/test_cache.py, preuve du critere 3, est absent")


def d6() -> None:
    """Le SDK CDP est defensif et la verification est fail-closed."""
    source = read("app/payment.py")
    guard("_CDP_AVAILABLE" in source, "aucun drapeau de disponibilite du SDK CDP")
    guard("except Exception" in source.split("def configure_cdp")[0], "l'import du SDK CDP n'est pas protege")
    from app.payment import PROOF_HEADER, configure_cdp, verify_payment

    guard(configure_cdp() in (True, False), "configure_cdp() ne renvoie pas un booleen")
    guard(verify_payment(fake_request({})) is False, "une requete sans preuve est acceptee")
    guard(
        verify_payment(fake_request({PROOF_HEADER: "nimportequoi"})) is False,
        "une preuve malformee est acceptee",
    )
    guard(
        verify_payment(fake_request({PROOF_HEADER: "tx:pasuntimestamp:sig"})) is False,
        "un timestamp non numerique est accepte",
    )
    guard("pragma: no cover" in source, "le chemin CDP reel n'est plus exclu de la couverture")


def d7() -> None:
    """La preuve de paiement est fraiche, symetrique, et signee en HMAC."""
    from app.payment import (
        PROOF_HEADER,
        PROOF_MAX_AGE_SECONDS,
        build_payment_proof,
        sign_proof,
        verify_payment,
    )

    guard(PROOF_MAX_AGE_SECONDS == 300, f"fenetre de fraicheur = {PROOF_MAX_AGE_SECONDS} s")
    guard(
        verify_payment(fake_request({PROOF_HEADER: build_payment_proof("0xabc")})) is True,
        "une preuve fraiche et bien signee est refusee",
    )
    now = time.time()
    guard(
        verify_payment(
            fake_request({PROOF_HEADER: build_payment_proof("0xabc", timestamp=now - 301)})
        )
        is False,
        "une preuve perimee (301 s) est acceptee",
    )
    guard(
        verify_payment(
            fake_request({PROOF_HEADER: build_payment_proof("0xabc", timestamp=now + 301)})
        )
        is False,
        "une preuve venue du futur est acceptee (fenetre non symetrique)",
    )
    fresh = build_payment_proof("0xabc")
    guard(
        verify_payment(fake_request({PROOF_HEADER: fresh[:-4] + "0000"})) is False,
        "une signature falsifiee est acceptee",
    )
    guard("hmac.compare_digest" in read("app/payment.py"), "la signature n'est pas comparee en temps constant")
    from app.config import get_settings

    expected = hmac.new(get_settings().api_secret_key.encode(), b"0xabc:1", hashlib.sha256).hexdigest()
    guard(sign_proof("0xabc", "1") == expected, "sign_proof n'est pas le HMAC-SHA256 du couple tx_hash:timestamp")


def d8() -> None:
    """Une variable d'environnement inconnue ne casse pas le demarrage."""
    from app.config import Settings

    guard(Settings.Config.extra == "ignore", f"extra = {Settings.Config.extra!r}")
    guard(Settings(variable_vps_inconnue="x") is not None, "un mot-cle inconnu fait echouer Settings")
    os.environ["VARIABLE_VPS_INCONNUE_XYZ"] = "1"
    try:
        settings = Settings()
    finally:
        os.environ.pop("VARIABLE_VPS_INCONNUE_XYZ", None)
    guard(settings.api_port == 8000, f"les defauts ont bouge : api_port={settings.api_port}")
    guard(not hasattr(settings, "variable_vps_inconnue_xyz"), "une variable inconnue est exposee dans Settings")


def d9() -> None:
    """L'etat du disjoncteur est protege par un verrou et rearmable."""
    from app.validation.verifier import CircuitBreaker

    guard(hasattr(CircuitBreaker, "reset"), "CircuitBreaker n'a pas de reset()")
    # Seuil inatteignable : le disjoncteur ne s'ouvre jamais, donc chaque appel
    # concurrent passe par le chemin d'increment. Une incrementation non
    # protegee perdrait des mises a jour.
    breaker = CircuitBreaker(failure_threshold=10**9, recovery_timeout=0)

    def hammer() -> None:
        for _ in range(200):
            try:
                breaker.call(boom)
            except RuntimeError:
                pass

    in_threads(hammer, 8)
    guard(
        breaker.failure_count == 1600,
        f"mises a jour perdues : failure_count={breaker.failure_count} au lieu de 1600",
    )
    guard(breaker.state == "closed", f"etat inattendu apres 1600 echecs : {breaker.state}")
    breaker.reset()
    guard(
        breaker.failure_count == 0 and breaker.last_failure_time is None and breaker.state == "closed",
        "reset() ne rend pas l'etat initial",
    )


def d10() -> None:
    """Le geocodage annonce un User-Agent avec adresse de contact."""
    from app.validation import geocoder

    guard(
        "@" in geocoder.USER_AGENT and "(" in geocoder.USER_AGENT,
        f"User-Agent sans contact : {geocoder.USER_AGENT!r}",
    )
    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return [{"lat": "48.8566", "lon": "2.3522"}]

    class FakeClient:
        def __init__(self, timeout=None) -> None:
            captured["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, *exc) -> bool:
            return False

        def get(self, url, params=None, headers=None):
            captured["url"] = url
            captured["headers"] = headers
            return FakeResponse()

    original = geocoder.httpx.Client
    geocoder.httpx.Client = FakeClient
    try:
        result = geocoder.geocode(
            {"line1": "1 rue X", "city": "Paris", "state": "IDF", "zip": "75001", "country": "FR"}
        )
    finally:
        geocoder.httpx.Client = original
    guard(
        captured.get("headers", {}).get("User-Agent") == geocoder.USER_AGENT,
        "l'en-tete User-Agent n'est pas transmis a Nominatim",
    )
    guard(captured.get("url") == geocoder.NOMINATIM_URL, f"URL interrogee : {captured.get('url')}")
    guard(result == {"lat": 48.8566, "lng": 2.3522}, f"resultat de geocodage : {result}")
    guard(captured.get("timeout") is geocoder.GEOCODER_TIMEOUT, "le client n'utilise pas GEOCODER_TIMEOUT")


def d11() -> None:
    """Les paiements sont traces dans une table dediee et idempotente."""
    ddl = read("app/database.py")
    guard("CREATE TABLE IF NOT EXISTS payment_transactions" in ddl, "table payment_transactions absente du DDL")
    guard(
        re.search(r"tx_hash VARCHAR\(128\) UNIQUE NOT NULL", ddl) is not None,
        "tx_hash n'est pas UNIQUE : ON CONFLICT DO NOTHING serait inoperant",
    )
    source = read("app/payment.py")
    guard("INSERT INTO payment_transactions" in source, "record_transaction n'ecrit pas dans payment_transactions")
    guard("ON CONFLICT (tx_hash) DO NOTHING" in source, "record_transaction n'est plus idempotent")
    guard(
        all(
            "INSERT INTO address_validations" not in read(str(path.relative_to(ROOT)))
            for path in app_py_files()
        ),
        "address_validations est ecrite alors que PRIVACY.md documente le contraire",
    )
    from app.payment import record_transaction

    guard(record_transaction("", 0.02, "cle") is False, "un hash de transaction vide est enregistre")


def d12() -> None:
    """Les timeouts Redis sont courts et les erreurs du cache sont comptees."""
    from app import cache

    kwargs = cache.redis_client.connection_pool.connection_kwargs
    guard(
        kwargs.get("socket_connect_timeout") == 1,
        f"socket_connect_timeout={kwargs.get('socket_connect_timeout')} (un Redis absent pendrait)",
    )
    guard(kwargs.get("socket_timeout") == 1, f"socket_timeout={kwargs.get('socket_timeout')}")
    guard(cache.CACHE_TTL == 60 * 60 * 24 * 30, f"TTL du cache = {cache.CACHE_TTL} s")
    guard(cache.KEY_PREFIX == "addr:", f"prefixe de cle = {cache.KEY_PREFIX!r}")
    guard(isinstance(cache.cache_error_count(), int), "cache_error_count() ne renvoie pas un entier")


def d13() -> None:
    """Les modeles ORM couvrent exactement les colonnes du DDL SQL."""
    from app.models import AddressValidation, PaymentTransaction

    ddl = read("app/database.py")
    for model in (AddressValidation, PaymentTransaction):
        table = model.__tablename__
        marker = f"CREATE TABLE IF NOT EXISTS {table}"
        guard(marker in ddl, f"{table} est absent du DDL")
        block = ddl.split(marker, 1)[1].split(");", 1)[0]
        declared = set(re.findall(r"^\s*([a-z_]+)\s+[A-Z]", block, re.M))
        mapped = {column.name for column in model.__table__.columns}
        guard(not (mapped - declared), f"{table} : colonnes ORM absentes du DDL {sorted(mapped - declared)}")
        guard(not (declared - mapped), f"{table} : colonnes du DDL absentes de l'ORM {sorted(declared - mapped)}")
    guard(AddressValidation.__table__.c.cache_key.unique is True, "cache_key n'est pas unique cote ORM")
    guard(PaymentTransaction.__table__.c.tx_hash.unique is True, "tx_hash n'est pas unique cote ORM")
    guard("D13" in read("app/models.py"), "le doublon ORM/DDL n'est pas renvoye a la decision D13")


def d14() -> None:
    """Une base injoignable ne bloque ni le demarrage ni /health."""
    from app.database import engine, init_db

    guard(engine.dialect.name == "postgresql", f"dialecte = {engine.dialect.name}")
    guard(getattr(engine.pool, "_pre_ping", False) is True, "pool_pre_ping est desactive")
    guard(
        re.search(r"connect_args=\{\s*\"connect_timeout\":\s*2\s*\}", read("app/database.py")) is not None,
        "connect_timeout court absent de create_engine",
    )
    started = time.perf_counter()
    result = init_db()
    elapsed = time.perf_counter() - started
    guard(isinstance(result, bool), f"init_db() renvoie {type(result).__name__} au lieu d'un booleen")
    guard(elapsed < 6.0, f"init_db() a mis {elapsed:.2f}s : le demarrage depend de la base")
    guard(re.search(r"except Exception:\s*\n\s*return False", read("app/database.py")) is not None, "init_db ne retombe pas sur False")


def d15() -> None:
    """La preuve a secret partage reste servie, et la surface on-chain est bornee."""
    # La porte interdit ce qui n'a jamais ete decide : les bibliotheques lourdes
    # (web3, eth-account et son magasin de cles, eth-utils) ne doivent etre
    # importees **nulle part**, meme si un commentaire peut les nommer.
    source = app_source()
    for paquet in ("web3", "eth_account", "eth_utils"):
        guard(
            re.search(rf"(?m)^\s*(?:import|from)\s+{paquet}\b", source) is None,
            f"le paquet {paquet} est importe dans l'application : la surface n'a jamais ete decidee",
        )
    # Les primitives qui rendent la verification EIP-712 possible sont, elles,
    # bornees a **un seul** fichier : c'est ce qui rend la surface on-chain
    # auditable d'un coup d'oeil (`TransferWithAuthorization` en prose ailleurs
    # ne declare rien : seuls les modules importes comptent).
    autorises = {"app/x402.py"}
    for token in ("eth_abi", "eth_keys", "eth_hash"):
        hors = sorted(
            nom
            for nom in (
                str(p.relative_to(ROOT)).replace("\\", "/") for p in app_py_files()
            )
            if token in read(nom) and nom not in autorises
        )
        guard(not hors, f"{token} sort du perimetre on-chain autorise ({sorted(autorises)}) : {hors}")

    from app.config import get_settings
    from app.payment import PROOF_HEADER, build_payment_proof, verify_payment

    secret = get_settings().api_secret_key
    proof = build_payment_proof("0xMADEUP")
    guard(
        verify_payment(fake_request({PROOF_HEADER: proof})) is True,
        "une preuve signee par le secret partage est refusee : la voie de compatibilite D15 est cassee",
    )
    guard(secret != "", "aucun secret partage : la preuve n'a plus de cle")
    guard("D15" in read("DECISIONS.md"), "la limite D15 n'est plus consignee dans DECISIONS.md")
    guard(
        "mise à niveau" in read("README.md") or "x402" in read("README.md").lower(),
        "README.md ne decrit plus le chemin de paiement x402",
    )


def d16() -> None:
    """Le demarrage cree le schema sans dependre de la base."""
    from starlette.testclient import TestClient

    from app import main
    from app.database import init_db

    guard(init_db() in (True, False), "init_db() ne renvoie pas un booleen")
    calls: list[str] = []
    real = main.init_db

    def recorder() -> bool:
        calls.append("init_db")
        return real()

    with mock.patch.object(main, "init_db", recorder):
        with TestClient(main.app) as client:
            guard(
                calls == ["init_db"],
                f"le demarrage n'appelle pas init_db() exactement une fois : {len(calls)} appel(s)",
            )
            root = client.get("/")
            guard(root.status_code == 200, f"GET / au demarrage : {root.status_code}")
            guard(root.json().get("service") == "Address Validator API", f"charge utile de / : {root.json()}")
    paths = {route.path for route in main.app.routes}
    for path in ("/", "/validate", "/health", "/manifest", "/metrics"):
        guard(path in paths, f"route {path} absente de l'application")


def d17() -> None:
    """Les compteurs de /metrics sont atomiques et sans dependance externe."""
    from app.routes.metrics import Metrics, metrics

    counter = Metrics()

    def hammer() -> None:
        for _ in range(500):
            counter.incr("requests_total")

    in_threads(hammer, 8)
    guard(counter.snapshot()["requests_total"] == 4000, f"compteur = {counter.snapshot()['requests_total']} au lieu de 4000")
    ratio = Metrics()
    ratio.incr("cache_hits")
    ratio.incr("cache_misses")
    ratio.incr("cache_misses")
    guard(ratio.snapshot()["cache_hit_ratio"] == 0.3333, f"cache_hit_ratio={ratio.snapshot()['cache_hit_ratio']}")
    guard(Metrics().snapshot()["cache_hit_ratio"] == 0.0, "un ratio non mesure vaut autre chose que 0.0")
    guard("prometheus" not in (read("requirements.txt") + read("requirements-dev.txt")).lower(), "dependance de metriques externe ajoutee")
    guard(isinstance(metrics.snapshot(), dict), "l'instance applicative ne rend pas d'instantane")


def d18() -> None:
    """Les dependances de test ne sont pas dans l'image de production."""
    dev = requirement_pins("requirements-dev.txt")
    guard(dev.get("pytest") == "8.3.3", f"requirements-dev.txt epingle pytest={dev.get('pytest')}")
    guard(dev.get("pytest-cov") == "6.0.0", f"requirements-dev.txt epingle pytest-cov={dev.get('pytest-cov')}")
    guard("pytest" not in read("requirements.txt").lower(), "pytest a fuite dans requirements.txt")
    dockerfile = read("Dockerfile")
    guard("requirements-dev" not in dockerfile, "le Dockerfile installe requirements-dev.txt")
    guard("requirements.txt" in dockerfile, "le Dockerfile n'installe plus requirements.txt")


def d19() -> None:
    """Un delai de recuperation exactement ecoule rouvre la sonde."""
    from app.validation.verifier import CircuitBreaker

    with mock.patch("app.validation.verifier.time.time", return_value=1000.0):
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=0)
        guard(breaker.recovery_timeout == 0, f"recovery_timeout={breaker.recovery_timeout}")
        try:
            breaker.call(boom)
        except RuntimeError:
            pass
        guard(breaker.state == "open", f"un echec pour un seuil de 1 ne declenche rien : {breaker.state}")
        guard(
            breaker.call(lambda: "sonde") == "sonde",
            "elapsed == recovery_timeout : la sonde half-open reste bloquee (`>` au lieu de `>=`)",
        )
        guard(
            breaker.state == "closed" and breaker.failure_count == 0,
            "la sonde half-open ne referme pas le disjoncteur",
        )
    guard(">=" in inspect.getsource(CircuitBreaker.call), "la comparaison du delai n'est plus >=")


def d20() -> None:
    """Le deploiement echoue vite et se lance depuis le venv."""
    deploy = read("deploy.sh")
    guard("set -euo pipefail" in deploy, "deploy.sh n'echoue plus au premier probleme")
    guard(not re.search(r"sudo\s+\S*pip", deploy), "deploy.sh installe des paquets avec sudo pip")
    guard("venv" in deploy, "deploy.sh ne cree plus d'environnement virtuel")
    guard(
        "source venv/bin/activate" in deploy or "venv/bin/python" in deploy,
        "deploy.sh n'active pas l'environnement virtuel avant d'installer",
    )
    guard(
        b"\r\n" not in (ROOT / "deploy.sh").read_bytes(),
        "deploy.sh contient des fins de ligne CRLF (/bin/bash^M sur le VPS Debian)",
    )
    scripts = sorted((ROOT / "scripts").glob("*.py"))
    guard(scripts, "aucun script Python dans scripts/")
    for path in scripts:
        first = read(f"scripts/{path.name}").splitlines()[0].strip()
        guard(
            first in ("#!/usr/bin/env python3", "#!/usr/bin/env python"),
            f"{path.name} n'a plus de shebang python3 : {first!r}",
        )


def d21() -> None:
    """Pas de limitation de debit applicative : elle est deleguee au proxy."""
    source = app_source()
    for token in ("slowapi", "Limiter(", "rate_limit", "RateLimitMiddleware"):
        guard(token not in source, f"limitation de debit maison ajoutee dans l'application : {token}")
    guard("slowapi" not in (read("requirements.txt") + read("requirements-dev.txt")).lower(), "slowapi ajoute aux dependances")
    security = fold(read("SECURITY.md"))
    guard("rate limiting par ip" in security, "SECURITY.md ne traite plus la limitation par IP")
    guard("absent de l'application" in security, "SECURITY.md ne dit plus que la limitation est absente de l'application")
    guard(
        "limit_req" in security and "nginx" in security,
        "SECURITY.md ne nomme plus le proxy responsable de la limitation",
    )
    guard("(d21)" in security, "SECURITY.md ne renvoie plus a la decision D21")
    guard("MEASUREMENT" not in read("SECURITY.md"), "placeholder de mesure reste dans SECURITY.md")


def d22() -> None:
    """Les sondes de /health sont hors boucle, en parallele et bornees."""
    from app.routes import health as health_module

    source = read("app/routes/health.py")
    guard("asyncio.to_thread" in source, "les sondes bloquantes ne sont pas sorties de la boucle")
    guard("asyncio.gather" in source, "les deux sondes ne partent plus en parallele")
    guard(
        health_module.PROBE_TIMEOUT_SECONDS == 2.0,
        f"borne = {health_module.PROBE_TIMEOUT_SECONDS} s",
    )
    guard(
        inspect.iscoroutinefunction(health_module.health),
        "la route /health n'est plus async : les sondes bloqueraient la boucle",
    )
    release = threading.Event()

    def pendu() -> str:
        release.wait(10)
        return "trop tard"

    loop = asyncio.new_event_loop()
    try:
        started = time.perf_counter()
        with mock.patch.object(health_module, "check_db", pendu), mock.patch.object(
            health_module, "check_redis", pendu
        ):
            payload = loop.run_until_complete(health_module.health())
        elapsed = time.perf_counter() - started
    finally:
        loop.close()
        release.set()
    guard(
        set(payload) == {"status", "database", "redis", "version"},
        f"contrat de /health change : {sorted(payload)}",
    )
    guard(payload["database"].startswith("timeout after"), f"sonde non bornee : {payload['database']}")
    guard(payload["redis"].startswith("timeout after"), f"sonde non bornee : {payload['redis']}")
    guard(elapsed < 3.5, f"/health a mis {elapsed:.2f}s avec deux sondes qui pendent 10 s")
    guard(payload["status"] == "ok" and payload["version"] == "1.0.0", f"charge utile : {payload}")


def d23() -> None:
    """Le budget d'appel fournisseur est borne, retry conserve."""
    from app.validation import verifier

    timeout = verifier.PROVIDER_TIMEOUT
    got = (timeout.connect, timeout.read, timeout.write, timeout.pool)
    guard(got == (2.0, 4.0, 4.0, 2.0), f"PROVIDER_TIMEOUT={got} au lieu de (2.0, 4.0, 4.0, 2.0)")
    guard(verifier.breaker.failure_threshold == 3, f"seuil applicatif = {verifier.breaker.failure_threshold} au lieu de 3")
    guard(verifier.breaker.recovery_timeout == 60, f"delai applicatif = {verifier.breaker.recovery_timeout}")
    default = verifier.CircuitBreaker()
    guard(
        (default.failure_threshold, default.recovery_timeout) == (5, 60),
        f"les defauts de la classe ont bouge : {(default.failure_threshold, default.recovery_timeout)}",
    )
    retry = verifier._call_smarty.retry
    guard(retry.stop.max_attempt_number == 3, f"tentatives = {retry.stop.max_attempt_number} au lieu de 3")
    guard(type(retry.wait).__name__ == "wait_exponential", f"attente = {type(retry.wait).__name__}")
    guard(
        "timeout=PROVIDER_TIMEOUT" in read("app/validation/verifier.py"),
        "le client fournisseur n'utilise plus le timeout borne",
    )
    guard("stop_after_attempt" in read("app/validation/verifier.py"), "la politique de retry a disparu")


def d24() -> None:
    """/metrics distingue disjoncteur ouvert et backend cache en panne."""
    from app import cache
    from app.routes import metrics as metrics_module
    from app.validation import verifier

    payload = asyncio.run(metrics_module.get_metrics())
    for key in (
        "uptime_seconds",
        "price_per_validation_usdc",
        "environment",
        "provider_circuit_breaker",
        "cache_backend_available",
        "cache_errors",
        "requests_total",
        "cache_hit_ratio",
    ):
        guard(key in payload, f"cle {key} absente de /metrics")
    guard(
        set(payload["provider_circuit_breaker"]) == {"state", "failures", "threshold"},
        f"bloc disjoncteur incomplet : {sorted(payload['provider_circuit_breaker'])}",
    )
    guard(payload["provider_circuit_breaker"]["state"] == verifier.breaker.state, "etat du disjoncteur non reflete")
    verifier.breaker.state = "open"
    try:
        open_payload = asyncio.run(metrics_module.get_metrics())
        guard(
            open_payload["provider_circuit_breaker"]["state"] == "open",
            "l'etat expose est fige : disjoncteur ouvert et fournisseur en echec restent indistinguables",
        )
    finally:
        verifier.breaker.reset()

    class FailingRedis:
        def get(self, key):
            raise RuntimeError("sonde D24")

    cache.reset_backend_state()
    guard(cache.cache_backend_available() is True, "etat du cache non rearme")
    original = cache.redis_client
    cache.redis_client = FailingRedis()
    try:
        cache.get_cached_result("sonde")
        down = asyncio.run(metrics_module.get_metrics())
    finally:
        cache.redis_client = original
        cache.reset_backend_state()
    guard(down["cache_backend_available"] is False, "la panne du cache n'est pas exposee")
    guard(down["cache_errors"] == 1, f"compteur d'erreurs du cache = {down['cache_errors']}")


def d25() -> None:
    """Une panne du backend cache coute une fois, pas a chaque requete."""
    from app import cache

    guard(cache.REDIS_RETRY_COOLDOWN == 30.0, f"cooldown = {cache.REDIS_RETRY_COOLDOWN} s")
    calls = {"get": 0, "setex": 0, "ping": 0}

    class FailingRedis:
        def get(self, key):
            calls["get"] += 1
            raise RuntimeError("sonde D25")

        def setex(self, key, ttl, value):
            calls["setex"] += 1
            raise RuntimeError("sonde D25")

        def ping(self):
            calls["ping"] += 1
            raise RuntimeError("sonde D25")

    original = cache.redis_client
    cache.redis_client = FailingRedis()
    cache.reset_backend_state()
    try:
        guard(cache.cache_backend_available() is True, "le backend est marque indisponible avant toute panne")
        guard(cache.get_cached_result("k") is None, "une lecture en panne ne renvoie pas None")
        guard(calls["get"] == 1, f"lectures Redis payees : {calls['get']}")
        guard(cache.cache_backend_available() is False, "l'echec n'ouvre pas la fenetre de cooldown")
        guard(cache.get_cached_result("k") is None, "lecture servie autrement que None pendant le cooldown")
        cache.store_in_cache("k", {"ok": True})
        guard(
            calls["get"] == 1 and calls["setex"] == 0,
            f"le cooldown ne court-circuite pas les E/S : {calls}",
        )
        guard(cache.check_redis().startswith("error:"), "check_redis ne remonte plus la verite")
        guard(calls["ping"] == 1, "check_redis est court-circuite par le cooldown")
        guard(cache.cache_error_count() == 2, f"compteur d'erreurs = {cache.cache_error_count()} au lieu de 2")
        with mock.patch("app.cache.time.time", return_value=time.time() + 31.0):
            guard(cache.cache_backend_available() is True, "la fenetre de cooldown ne se referme jamais")
            guard(cache.get_cached_result("k") is None, "lecture apres cooldown non servie")
        guard(calls["get"] == 2, f"aucune nouvelle tentative apres le cooldown : {calls['get']}")
        guard(cache.cache_error_count() == 3, f"compteur d'erreurs = {cache.cache_error_count()} au lieu de 3")
    finally:
        cache.redis_client = original
        cache.reset_backend_state()


def d26() -> None:
    """Le geocodeur est borne et degrade en coordonnees nulles."""
    import httpx

    from app.validation import geocoder

    timeout = geocoder.GEOCODER_TIMEOUT
    got = (timeout.connect, timeout.read, timeout.write, timeout.pool)
    guard(got == (2.0, 3.0, 3.0, 2.0), f"GEOCODER_TIMEOUT={got} au lieu de (2.0, 3.0, 3.0, 2.0)")
    guard(geocoder.NOMINATIM_URL.endswith("/search"), f"URL Nominatim : {geocoder.NOMINATIM_URL}")

    class FailingClient:
        def __init__(self, timeout=None) -> None:
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, *exc) -> bool:
            return False

        def get(self, url, params=None, headers=None):
            raise httpx.ConnectTimeout("sonde D26")

    class EmptyClient(FailingClient):
        def get(self, url, params=None, headers=None):
            class Response:
                def raise_for_status(self) -> None:
                    return None

                def json(self):
                    return []

            return Response()

    original = geocoder.httpx.Client
    try:
        geocoder.httpx.Client = FailingClient
        started = time.perf_counter()
        degraded = geocoder.geocode({"line1": "1 rue X", "city": "Paris"})
        elapsed = time.perf_counter() - started
        geocoder.httpx.Client = EmptyClient
        empty = geocoder.geocode({"city": "Nulle part"})
    finally:
        geocoder.httpx.Client = original
    guard(degraded == {"lat": None, "lng": None}, f"panne du geocodeur non absorbee : {degraded}")
    guard(empty == {"lat": None, "lng": None}, f"reponse vide non degradee : {empty}")
    guard(elapsed < 1.0, f"la degradation a mis {elapsed:.2f}s")
    guard("except Exception" in read("app/validation/geocoder.py"), "l'exception du geocodeur n'est plus absorbee")


def d27() -> None:
    """La confirmation on-chain est optionnelle, fail-closed et sans web3."""
    from app import onchain
    from app.config import Settings, get_settings
    from app.payment import PROOF_HEADER, build_payment_proof, verify_payment

    guard(
        Settings.model_fields["x402_onchain_verify"].default is False,
        "la verification on-chain n'est plus desactivee par defaut : D15 a change de sens",
    )
    settings = get_settings()
    wallet = "0x" + "aa" * 20
    tx = "0x" + "ab" * 32
    receipt = {
        "status": "0x1",
        "blockNumber": "0x1000",
        "logs": [
            {
                "address": settings.x402_usdc_contract,
                "topics": [
                    onchain.TRANSFER_TOPIC,
                    "0x" + "00" * 12 + "22" * 20,
                    "0x" + "00" * 12 + wallet[2:],
                ],
                "data": "0x" + format(onchain.usdc_units(settings.x402_price), "064x"),
            }
        ],
    }
    real_wallet, real_flag = settings.cdp_wallet_address, settings.x402_onchain_verify
    calls: list[str] = []
    try:
        settings.cdp_wallet_address = wallet
        with mock.patch.object(onchain, "_rpc", lambda method, params: calls.append(method) or None):
            guard(onchain.check_usdc_transfer(tx)[0] is False, "un recu absent est accepte")
            settings.x402_onchain_verify = True
            guard(
                verify_payment(fake_request({PROOF_HEADER: build_payment_proof(tx)})) is False,
                "une fausse preuve de paiement est acceptee malgre la verification on-chain",
            )
        appels = list(calls)
        guard(
            onchain.check_usdc_transfer("test_tx:1234567890:test_signature")[0] is False,
            "un hash de transaction malforme est accepte",
        )
        guard(calls == appels, "un hash malforme declenche un appel reseau")

        def rpc(method: str, params: list) -> object:
            return receipt if method == "eth_getTransactionReceipt" else "0x1005"

        with mock.patch.object(onchain, "_rpc", rpc):
            ok, reason = onchain.check_usdc_transfer(tx)
            guard(ok is True, f"un transfert USDC confirme est refuse : {reason}")
            guard(
                verify_payment(fake_request({PROOF_HEADER: build_payment_proof(tx)})) is True,
                "une preuve appuyee par un transfert confirme est refusee",
            )
    finally:
        settings.cdp_wallet_address = real_wallet
        settings.x402_onchain_verify = real_flag

    guard(
        verify_payment(fake_request({PROOF_HEADER: build_payment_proof("0xMADEUP")})) is True,
        "la verification on-chain desactivee ne rend plus D15 vrai",
    )
    guard("D27" in read("DECISIONS.md"), "la decision D27 n'est plus consignee dans DECISIONS.md")
    guard(
        re.search(r"X402_ONCHAIN_VERIFY", read(".env.example")) is not None,
        "la variable d'activation n'est plus documentee dans .env.example",
    )


def d28() -> None:
    """Les fichiers de decouverte ne citent que des routes qui repondent."""
    from datetime import datetime, timezone

    import app.main as main

    #: Servis en statique par nginx-discovery.conf, presents dans ce depot.
    statics = {"/llms.txt", "/AGENTS.md", "/.well-known/security.txt"}
    known = statics | {getattr(route, "path", "") for route in main.app.routes}
    # D37 : les deux chemins MCP existent desormais. Ils sont servis par
    # l'application (et non par un fichier statique) pour qu'ils disparaissent
    # avec l'endpoint : l'avant-dernier essai de cette porte refusait `/mcp`
    # justement parce qu'il n'existait pas, et c'est ce controle qui a evite de
    # publier un manifeste MCP vers une URL morte.
    for chemin in ("/mcp", "/.well-known/mcp-server"):
        guard(chemin in known, f"{chemin} est cite mais n'est pas servi par l'application")

    shipped = ["llms.txt", "AGENTS.md", ".well-known/security.txt", "nginx-discovery.conf"]
    for name in shipped:
        path = ROOT / name
        guard(path.is_file(), f"{name} est annonce mais absent du depot")
        raw = path.read_bytes()
        guard(not raw.startswith(b"\xef\xbb\xbf"), f"{name} commence par un BOM UTF-8")
        guard(b"\r" not in raw, f"{name} contient des fins de ligne Windows (CR)")
        guard(raw.endswith(b"\n"), f"{name} ne se termine pas par un saut de ligne")
        guard(len(raw.strip()) > 200, f"{name} est trop court pour etre utile ({len(raw)} octets)")

    cited: set[str] = set()
    for name in ("llms.txt", "AGENTS.md", ".well-known/security.txt"):
        for match in re.finditer(r"kder-ai\.com(/[A-Za-z0-9._~/\-]*)", read(name)):
            cited.add(match.group(1).rstrip(".,;:)"))
    guard(len(cited) >= 3, f"seulement {len(cited)} chemin(s) cite(s) : la porte ne mesure rien")
    unknown = sorted(chemin for chemin in cited if chemin not in known)
    guard(not unknown, f"chemins cites qui ne repondent pas : {unknown}")

    security = read(".well-known/security.txt")
    guard(re.search(r"(?m)^Contact:\s*mailto:\S+@\S+\.\S+", security), "security.txt sans champ Contact utilisable")
    found = re.search(r"(?m)^Expires:\s*(\S+)", security)
    guard(found is not None, "security.txt sans champ Expires, exige par la RFC 9116")
    expires = datetime.fromisoformat(found.group(1).replace("Z", "+00:00"))
    guard(expires.tzinfo is not None, "Expires doit porter un fuseau horaire (RFC 9116)")
    guard(
        expires > datetime.now(timezone.utc),
        f"security.txt est deja perime (Expires={found.group(1)})",
    )

    nginx = read("nginx-discovery.conf")
    for target in sorted(statics):
        guard(
            re.search(rf"(?m)^\s*location\s*=\s*{re.escape(target)}\s*\{{", nginx) is not None,
            f"nginx-discovery.conf ne sert pas {target}",
        )
        guard(
            (ROOT / target.lstrip("/")).is_file(),
            f"nginx sert {target} mais le fichier est absent du depot",
        )
    guard(
        re.search(r"(?m)^\s*location\s*=\s*/\.well-known/mcp-server", nginx) is None,
        "un fichier statique est publie a la place de la carte servie par l'application : "
        "il survivrait a l'arret du service (D37)",
    )
    guard(
        "mcp-server" in nginx and "D37" in nginx,
        "nginx-discovery.conf n'explique plus pourquoi ces deux chemins ne sont pas aliases",
    )


def d29() -> None:
    """Chaque paiement accepte est enregistre sous le hash de sa preuve."""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.payment import PROOF_HEADER, build_payment_proof, tx_hash_from_proof

    guard(
        tx_hash_from_proof("0xabc123:1234567890:signature") == "0xabc123",
        "tx_hash_from_proof ne relit plus la preuve",
    )
    for invalide in ("nimportequoi", ""):
        guard(
            tx_hash_from_proof(invalide) is None,
            f"une preuve invalide ({invalide!r}) produit un hash",
        )
    guard(tx_hash_from_proof(None) is None, "une preuve absente produit un hash")

    ecritures: list[tuple] = []

    def espion(tx_hash: str, amount: float, address: str) -> bool:
        ecritures.append((tx_hash, amount, address))
        return True

    corps = {
        "address": {
            "street": "1 Oracle Way",
            "city": "Springfield",
            "state": "IL",
            "zip": "62701",
            "country": "US",
        }
    }
    # Hors ligne : le sujet de D29 est le *registre*, pas le geocodeur. On coupe
    # donc le fournisseur, le geocodeur et le cache (sujets de D5/D23) afin que
    # la porte reste deterministe et sans I/O sortante.
    resultat = {
        "validated": True,
        "standardized": {
            "line1": "1 Oracle Way",
            "line2": "",
            "city": "Springfield",
            "state": "IL",
            "zip": "62701",
            "country": "US",
        },
        "latitude": 39.78,
        "longitude": -89.65,
        "deliverability": "residential",
        "confidence": 0.9,
        "degraded": False,
    }
    client = TestClient(app)
    with (
        mock.patch("app.routes.validate.validate_full", lambda address: dict(resultat)),
        mock.patch("app.routes.validate.get_cached_result", lambda key: None),
        mock.patch("app.routes.validate.store_in_cache", lambda key, value: None),
        mock.patch("app.routes.validate.record_transaction", espion),
    ):
        for _ in range(2):
            answer = client.post(
                "/validate", json=corps, headers={PROOF_HEADER: build_payment_proof("0xLEDGER")}
            )
            guard(answer.status_code == 200, f"un appel dument paye est refuse ({answer.status_code})")
    guard(
        len(ecritures) == 2,
        f"{len(ecritures)} ecriture(s) pour deux appels payes : la route ne trace plus chaque paiement",
    )
    guard({row[0] for row in ecritures} == {"0xLEDGER"}, f"identifiant enregistre inattendu : {ecritures}")
    guard(all(row[1] == 0.02 for row in ecritures), f"montant enregistre different du prix : {ecritures}")
    guard(
        "ON CONFLICT (tx_hash) DO NOTHING" in read("app/payment.py"),
        "l'ecriture du paiement n'est plus idempotente : un rejeu compterait double",
    )
    guard(
        "tx_hash_from_proof" in read("app/routes/validate.py"),
        "la route ne derive plus l'identifiant du paiement depuis la preuve",
    )
    guard("D29" in read("DECISIONS.md"), "la decision D29 n'est plus consignee dans DECISIONS.md")


def d30() -> None:
    """Une tentative non payee est tracee, avec une IP pseudonymisee par defaut."""
    import json as jsonlib
    import tempfile
    from datetime import datetime, timedelta, timezone

    from app import observability
    from app.config import get_settings

    settings = get_settings()
    originals = (
        settings.discovery_log_enabled,
        settings.discovery_log_path,
        settings.discovery_log_raw_ip,
        settings.discovery_log_salt,
    )
    journal = Path(tempfile.mkdtemp()) / "discovery.log"
    try:
        settings.discovery_log_enabled = True
        settings.discovery_log_path = str(journal)
        settings.discovery_log_raw_ip = False
        settings.discovery_log_salt = "sel-de-porte-d30"

        entree = observability.log_unpaid_attempt(
            fake_request({"X-Forwarded-For": "203.0.113.9", "User-Agent": "agent-porte/1.0"}), 0.02
        )
        lignes = [
            jsonlib.loads(ligne)
            for ligne in journal.read_text(encoding="utf-8").splitlines()
            if ligne.strip()
        ]
        guard(len(lignes) == 1, f"{len(lignes)} ligne(s) ecrite(s) au lieu d'une")
        ligne = lignes[0]
        attendues = (
            "ts",
            "event",
            "endpoint",
            "method",
            "price_usdc",
            "reason",
            "user_agent",
            "ip",
            "ip_pseudonymised",
        )
        for cle in attendues:
            guard(cle in ligne, f"cle {cle!r} absente du journal")
        guard(ligne["event"] == "unpaid_request", f"evenement inattendu : {ligne['event']}")
        guard(
            (ligne["method"], ligne["endpoint"]) == ("POST", "/validate"),
            f"cible mal journalisee : {ligne['method']} {ligne['endpoint']}",
        )
        guard(ligne["price_usdc"] == 0.02, f"prix journalise faux : {ligne['price_usdc']}")
        guard(ligne["user_agent"] == "agent-porte/1.0", f"agent mal journalise : {ligne['user_agent']}")
        guard(ligne["ip_pseudonymised"] is True, "IP annoncee en clair alors que le reglage est desactive")
        guard("203.0.113.9" not in jsonlib.dumps(ligne), "l'IP en clair figure dans le journal")
        guard(ligne["ip"] == observability.pseudonymise("203.0.113.9"), "le pseudonyme n'est pas celui de la fonction")
        guard(len(ligne["ip"]) >= 32, f"pseudonyme trop court : {ligne['ip']!r}")
        guard(
            observability.pseudonymise("203.0.113.9") != observability.pseudonymise("203.0.113.10"),
            "deux IP distinctes partagent le meme pseudonyme",
        )
        interdites = {"address", "street", "city", "zip", "body", "payload", "query", "ip_raw"}
        guard(
            not (interdites & set(ligne)),
            f"donnee d'adresse journalisee : {sorted(interdites & set(ligne))}",
        )

        settings.discovery_log_raw_ip = True
        brut = observability.log_unpaid_attempt(
            fake_request({"X-Forwarded-For": "198.51.100.7"}), 0.02
        )
        guard(
            brut["ip"] == "198.51.100.7",
            f"l'IP en clair n'est plus tracable en enquete : {brut['ip']!r}",
        )
        guard(brut["ip_pseudonymised"] is False, "le drapeau d'IP en clair est faux")

        # Retention annoncee = retention appliquee (PRIVACY.md).
        vieux = {
            "ts": (datetime.now(timezone.utc) - timedelta(days=60)).isoformat(timespec="seconds"),
            "event": "unpaid_request",
        }
        journal.write_text(
            jsonlib.dumps(vieux, sort_keys=True) + "\n" + jsonlib.dumps(entree, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        guard(
            observability.count_since(journal, datetime.now(timezone.utc) - timedelta(days=1)) == 1,
            "le comptage des evenements recents est faux",
        )
        restants = observability.prune(journal, 30)
        guard(restants == 1, f"purge a 30 jours : {restants} entree(s) conservee(s) au lieu d'une")
    finally:
        for handler in list(observability.logger.handlers):
            observability.logger.removeHandler(handler)
            handler.close()
        observability._ATTACHED.clear()  # noqa: SLF001 - remise a zero de l'oracle
        (
            settings.discovery_log_enabled,
            settings.discovery_log_path,
            settings.discovery_log_raw_ip,
            settings.discovery_log_salt,
        ) = originals

    guard(
        re.search(r"DISCOVERY_LOG", read(".env.example")) is not None,
        "les variables du journal de decouverte ne sont pas documentees dans .env.example",
    )
    privacy = fold(read("PRIVACY.md"))
    guard("pseudonymise" in privacy, "PRIVACY.md ne dit pas que l'IP est pseudonymisee")
    guard("journal de decouverte" in privacy, "PRIVACY.md ne decrit pas le journal de decouverte")
    guard("D30" in read("DECISIONS.md"), "la decision D30 n'est plus consignee dans DECISIONS.md")


def d31() -> None:
    """Le revenu publie vient du registre durable, et la panne est avouee."""
    from datetime import datetime, timedelta, timezone

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text

    from app.main import app
    from app.routes.revenue import WINDOWS, aggregate, revenue_stats

    # --- 1. Agregation pure : bornes de fenetres, cumul, deduplication ------
    now = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    lignes = [
        {
            "tx_hash": "0xA",
            "amount": 0.02,
            "address": "cle-a",
            "created_at": (now - timedelta(hours=1)).isoformat(),
        },
        {
            "tx_hash": "0xB",
            "amount": 0.02,
            "address": "cle-a",
            "created_at": (now - timedelta(hours=2)).isoformat(),
        },
        {
            "tx_hash": "0xC",
            "amount": 0.02,
            "address": "cle-b",
            "created_at": (now - timedelta(days=10)).isoformat(),
        },
    ]
    stats = aggregate(lignes, now)
    guard(
        set(stats["windows"]) == set(WINDOWS),
        f"fenetres publiees inattendues : {sorted(stats['windows'])}",
    )
    jour = stats["windows"]["24h"]
    guard(jour["validations_paid"] == 2, f"24 h : {jour['validations_paid']} paiement(s) au lieu de 2")
    guard(jour["revenue_usdc"] == 0.04, f"24 h : revenu {jour['revenue_usdc']} au lieu de 0.04")
    guard(jour["unique_address_keys"] == 1, f"24 h : {jour['unique_address_keys']} cle(s) au lieu d'une")
    guard(stats["windows"]["7d"]["validations_paid"] == 2, "7 j : la fenetre ne borne plus les paiements")
    guard(stats["windows"]["30d"]["validations_paid"] == 3, "30 j : le paiement ancien n'est plus compte")
    guard(
        stats["validations_paid"] == 3 and stats["revenue_usdc"] == 0.06,
        f"cumul faux : {stats['validations_paid']} paiement(s) / {stats['revenue_usdc']} USDC",
    )
    guard(
        stats["unique_proofs"] == 3 and stats["unique_address_keys"] == 2,
        "la deduplication par preuve et par cle de cache a disparu",
    )
    guard(stats["last_observed_at"] is not None, "aucune date d'observation n'est publiee")

    # --- 2. Lecture reelle sur une base jetable (SQL -> agregat) -------------
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE payment_transactions "
                "(tx_hash TEXT PRIMARY KEY, amount REAL, address TEXT, created_at TEXT, "
                "test_mode BOOLEAN)"
            )
        )
        for ligne in lignes:
            conn.execute(
                text(
                    "INSERT INTO payment_transactions "
                    "(tx_hash, amount, address, created_at, test_mode) "
                    "VALUES (:t, :a, :k, :c, :m)"
                ),
                {
                    "t": ligne["tx_hash"],
                    "a": ligne["amount"],
                    "k": ligne["address"],
                    "c": ligne["created_at"],
                    "m": ligne.get("test_mode", False),
                },
            )
    charge = revenue_stats(engine=engine, now=now)
    guard(
        charge["ledger"]["available"] is True,
        f"une base joignable est annoncee indisponible : {charge['ledger']['error']}",
    )
    guard(charge["ledger"]["error"] is None, "une lecture reussie porte une erreur")
    guard(
        charge["windows"]["24h"]["validations_paid"] == 2,
        "la lecture SQL ne remonte plus les paiements des dernieres 24 h",
    )
    guard(
        charge["price_per_validation_usdc"] == 0.02,
        f"prix publie inattendu : {charge['price_per_validation_usdc']}",
    )

    # --- 3. Degradation honnete : la panne est une donnee, pas une erreur ----
    with mock.patch("app.routes.revenue.fetch_rows", side_effect=RuntimeError("base muette")):
        degrade = revenue_stats(engine=engine, now=now)
    guard(degrade["ledger"]["available"] is False, "une base injoignable est annoncee disponible")
    guard(
        "base muette" in str(degrade["ledger"]["error"]),
        f"la panne n'est pas expliquee : {degrade['ledger']['error']!r}",
    )
    guard(degrade["windows"] == {}, "des fenetres sont publiees alors que la base est muette")
    guard("payment_transactions" in str(degrade["source"]), "la source du chiffre n'est plus publiee")

    # --- 4. La route HTTP survit a une base muette --------------------------
    reponse = TestClient(app).get("/metrics/revenue")
    guard(reponse.status_code == 200, f"/metrics/revenue repond {reponse.status_code}")
    corps = reponse.json()
    guard(
        "ledger" in corps and corps["ledger"]["available"] in (True, False),
        f"drapeau ledger.available absent : {sorted(corps)}",
    )
    guard("session" in corps, "les compteurs de session (D17) ont disparu de la reponse")

    source = read("app/routes/revenue.py")
    guard("def aggregate(" in source, "l'agregation Python a disparu")
    guard("LIMIT" in source and "LEDGER_LIMIT" in source, "la lecture du registre n'est plus bornee")
    guard("D31" in read("DECISIONS.md"), "la decision D31 n'est plus consignee dans DECISIONS.md")


def d32() -> None:
    """Le tableau de bord est autonome, protege et ferme par defaut."""
    import base64

    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import app
    from app.routes.dashboard import _page

    settings = get_settings()
    originals = (settings.dashboard_user, settings.dashboard_password)
    client = TestClient(app)
    try:
        # 1. Ferme par defaut : mot de passe absent = 503, jamais 200.
        settings.dashboard_password = ""
        ferme = client.get("/dashboard")
        guard(
            ferme.status_code == 503,
            f"sans DASHBOARD_PASSWORD, /dashboard repond {ferme.status_code} au lieu de 503",
        )
        guard("DASHBOARD_PASSWORD" in ferme.text, "le refus ne nomme pas le reglage a poser")
        guard(ferme.headers.get("cache-control") == "no-store", "un refus 503 reste cachable")

        settings.dashboard_user = "kder"
        settings.dashboard_password = "porte-d32-secret"

        # 2. Sans identifiants : 401 + challenge Basic, sans fuite du secret.
        anonyme = client.get("/dashboard")
        guard(
            anonyme.status_code == 401,
            f"sans identifiants, /dashboard repond {anonyme.status_code} au lieu de 401",
        )
        challenge = anonyme.headers.get("www-authenticate", "")
        guard(challenge.lower().startswith("basic"), f"challenge Basic absent : {challenge!r}")
        guard("porte-d32-secret" not in anonyme.text, "le mot de passe attendu apparait dans le refus")

        def entete(couple: str) -> dict:
            return {"Authorization": "Basic " + base64.b64encode(couple.encode()).decode()}

        # 3. Mauvais couple refuse, y compris avec le bon utilisateur.
        for couple in ("kder:mauvais", "intrus:porte-d32-secret", "sans_deux_points"):
            refus = client.get("/dashboard", headers=entete(couple))
            guard(refus.status_code == 401, f"{couple!r} obtient {refus.status_code} au lieu de 401")

        # 4. Bon couple : page servie, non cachable, avec une CSP.
        page = client.get("/dashboard", headers=entete("kder:porte-d32-secret"))
        guard(page.status_code == 200, f"un couple valide est refuse ({page.status_code})")
        guard(
            page.headers.get("content-type", "").startswith("text/html"),
            f"type de contenu inattendu : {page.headers.get('content-type')!r}",
        )
        guard(
            page.headers.get("cache-control") == "no-store",
            "une page d'exploitation cachable peut finir dans un cache partage",
        )
        guard("content-security-policy" in page.headers, "aucune CSP : l'autonomie n'est pas prouvee")
    finally:
        settings.dashboard_user, settings.dashboard_password = originals

    # 5. La page elle-meme : autonome, sans script, sans injection.
    hostile = "<script>alert('xss')</script>"
    stats = {
        "generated_at": "2026-10-10T12:00:00+00:00",
        "price_per_validation_usdc": 0.02,
        "source": "postgresql:payment_transactions",
        "ledger": {
            "available": False,
            "error": hostile,
            "validations_paid": 0,
            "revenue_usdc": 0.0,
            "unique_proofs": 0,
            "unique_address_keys": 0,
        },
        "windows": {
            "24h": {"revenue_usdc": 0.04},
            "7d": {"revenue_usdc": 0.06},
            "30d": {"revenue_usdc": 0.08},
        },
        "recent": [
            {
                "tx_hash": "0xPORTE",
                "amount_usdc": 0.02,
                "address_key": "cle-a",
                "created_at": "2026-10-10T11:00:00+00:00",
            }
        ],
    }
    html = _page(stats)
    guard("<script" not in html.lower(), "la page embarque du script")
    guard("&lt;script&gt;" in html, "le message d'erreur de la base est injecte sans echappement")
    guard('class="bar"' in html and "fill" in html, "aucune barre de revenu : la page ne montre rien")
    for motif in ("http://", "https://", "//cdn", "@import", "url(http"):
        guard(motif not in html, f"la page charge une ressource externe ({motif})")
    guard("0.04" in html and "0.06" in html, "les fenetres de revenu ne sont plus affichees")
    guard("0xPORTE" in html, "les dernieres transactions ne sont plus affichees")
    guard("incomplet" in fold(html), "une base muette est presentee sans avertissement")

    source = read("app/routes/dashboard.py")
    guard("secrets.compare_digest" in source, "la comparaison des identifiants n'est plus a temps constant")
    guard("Content-Security-Policy" in source, "la CSP du tableau de bord a disparu")
    guard("D32" in read("DECISIONS.md"), "la decision D32 n'est plus consignee dans DECISIONS.md")


def d33() -> None:
    """Le manifeste porte schemas, exemples, cas d'usage et limites explicites."""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.payment import generate_payment_required_response
    from app.schemas import ValidationRequest, ValidationResponse

    reponse = TestClient(app).get("/manifest")
    guard(reponse.status_code == 200, f"/manifest repond {reponse.status_code}")
    manifeste = reponse.json()

    endpoint = manifeste.get("endpoints", {}).get("validate", {})
    guard(endpoint, "le manifeste ne decrit plus l'endpoint /validate")
    guard(
        (endpoint.get("method"), endpoint.get("path")) == ("POST", "/validate"),
        f"le manifeste annonce {endpoint.get('method')} {endpoint.get('path')}",
    )
    guard(
        endpoint.get("input_schema") == ValidationRequest.model_json_schema(),
        "le schema d'entree n'est plus derive du modele Pydantic",
    )
    guard(
        endpoint.get("output_schema") == ValidationResponse.model_json_schema(),
        "le schema de sortie n'est plus derive du modele Pydantic",
    )
    guard(
        endpoint["errors"]["402"]["body_example"]
        == generate_payment_required_response(endpoint["price"]),
        "l'exemple 402 n'est pas le corps que la route renvoie",
    )

    exemples = endpoint.get("examples") or []
    guard(len(exemples) >= 2, f"{len(exemples)} exemple(s) : un seul ne montre pas le mode degrade")
    for exemple in exemples:
        ValidationRequest.model_validate(exemple["request"])
        ValidationResponse.model_validate(exemple["response_200"])
    guard(
        any(exemple["response_200"]["result"]["degraded"] for exemple in exemples),
        "aucun exemple ne montre le mode degrade",
    )

    guard(len(manifeste.get("use_cases") or []) >= 3, "moins de trois cas d'usage publies")
    limites = manifeste.get("limitations") or []
    guard(len(limites) >= 5, f"{len(limites)} limite(s) publiee(s), le manifeste redescend vers la promesse")
    texte = fold(" ".join(limites))
    for attendu in ("x402", "degrade", "mcp", "international"):
        guard(attendu in texte, f"les limites passent sous silence {attendu!r}")

    valeurs = manifeste.get("deliverability_values") or {}
    guard(
        set(valeurs) == {"residential", "commercial", "unknown", "invalid"},
        f"valeurs de deliverabilite inattendues : {sorted(valeurs)}",
    )
    for valeur, explication in valeurs.items():
        guard(len(str(explication)) > 20, f"la valeur {valeur!r} n'est pas expliquee")

    standard = manifeste["payment"]["x402_standard"]
    guard(
        standard["compliant"] is True,
        "le manifeste ne declare pas conforme un protocole desormais implemente (D34-D36)",
    )
    guard(
        standard["missing"] == [],
        f"le manifeste annonce encore des manques alors que D34-D36 sont en place : {standard['missing']}",
    )
    guard(len(standard["verified"]) >= 4, "le manifeste ne liste pas ce qui est verifie")
    guard(
        manifeste["payment"]["header"] == "X-PAYMENT",
        "l'en-tete de paiement annonce n'est plus celui du code",
    )
    guard(
        manifeste["payment"]["accepts"][0]["maxAmountRequired"] == "20000",
        "l'offre publiee dans le manifeste ne correspond plus au prix facture",
    )
    guard(manifeste["contact"]["email"], "aucun contact n'est publie dans le manifeste")
    guard("D33" in read("DECISIONS.md"), "la decision D33 n'est plus consignee dans DECISIONS.md")


# --- Aides du Lot B : paiement x402 reel, sans reseau ni secret -------------
#: Cle jetable des oracles (aucun secret du depot n'est utilise ici).
ORACLE_PRIVATE_KEY = bytes.fromhex("22" * 32)
#: Hash de reglement rendu par le facilitateur simule.
ORACLE_TX = "0x" + "ef" * 32
#: Resultat degrade deterministe, pour ne jamais appeler le fournisseur.
ORACLE_RESULT = {
    "validated": False,
    "standardized": {
        "line1": "1 A St",
        "city": "X",
        "state": "CA",
        "zip": "90001",
        "country": "US",
    },
    "latitude": None,
    "longitude": None,
    "deliverability": "unknown",
    "confidence": 0.2,
    "degraded": True,
}


def install_keccak() -> None:
    """Branche le keccak de secours si le backend natif est bloque (D38)."""
    from tests.keccak_fallback import install_if_needed

    install_if_needed()


def x402_payload(**overrides) -> str:
    """En-tete `X-PAYMENT` **signe reellement** (EIP-712) pour les oracles."""
    import base64
    import json as jsonlib
    import time as timelib

    install_keccak()
    from eth_keys import keys

    from app.config import get_settings
    from app.x402 import eip712_digest, payment_requirements

    price = overrides.pop("price", None) or get_settings().x402_price
    nonce = overrides.pop("nonce", None) or os.urandom(32).hex()
    wallet = keys.PrivateKey(ORACLE_PRIVATE_KEY)
    offer = payment_requirements(price)
    now = int(timelib.time())
    authorization = {
        "from": wallet.public_key.to_address(),
        "to": offer["payTo"],
        "value": offer["maxAmountRequired"],
        "validAfter": now - 60,
        "validBefore": now + 240,
        "nonce": "0x" + nonce,
    }
    authorization.update(overrides)
    digest = eip712_digest(offer, authorization)
    payload = {
        "x402Version": 1,
        "scheme": offer["scheme"],
        "network": offer["network"],
        "payload": {
            "signature": "0x" + bytes(wallet.sign_msg_hash(digest).to_bytes()).hex(),
            "authorization": authorization,
        },
    }
    return base64.b64encode(jsonlib.dumps(payload).encode()).decode()


def fake_facilitator(transaction: str | None = ORACLE_TX):
    """Double du facilitateur : `/verify` puis `/settle` repondent comme le vrai."""
    appels: list[str] = []

    def _post(suffix: str, body: dict) -> dict:
        appels.append(suffix)
        if suffix == "/verify":
            return {"isValid": True, "payer": body["paymentPayload"]["payload"]["authorization"]["from"]}
        return {
            "success": True,
            "transaction": transaction or "",
            "network": "base",
            "payer": body["paymentPayload"]["payload"]["authorization"]["from"],
        }

    _post.appels = appels  # type: ignore[attr-defined]
    return _post


def offline_route():
    """Contexte de `mock.patch` : jamais de fournisseur, de cache ni de base.

    `ExitStack` plutot qu'un tuple de `patch` : un `with` n'accepte pas
    d'eclatement (`with *patchs:`), et une pile de contextes est exactement ce
    qu'on veut — quatre remplacements poses ensemble, defaits ensemble.
    """
    from contextlib import ExitStack

    stack = ExitStack()
    for cible, valeur in (
        ("app.routes.validate.validate_full", lambda raw: dict(ORACLE_RESULT)),
        ("app.routes.validate.get_cached_result", lambda key: None),
        ("app.routes.validate.store_in_cache", lambda key, value: None),
        ("app.routes.validate.record_transaction", lambda tx, amount, key: True),
    ):
        stack.enter_context(mock.patch(cible, valeur))
    return stack


def d34() -> None:
    """Le 402 est un PaymentRequired x402 v1, et un paiement regle recoit sa reponse."""
    import base64
    import json as jsonlib

    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import app
    from app.payment import PROOF_HEADER, build_payment_proof
    from app.x402 import (
        PAYMENT_HEADER,
        PAYMENT_RESPONSE_HEADER,
        X402_VERSION,
        reset_nonce_cache,
    )

    settings = get_settings()
    client = TestClient(app)
    corps = {"address": {"street": "1 A St", "city": "X", "state": "CA", "zip": "90001"}}

    # --- 1. Le corps 402 est un document PaymentRequired complet ------------
    refus = client.post("/validate", json=corps)
    guard(refus.status_code == 402, f"/validate sans paiement repond {refus.status_code}")
    body = refus.json()
    guard(body["x402Version"] == X402_VERSION, f"x402Version publiee : {body.get('x402Version')}")
    guard(isinstance(body.get("error"), str) and body["error"], "le 402 ne dit pas pourquoi il refuse")
    guard(len(body["accepts"]) == 1, f"{len(body['accepts'])} offre(s) publiee(s) au lieu d'une")
    offre = body["accepts"][0]
    attendus = (
        "scheme",
        "network",
        "maxAmountRequired",
        "asset",
        "payTo",
        "resource",
        "description",
        "mimeType",
        "maxTimeoutSeconds",
        "extra",
    )
    for champ in attendus:
        guard(champ in offre, f"champ x402 obligatoire absent de l'offre : {champ}")
    guard(offre["scheme"] == "exact", f"schema publie : {offre['scheme']}")
    guard(offre["network"] == settings.x402_network, f"reseau publie : {offre['network']}")
    guard(
        offre["maxAmountRequired"] == "20000",
        f"0,02 USDC doit s'ecrire 20000 unites atomiques, pas {offre['maxAmountRequired']}",
    )
    guard(
        offre["asset"].lower() == settings.x402_usdc_contract.lower(),
        f"jeton publie inattendu : {offre['asset']}",
    )
    guard(
        offre["payTo"].lower() == settings.cdp_wallet_address.lower(),
        f"adresse d'encaissement publiee : {offre['payTo']}",
    )
    guard(offre["resource"].endswith("/validate"), f"ressource publiee : {offre['resource']}")
    guard(offre["maxTimeoutSeconds"] > 0, "fenetre de validite nulle")
    guard(
        offre["extra"].get("name") and offre["extra"].get("version"),
        "le domaine EIP-712 (name/version) n'est pas annonce : un client ne peut pas signer",
    )
    # Les cles historiques restent servies (outillage), elles ne remplacent rien.
    guard(body.get("price") == 0.02 and body.get("currency") == "USDC", "cles historiques disparues")

    # --- 2. Le manifeste publie exactement la meme offre -------------------
    manifeste = client.get("/manifest").json()
    guard(
        manifeste["payment"]["accepts"][0] == offre,
        "l'offre publiee dans /manifest differe de celle du corps 402",
    )
    standard = manifeste["payment"]["x402_standard"]
    guard(standard["compliant"] is True, "le manifeste ne se declare pas conforme x402")
    guard(standard["missing"] == [], f"le manifeste annonce encore des manques : {standard['missing']}")
    guard(len(standard["verified"]) >= 4, "le manifeste ne liste pas ce qui est verifie")
    guard(manifeste["payment"]["header"] == PAYMENT_HEADER, "en-tete client mal annonce")
    guard(
        manifeste["payment"]["response_header"] == PAYMENT_RESPONSE_HEADER,
        "en-tete de reglement mal annonce",
    )

    # --- 3. Un paiement regle pose X-PAYMENT-RESPONSE et alimente le registre
    ecritures: list[str] = []
    facilitateur = fake_facilitator()
    reset_nonce_cache()
    with (
        mock.patch("app.x402._post_facilitator", facilitateur),
        mock.patch("app.routes.validate.validate_full", lambda raw: dict(ORACLE_RESULT)),
        mock.patch("app.routes.validate.get_cached_result", lambda key: None),
        mock.patch("app.routes.validate.store_in_cache", lambda key, value: None),
        mock.patch("app.routes.validate.record_transaction", lambda tx, amount, key: ecritures.append(tx)),
    ):
        paye = client.post("/validate", json=corps, headers={PAYMENT_HEADER: x402_payload()})
    reset_nonce_cache()
    guard(paye.status_code == 200, f"un paiement regle est refuse (HTTP {paye.status_code})")
    guard(
        facilitateur.appels == ["/verify", "/settle"],
        f"ordre d'appel du facilitateur inattendu : {facilitateur.appels}",
    )
    recu = jsonlib.loads(base64.b64decode(paye.headers[PAYMENT_RESPONSE_HEADER] + "==="))
    guard(recu["success"] is True, f"un reglement reussi est annonce en echec : {recu}")
    guard(recu["transaction"] == ORACLE_TX, f"hash de reglement non publie : {recu}")
    guard(recu["network"] == settings.x402_network, f"reseau du reglement : {recu}")
    guard(recu.get("payer"), "le payeur n'est pas publie")
    guard(ecritures == [ORACLE_TX], f"le registre a recu {ecritures} au lieu du hash regle")
    guard(paye.json()["transaction_id"] == ORACLE_TX, "la reponse ne cite pas la transaction reglee")

    # --- 4. La voie heritage n'est ni cassee ni presentee comme du x402 -----
    heritage = client.post(
        "/validate", json=corps, headers={PROOF_HEADER: build_payment_proof("0xORACLE34")}
    )
    guard(heritage.status_code == 200, "la preuve HMAC historique ne sert plus")
    guard(
        PAYMENT_RESPONSE_HEADER not in heritage.headers,
        "un paiement HMAC est annonce comme un reglement x402",
    )
    guard("D34" in read("DECISIONS.md"), "la decision D34 n'est plus consignee dans DECISIONS.md")


def d35() -> None:
    """La signature EIP-712 est verifiee hors ligne, avant tout appel reseau."""
    import base64
    import json as jsonlib
    import time as timelib

    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import app
    from app import x402

    install_keccak()
    settings = get_settings()
    client = TestClient(app)
    corps = {"address": {"street": "1 A St", "city": "X", "state": "CA", "zip": "90001"}}

    # --- 1. Vecteur externe : la charge utile que publie la spec x402 -------
    # Si le keccak, l'encodage ABI ou la reconstruction du domaine etaient faux,
    # l'adresse recuperee ne pourrait pas etre celle du document.
    spec_signature = (
        "0x2d6a7588d6acca505cbf0d9a4a227e0c52c6c34008c8e8986a12832597641736"
        "08a2ce6496642e377d6da8dbbf5836e9bd15092f9ecab05ded3d6293af148b571c"
    )
    spec_authorization = {
        "from": "0x857b06519E91e3A54538791bDbb0E22373e36b66",
        "to": "0x209693Bc6afc0C5328bA36FaF03C514EF312287C",
        "value": "10000",
        "validAfter": "1740672089",
        "validBefore": "1740672154",
        "nonce": "0xf3746613c2d920b5fdabc0856f2aeb2d4f88ee6037b8cc5d04a71a4462f13480",
    }
    anciens = (settings.x402_domain_name, settings.x402_domain_version, settings.x402_chain_id)
    try:
        settings.x402_domain_name = "USDC"
        settings.x402_domain_version = "2"
        settings.x402_chain_id = 84532
        exigences = {"asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e"}
        guard(
            x402.authorization_struct_hash(exigences, spec_authorization).hex()
            == "49ad644555c7bdf3ea4fc022a4940ae5589b230b1dde0065c5504fa19bbe39a4",
            "le struct hash EIP-712 ne reproduit pas celui rendu par eth_account sur le VPS",
        )
        guard(
            x402.eip712_digest(exigences, spec_authorization).hex()
            == "f256992871671abcb27ff92885a7afa46218724e5fc0bac35d050115aa1d22e6",
            "le digest EIP-712 ne correspond pas a la reference",
        )
        rendu = x402.recover_signer(exigences, spec_authorization, spec_signature)
        guard(
            rendu.lower() == spec_authorization["from"].lower(),
            f"le signataire documente n'est pas retrouve : {rendu}",
        )
    finally:
        settings.x402_domain_name, settings.x402_domain_version, settings.x402_chain_id = anciens

    # --- 2. Convention `v` : 27/28 et 0/1 doivent etre acceptees ------------
    entete = jsonlib.loads(base64.b64decode(x402_payload() + "==="))
    signature = bytes.fromhex(entete["payload"]["signature"][2:])
    offre = x402.payment_requirements(settings.x402_price)
    basse = signature[:64] + bytes([signature[64] - 27 if signature[64] >= 27 else signature[64]])
    haute = signature[:64] + bytes([(signature[64] + 27) if signature[64] < 27 else signature[64]])
    attendu = x402.recover_signer(offre, entete["payload"]["authorization"], "0x" + basse.hex())
    guard(
        x402.recover_signer(offre, entete["payload"]["authorization"], "0x" + haute.hex()) == attendu,
        "une signature en convention 27/28 est refusee alors qu'elle est valide",
    )

    # --- 3. Refus locaux : le facilitateur ne doit jamais etre appele -------
    appels: list[str] = []

    def interdit(suffix: str, body: dict) -> dict:
        appels.append(suffix)
        raise AssertionError("le facilitateur est appele avant la verification locale")

    def reencode(changements: dict) -> str:
        charge = jsonlib.loads(base64.b64decode(x402_payload() + "==="))
        charge.update(changements)
        return base64.b64encode(jsonlib.dumps(charge).encode()).decode()

    signatures_falsifiees = jsonlib.loads(base64.b64decode(x402_payload() + "==="))
    signatures_falsifiees["payload"]["signature"] = "0x" + "11" * 65

    montant_illisible = jsonlib.loads(base64.b64decode(x402_payload() + "==="))
    montant_illisible["payload"]["authorization"]["value"] = "pas-un-nombre"

    cas = {
        "signature falsifiee": base64.b64encode(jsonlib.dumps(signatures_falsifiees).encode()).decode(),
        "montant illisible": base64.b64encode(jsonlib.dumps(montant_illisible).encode()).decode(),
        "destinataire incorrect": x402_payload(to="0x" + "99" * 20),
        "montant insuffisant": x402_payload(value="19999"),
        "autorisation expiree": x402_payload(validBefore=int(timelib.time()) - 1),
        "autorisation future": x402_payload(validAfter=int(timelib.time()) + 600),
        "reseau inconnu": reencode({"network": "solana"}),
        "schema inconnu": reencode({"scheme": "permit2"}),
    }
    with (
        mock.patch("app.x402._post_facilitator", interdit),
        mock.patch("app.routes.validate.validate_full", lambda raw: dict(ORACLE_RESULT)),
        mock.patch("app.routes.validate.get_cached_result", lambda key: None),
        mock.patch("app.routes.validate.store_in_cache", lambda key, value: None),
        mock.patch("app.routes.validate.record_transaction", lambda tx, amount, key: True),
    ):
        for nom, valeur in cas.items():
            reponse = client.post("/validate", json=corps, headers={x402.PAYMENT_HEADER: valeur})
            guard(reponse.status_code == 402, f"paiement accepte alors qu'il est invalide : {nom}")
    guard(appels == [], f"le facilitateur a ete appele pour un paiement non verifie : {appels}")

    # --- 4. Le keccak utilise est correct (vecteurs publics) ----------------
    from tests.keccak_fallback import keccak256

    guard(
        keccak256(b"").hex()
        == "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470",
        "keccak256(b'') n'est pas le condensat attendu",
    )
    guard(
        keccak256(b"abc").hex()
        == "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45",
        "keccak256(b'abc') n'est pas le condensat attendu",
    )
    guard("D35" in read("DECISIONS.md"), "la decision D35 n'est plus consignee dans DECISIONS.md")


def d36() -> None:
    """Le service n'est rendu qu'apres reglement : tout echec de reglement refuse."""
    import base64
    import json as jsonlib

    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import app
    from app import x402

    install_keccak()
    settings = get_settings()
    client = TestClient(app)
    corps = {"address": {"street": "1 A St", "city": "X", "state": "CA", "zip": "90001"}}

    def passe(entete: str, double, reset: bool = True):
        """Un appel x402 avec un facilitateur donne, tout le reste hors ligne.

        `reset=False` conserve le cache de nonces : c'est indispensable pour
        mesurer un **rejeu**, qui ne se voit que si la memoire du premier
        reglement survit (le premier essai de cet oracle remettait le cache a
        zero entre les deux appels et ne mesurait donc rien).
        """
        if reset:
            x402.reset_nonce_cache()
        with mock.patch("app.x402._post_facilitator", double), offline_route():
            reponse = client.post("/validate", json=corps, headers={x402.PAYMENT_HEADER: entete})
        if reset:
            x402.reset_nonce_cache()
        return reponse

    def reglement(reponse: dict):
        return lambda suffix, body: {"isValid": True} if suffix == "/verify" else reponse

    # --- 1. Reglement refuse par le facilitateur ---------------------------
    refus = passe(
        x402_payload(),
        reglement({"success": False, "errorReason": "insufficient_funds", "transaction": ""}),
    )
    guard(refus.status_code == 402, f"un reglement refuse donne HTTP {refus.status_code}")
    guard(
        "insufficient_funds" in refus.json()["error"],
        f"le refus ne dit pas pourquoi : {refus.json()['error']}",
    )
    recu = jsonlib.loads(base64.b64decode(refus.headers[x402.PAYMENT_RESPONSE_HEADER] + "==="))
    guard(recu["success"] is False, "un echec de reglement est annonce comme un succes")
    guard(recu.get("errorReason"), "l'echec de reglement ne porte pas de raison")
    guard(recu["transaction"] == "", f"un echec de reglement cite une transaction : {recu}")

    # --- 2. Reglement annonce reussi mais sans hash : non verifiable -------
    sans_hash = passe(
        x402_payload(), reglement({"success": True, "transaction": "", "network": "base"})
    )
    guard(sans_hash.status_code == 402, "un reglement sans hash est accepte")
    guard("hash" in sans_hash.json()["error"], f"le refus ne nomme pas le hash : {sans_hash.json()['error']}")

    # --- 3. Verification refusee par le facilitateur ----------------------
    verif_ko = passe(
        x402_payload(),
        lambda suffix, body: (
            {"isValid": False, "invalidReason": "insufficient_balance"}
            if suffix == "/verify"
            else {"success": True, "transaction": ORACLE_TX, "network": "base"}
        ),
    )
    guard(verif_ko.status_code == 402, "un paiement refuse par le facilitateur est accepte")
    guard("insufficient_balance" in verif_ko.json()["error"], "la raison du facilitateur n'est pas reprise")

    # --- 4. Panne de transport : refus, jamais service gratuit ------------
    import httpx

    def injoignable(suffix: str, body: dict) -> dict:
        raise httpx.ConnectError("facilitateur injoignable")

    panne = passe(x402_payload(), injoignable)
    guard(panne.status_code == 402, f"une panne du facilitateur donne HTTP {panne.status_code}")
    guard("impossible" in panne.json()["error"], f"la panne n'est pas expliquee : {panne.json()['error']}")

    # --- 5. Rejeu : un nonce deja regle ne rappelle pas le facilitateur ----
    facilitateur = fake_facilitator()
    premier = passe(x402_payload(nonce="33" * 32), facilitateur, reset=False)
    guard(premier.status_code == 200, f"le premier reglement echoue (HTTP {premier.status_code})")
    guard(facilitateur.appels == ["/verify", "/settle"], f"appels inattendus : {facilitateur.appels}")
    second = passe(x402_payload(nonce="33" * 32), facilitateur, reset=False)
    guard(second.status_code == 402, "une autorisation deja reglee est rejouee avec succes")
    guard("deja reglee" in second.json()["error"], f"le rejeu n'est pas nomme : {second.json()['error']}")
    guard(
        facilitateur.appels == ["/verify", "/settle"],
        f"le rejeu a rappele le facilitateur : {facilitateur.appels}",
    )
    x402.reset_nonce_cache()

    # --- 6. Le defaut est bien « reglement obligatoire » -------------------
    guard(
        settings.x402_require_settlement is True,
        "le reglement n'est plus obligatoire par defaut : service rendu sans encaissement",
    )
    guard(
        settings.x402_facilitator_verify is True,
        "la verification par le facilitateur est desactivee par defaut",
    )


    # --- 7. L'authentification CDP est reellement signee ------------------
    import base64 as b64

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    cle = ed25519.Ed25519PrivateKey.generate()
    secret = b64.b64encode(
        cle.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        + cle.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode()
    anciens = (settings.cdp_api_key_id, settings.cdp_api_key_secret)
    try:
        settings.cdp_api_key_id = "11111111-2222-3333-4444-555555555555"
        settings.cdp_api_key_secret = secret
        entetes = x402.facilitator_headers("POST", f"{settings.x402_facilitator_url}/settle")
    finally:
        settings.cdp_api_key_id, settings.cdp_api_key_secret = anciens

    jeton = entetes.get("Authorization", "")
    guard(jeton.startswith("Bearer "), "aucun jeton CDP n'est joint a l'appel du facilitateur")
    charge = jeton.split(" ", 1)[1].split(".")[1]
    claims = jsonlib.loads(b64.urlsafe_b64decode(charge + "=" * (-len(charge) % 4)))
    guard(claims.get("iss") == "cdp", f"emetteur du jeton inattendu : {claims.get('iss')}")
    guard(claims.get("aud") == ["cdp_service"], f"audience du jeton inattendue : {claims.get('aud')}")
    guard(
        claims.get("sub") == "11111111-2222-3333-4444-555555555555",
        "le jeton ne porte pas la cle CDP",
    )
    guard(
        any(str(uri).endswith("/settle") for uri in claims.get("uris", [])),
        f"le jeton ne restreint pas l'appel a /settle : {claims.get('uris')}",
    )
    guard("D36" in read("DECISIONS.md"), "la decision D36 n'est plus consignee dans DECISIONS.md")


def d37() -> None:
    """Le serveur MCP expose un outil paye, et sa carte decrit l'endpoint reel."""
    import json as jsonlib

    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import app
    from app.routes.mcp import MCP_PROTOCOL_VERSION, tool_definition
    from app.schemas import ValidationRequest, ValidationResponse
    from app import x402

    install_keccak()
    settings = get_settings()
    client = TestClient(app)
    arguments = {
        "address": {"street": "1 A St", "city": "X", "state": "CA", "zip": "90001"}
    }
    http_402 = client.post("/validate", json=arguments).json()

    def rpc(methode: str, params: dict | None = None, identifiant: int = 1, headers=None):
        corps = {"jsonrpc": "2.0", "id": identifiant, "method": methode}
        if params is not None:
            corps["params"] = params
        return client.post("/mcp", json=corps, headers=headers or {})

    # --- 1. Decouvrir est gratuit, et derive du code -----------------------
    init = rpc("initialize", {"protocolVersion": MCP_PROTOCOL_VERSION})
    guard(init.status_code == 200, f"initialize repond {init.status_code}")
    guard(
        init.headers.get("MCP-Protocol-Version") == MCP_PROTOCOL_VERSION,
        "la version du protocole n'est pas annoncee dans la reponse",
    )
    resultat = init.json()["result"]
    guard(resultat["protocolVersion"] == MCP_PROTOCOL_VERSION, f"version negociee : {resultat['protocolVersion']}")
    guard(resultat["serverInfo"]["name"] == "address-validator", "le serveur ne se nomme pas")
    guard("tools" in resultat["capabilities"], "la capacite tools n'est pas annoncee")
    guard(resultat.get("instructions"), "aucune instruction pour l'agent")

    outils = rpc("tools/list").json()["result"]["tools"]
    guard(outils == [tool_definition()], "tools/list ne rend pas la definition du code")
    guard(
        outils[0]["inputSchema"] == ValidationRequest.model_json_schema(),
        "le schema d'entree MCP n'est plus derive du modele Pydantic",
    )
    guard(
        outils[0]["outputSchema"] == ValidationResponse.model_json_schema(),
        "le schema de sortie MCP n'est plus derive du modele Pydantic",
    )
    guard(rpc("ping").json()["result"] == {}, "ping ne repond pas")

    # --- 2. L'outil paye refuse sans paiement, en publiant l'offre ---------
    sans = rpc("tools/call", {"name": "validate_address", "arguments": arguments})
    guard(sans.status_code == 402, f"un appel d'outil non paye donne HTTP {sans.status_code}")
    erreur = sans.json()["error"]
    guard(erreur["code"] == -32002, f"code d'erreur inattendu : {erreur['code']}")
    guard(
        erreur["data"]["accepts"][0] == http_402["accepts"][0],
        "l'offre publiee par MCP differe de celle du 402 HTTP",
    )
    guard(
        rpc("tools/call", {"name": "validate_address", "arguments": {}}).json()["error"]["code"]
        == -32602,
        "des arguments invalides ne sont pas refuses en -32602",
    )

    # --- 3. Appel paye : contenu structure et registre sur le hash regle ---
    ecritures: list[str] = []
    x402.reset_nonce_cache()
    with (
        mock.patch("app.x402._post_facilitator", fake_facilitator()),
        mock.patch("app.routes.mcp.perform_validation", lambda raw: (dict(ORACLE_RESULT), "cle-oracle")),
        mock.patch("app.routes.mcp.record_transaction", lambda tx, amount, key: ecritures.append(tx)),
    ):
        paye = rpc(
            "tools/call",
            {"name": "validate_address", "arguments": arguments},
            headers={x402.PAYMENT_HEADER: x402_payload()},
        )
    x402.reset_nonce_cache()
    guard(paye.status_code == 200, f"un appel d'outil paye echoue (HTTP {paye.status_code})")
    contenu = paye.json()["result"]
    guard(contenu["isError"] is False, "le resultat est marque en erreur")
    structure = contenu["structuredContent"]
    guard(structure["transaction_id"] == ORACLE_TX, f"transaction du resultat : {structure['transaction_id']}")
    guard(structure["price_paid"] == 0.02, "le prix facture n'est pas publie")
    guard(structure["result"]["cache_key"] == "cle-oracle", "le resultat ne vient pas du pipeline partage")
    guard(ecritures == [ORACLE_TX], f"le registre a recu {ecritures} au lieu du hash regle")
    guard(
        jsonlib.loads(contenu["content"][0]["text"]) == structure,
        "le contenu texte et le contenu structure divergent",
    )


    # --- 4. Protocole JSON-RPC : notification, methode inconnue, GET -------
    notif = client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    guard(notif.status_code == 202, f"une notification repond {notif.status_code}")
    guard(notif.content == b"", "une notification recoit un corps de reponse")
    guard(
        rpc("tools/teleport").json()["error"]["code"] == -32601,
        "une methode inconnue ne rend pas METHOD_NOT_FOUND",
    )
    guard(
        client.post("/mcp", content=b"{pas-du-json").json()["error"]["code"] == -32700,
        "un corps illisible ne rend pas PARSE_ERROR",
    )
    refus_get = client.get("/mcp")
    guard(refus_get.status_code == 405, f"GET /mcp repond {refus_get.status_code} au lieu de 405")
    guard(refus_get.headers.get("allow") == "POST", "le refus ne dit pas que POST est attendu")

    # --- 5. La carte publie l'endpoint reel, sans champ invente ------------
    carte = client.get("/.well-known/mcp-server")
    guard(carte.status_code == 200, f"la carte repond {carte.status_code}")
    document = carte.json()
    champs = {
        "mcp_version",
        "name",
        "description",
        "endpoint",
        "transport",
        "auth",
        "capabilities",
        "categories",
        "languages",
        "coverage",
        "contact",
        "docs",
        "last_updated",
        "cache_ttl",
        "crawl",
    }
    guard(
        set(document) == champs,
        f"la carte publie des champs non documentes : {sorted(set(document) - champs)}",
    )
    guard(document["endpoint"] == settings.mcp_public_url, f"endpoint publie : {document['endpoint']}")
    guard(document["mcp_version"] == MCP_PROTOCOL_VERSION, "version MCP publiee incoherente")
    guard(document["transport"] == "http", f"transport publie : {document['transport']}")
    guard(
        document["auth"] == {"required": True, "methods": ["x402"]},
        f"authentification publiee : {document['auth']}",
    )
    guard(document["capabilities"] == ["tools"], "la carte annonce des capacites non servies")
    guard(document["coverage"] == "US", "la couverture reelle (Etats-Unis) n'est pas annoncee")
    guard(document["contact"] == settings.contact_email, "aucun contact publiable dans la carte")
    guard(document["cache_ttl"] > 0 and document["crawl"] is True, "directives de cache absentes")
    guard(
        not (ROOT / ".well-known" / "mcp-server").is_file(),
        "une copie statique de la carte survivrait a l'endpoint qu'elle decrit",
    )

    # --- 6. Le manifeste publie le meme contrat MCP ------------------------
    mcp_manifeste = client.get("/manifest").json()["endpoints"]["mcp"]
    guard(mcp_manifeste["path"] == "/mcp", f"chemin publie : {mcp_manifeste['path']}")
    guard(mcp_manifeste["tool"] == "validate_address", "l'outil publie differe de l'outil servi")
    guard(mcp_manifeste["paid_methods"] == ["tools/call"], "la frontiere payant/gratuit n'est pas publiee")
    guard("tools/list" in mcp_manifeste["free_methods"], "la decouverte gratuite n'est pas publiee")
    guard(
        mcp_manifeste["input_schema"] == ValidationRequest.model_json_schema(),
        "le manifeste publie un schema MCP different du contrat",
    )
    guard("D37" in read("DECISIONS.md"), "la decision D37 n'est plus consignee dans DECISIONS.md")


def d38() -> None:
    """Les fichiers d'annuaire decrivent l'endpoint reel, dans un format source."""
    import json as jsonlib

    from app.config import get_settings

    settings = get_settings()

    # --- 1. server.json : registre MCP officiel (schema 2025-12-11) --------
    serveur = jsonlib.loads(read("server.json"))
    guard(
        serveur.get("$schema")
        == "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json",
        f"schema du registre inattendu : {serveur.get('$schema')}",
    )
    # Le registre MCP derive le namespace du compte GitHub authentifie, casse
    # comprise : `mcp-publisher validate` n'accepte que la graphie rendue par
    # l'API GitHub (`io.github.Kder974`, verifie le 12/10/2026). L'oracle ne peut
    # pas rejouer cette authentification ; il verifie donc ce qui lui appartient :
    # aucun autre proprietaire ni depot ne peut se faire passer pour ce projet,
    # la casse du proprietaire etant arbitree par le registre lui-meme.
    proprietaire, _, depot = serveur["name"].partition("/")
    guard(
        proprietaire.lower() == "io.github.kder974" and depot == "address-validator",
        f"nom de registre inattendu : {serveur['name']} (forme io.github.<proprietaire>/<depot>)",
    )
    guard(serveur["version"] == "1.0.0", f"version publiee : {serveur['version']}")
    guard(
        serveur["repository"] == {"url": "https://github.com/Kder974/address-validator", "source": "github"},
        "le depot publie n'est pas celui du projet",
    )
    remotes = serveur["remotes"]
    guard(len(remotes) == 1, f"{len(remotes)} endpoint(s) publie(s) au lieu d'un")
    guard(remotes[0]["type"] == "streamable-http", f"transport publie : {remotes[0]['type']}")
    guard(
        remotes[0]["url"] == settings.mcp_public_url,
        f"endpoint publie ({remotes[0]['url']}) different de l'endpoint servi",
    )
    connus = {
        "$schema",
        "name",
        "description",
        "title",
        "version",
        "websiteUrl",
        "repository",
        "remotes",
        "packages",
        "_meta",
    }
    guard(
        set(serveur) <= connus,
        f"server.json porte des champs hors schema : {sorted(set(serveur) - connus)}",
    )

    # --- 2. .mcp.json : configuration client, endpoint unique -------------
    mcp_json = jsonlib.loads(read(".mcp.json"))
    guard(list(mcp_json["mcpServers"]) == ["address-validator"], "serveur client mal nomme")
    guard(
        mcp_json["mcpServers"]["address-validator"]
        == {"type": "http", "url": settings.mcp_public_url},
        f"configuration client inattendue : {mcp_json['mcpServers']['address-validator']}",
    )

    # --- 3. smithery.yaml : procedure documentee, aucun schema invente ----
    smithery = read("smithery.yaml")
    guard(
        "mcp publish https://kder-ai.com/mcp" in smithery,
        "la commande de publication officielle (par URL) n'est pas citee",
    )
    guard(
        "@smithery/cli" in smithery,
        "le client officiel (npx @smithery/cli) n'est pas nomme",
    )
    guard(
        "startCommand" not in smithery and "commandFunction" not in smithery,
        "un schema de serveur local non verifie est publie : c'est ce que D28 interdit",
    )
    guard(settings.mcp_public_url in smithery, "l'endpoint reel n'est pas cite dans smithery.yaml")
    for fichier in ("server.json", ".mcp.json", "smithery.yaml"):
        brut = (ROOT / fichier).read_bytes()
        guard(b"\r" not in brut, f"{fichier} contient des fins de ligne Windows")
        guard(brut.endswith(b"\n"), f"{fichier} ne se termine pas par un saut de ligne")

    # --- 4. La porte de paiement ne depend plus d'un transitif ------------
    pins = requirement_pins("requirements.txt")
    for paquet in ("eth-abi", "eth-keys", "eth-utils", "eth-hash", "pycryptodome"):
        guard(paquet in pins, f"{paquet} n'est pas epingle dans requirements.txt")
        guard(
            installed(paquet) == pins[paquet],
            f"{paquet} installe ({installed(paquet)}) different de l'epingle ({pins[paquet]})",
        )
    guard(
        "cdp-sdk" in pins and pins["cdp-sdk"] == "1.0.1",
        "le SDK CDP (JWT du facilitateur) n'est plus epingle",
    )
    guard("D38" in read("DECISIONS.md"), "la decision D38 n'est plus consignee dans DECISIONS.md")


def d39() -> None:
    """La retention annoncee est appliquee par une tache planifiee, pas promise."""
    import json as jsonlib
    import subprocess
    import tempfile
    from datetime import datetime, timedelta, timezone

    from app.config import get_settings

    # --- 1. La duree est celle annoncee publiquement ----------------------
    guard(
        get_settings().discovery_log_retention_days == 30,
        f"retention par defaut : {get_settings().discovery_log_retention_days} jours",
    )
    exemple = read(".env.example")
    guard(
        "DISCOVERY_LOG_RETENTION_DAYS=30" in exemple,
        "le .env d'exemple n'annonce plus la retention de 30 jours",
    )
    guard(
        "retention" in fold(read("PRIVACY.md")) or "conservation" in fold(read("PRIVACY.md")),
        "PRIVACY.md ne documente plus la duree de conservation",
    )

    # --- 2. Le script de purge supprime reellement les entrees agees -------
    journal = Path(tempfile.mkdtemp()) / "discovery.jsonl"
    vieux = {
        "ts": (datetime.now(timezone.utc) - timedelta(days=60)).isoformat(timespec="seconds"),
        "event": "unpaid_request",
    }
    recent = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "event": "unpaid_request",
    }
    journal.write_text(
        jsonlib.dumps(vieux, sort_keys=True) + "\n" + jsonlib.dumps(recent, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    environnement = dict(os.environ)
    environnement.update(
        {"DISCOVERY_LOG_PATH": str(journal), "DISCOVERY_LOG_RETENTION_DAYS": "30"}
    )
    resultat = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "prune_discovery_log.py")],
        capture_output=True,
        text=True,
        env=environnement,
        timeout=120,
    )
    guard(resultat.returncode == 0, f"la purge sort en {resultat.returncode} : {resultat.stderr[-200:]}")
    guard("PRUNE OK" in resultat.stdout, f"la purge ne confirme rien : {resultat.stdout!r}")
    restantes = [ligne for ligne in journal.read_text(encoding="utf-8").splitlines() if ligne.strip()]
    guard(len(restantes) == 1, f"{len(restantes)} entree(s) conservee(s) au lieu d'une")
    guard(recent["ts"] in restantes[0], "l'entree recente a ete supprimee par la purge")
    guard(vieux["ts"] not in restantes[0], "l'entree de 60 jours n'a pas ete purgeee")

    # --- 3. La tache planifiee existe et cible ce script -------------------
    service = read("address-validator-retention.service")
    timer = read("address-validator-retention.timer")
    guard("prune_discovery_log.py" in service, "le service ne lance pas le script de purge")
    guard("EnvironmentFile=" in service, "le service n'herite pas du .env (retention configurable)")
    guard("OnCalendar=" in timer and "UTC" in timer, "le minuteur n'est pas planifie en UTC")
    guard(
        "Unit=address-validator-retention.service" in timer,
        "le minuteur ne cible pas le service de purge",
    )
    guard(
        "Persistent=true" in timer,
        "sans Persistent=true, une purge manquee (VPS eteint) ne serait jamais rattrapee",
    )
    for fichier in ("address-validator-retention.service", "address-validator-retention.timer"):
        brut = (ROOT / fichier).read_bytes()
        guard(b"\r" not in brut, f"{fichier} contient des fins de ligne Windows (systemd les refuse)")
        guard(brut.endswith(b"\n"), f"{fichier} ne se termine pas par un saut de ligne")

    # --- 4. Le rapport quotidien et la porte D30 restent coherents --------
    rapport = read("scripts/daily_report.sh")
    guard(
        "DISCOVERY_LOG_RETENTION_DAYS" in rapport or "prune" in rapport,
        "le rapport quotidien ne tient plus compte de la retention",
    )
    guard("D39" in read("DECISIONS.md"), "la decision D39 n'est plus consignee dans DECISIONS.md")


def d40() -> None:
    """Un paiement encaisse se distingue d'un essai de recette, et c'est publie."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import create_engine, text

    from app.routes.revenue import _is_test, aggregate, revenue_stats

    # --- 1. L'agregation separe le reel du registre ------------------------
    moment = datetime(2026, 10, 11, 7, 0, tzinfo=timezone.utc)
    lignes = [
        {
            "tx_hash": "0xclient",
            "amount": 0.02,
            "address": "cle-client",
            "created_at": (moment - timedelta(hours=1)).isoformat(),
            "test_mode": False,
        },
        {
            "tx_hash": "0xessai",
            "amount": 0.02,
            "address": "cle-recette",
            "created_at": (moment - timedelta(hours=2)).isoformat(),
            "test_mode": True,
        },
        {
            # Ligne anterieure a la migration : ni marquee, ni recette.
            "tx_hash": "0xancienne",
            "amount": 0.02,
            "address": "cle-ancienne",
            "created_at": (moment - timedelta(hours=3)).isoformat(),
        },
    ]
    stats = aggregate(lignes, moment)
    guard(
        stats["real_validations_paid"] == 2 and stats["real_revenue_usdc"] == 0.04,
        f"revenu reel faux : {stats['real_validations_paid']} / {stats['real_revenue_usdc']}",
    )
    guard(
        stats["total_validations_paid"] == 3 and stats["total_revenue_usdc"] == 0.06,
        f"registre entier faux : {stats['total_validations_paid']} / {stats['total_revenue_usdc']}",
    )
    guard(
        stats["test_validations_paid"] == 1 and stats["test_revenue_usdc"] == 0.02,
        f"recette non chiffree : {stats['test_validations_paid']} / {stats['test_revenue_usdc']}",
    )
    guard(
        stats["validations_paid"] == stats["real_validations_paid"]
        and stats["revenue_usdc"] == stats["real_revenue_usdc"],
        "les alias D31 ne pointent plus sur le reel : un lecteur qui ignore D40 lirait un chiffre fictif",
    )
    guard(
        stats["windows"]["24h"]["real_revenue_usdc"] == 0.04
        and stats["windows"]["24h"]["test_revenue_usdc"] == 0.02,
        "les fenetres ne portent plus la distinction recette / reel",
    )
    guard(
        _is_test({"test_mode": "true"}) and _is_test({"test_mode": 1}),
        "la lecture tolerante de test_mode a disparu (bool / int / texte)",
    )
    guard(
        not _is_test({}) and not _is_test({"test_mode": None}),
        "une ligne sans marque est classee en recette : la migration ne doit marquer personne a la place de l'exploitant",
    )

    # --- 2. La marque est lue en base, jamais devinee depuis la date -------
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE payment_transactions "
                "(tx_hash TEXT PRIMARY KEY, amount REAL, address TEXT, created_at TEXT, "
                "test_mode BOOLEAN)"
            )
        )
        for ligne in lignes:
            conn.execute(
                text(
                    "INSERT INTO payment_transactions "
                    "(tx_hash, amount, address, created_at, test_mode) "
                    "VALUES (:t, :a, :k, :c, :m)"
                ),
                {
                    "t": ligne["tx_hash"],
                    "a": ligne["amount"],
                    "k": ligne["address"],
                    "c": ligne["created_at"],
                    "m": ligne.get("test_mode"),
                },
            )
    charge = revenue_stats(engine=engine, now=moment, include_recent=True)
    guard(
        charge["ledger"]["available"] is True,
        f"une base joignable est annoncee indisponible : {charge['ledger']['error']}",
    )
    guard(
        charge["ledger"]["real_revenue_usdc"] == 0.04
        and charge["ledger"]["total_revenue_usdc"] == 0.06
        and charge["ledger"]["test_validations_paid"] == 1,
        f"la lecture SQL ne separe plus recette et reel : {charge['ledger']}",
    )
    marques = {ligne["tx_hash"]: ligne["test_mode"] for ligne in charge["recent"]}
    guard(
        marques.get("0xessai") is True and marques.get("0xclient") is False,
        f"la marque de recette n'est plus publiee ligne a ligne : {marques}",
    )
    guard(
        "recording_now" in charge["test_mode"] and "cutoff_utc" in charge["test_mode"],
        "la reponse ne dit plus si la recette est en cours ni depuis quel seuil",
    )

    # --- 3. Le schema et le code portent tous les deux la colonne ----------
    ddl = read("app/database.py")
    guard(
        "test_mode BOOLEAN NOT NULL DEFAULT FALSE" in ddl,
        "le DDL ne cree pas la colonne test_mode : une base neuve serait incomplete",
    )
    guard(
        "ADD COLUMN IF NOT EXISTS test_mode" in ddl,
        "aucune migration idempotente : une base deja en service n'aurait pas la colonne",
    )
    guard("test_mode" in read("app/models.py"), "l'ORM et le DDL ne decrivent plus la meme table (D13)")
    guard("test_mode" in read("app/payment.py"), "record_transaction n'enregistre plus la marque de recette")
    guard(
        "real_revenue_usdc" in read("app/routes/revenue.py"),
        "l'endpoint ne publie plus le revenu reel",
    )
    guard(
        not re.search(r"^\s*test_mode: bool = True", read("app/config.py"), re.M),
        "TEST_MODE est actif par defaut : une production compterait ses essais comme du revenu",
    )
    guard(
        "TEST_MODE_CUTOFF_UTC" in read(".env.example")
        and "test_mode_cutoff_utc" in read("app/config.py"),
        "le seuil de rattrapage n'est documente ni dans .env.example ni dans la configuration",
    )
    guard(
        (ROOT / "scripts" / "migrate_test_mode.py").is_file(),
        "le script de rattrapage a disparu : les lignes deja ecrites ne pourraient plus etre marquees",
    )
    guard("D40" in read("DECISIONS.md"), "la decision D40 n'est plus consignee dans DECISIONS.md")


CHECKS = {f"D{index}": globals()[f"d{index}"] for index in range(1, 41)}


def catalog() -> list[tuple[str, str]]:
    """Liste (identifiant, titre) triee par numero de decision."""
    return [(name, (CHECKS[name].__doc__ or "").strip()) for name in sorted(CHECKS, key=lambda n: int(n[1:]))]


def run(name: str) -> bool:
    """Execute un controle, imprime sa ligne de verdict, renvoie True si vert."""
    try:
        CHECKS[name]()
    except CheckFailed as exc:
        print(f"DECISION FAIL {name}: {exc}")
        return False
    except Exception as exc:  # noqa: BLE001 - tout plantage est une porte rouge
        print(f"DECISION FAIL {name}: {type(exc).__name__}: {exc}")
        return False
    print(f"DECISION OK {name}")
    return True


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    if argv[0] in ("-h", "--help", "--list"):
        print(__doc__.strip())
        if argv[0] == "--list":
            for name, title in catalog():
                print(f"{name}\t{title}")
        return 0
    if argv[0] == "--all":
        names = [name for name, _ in catalog()]
    else:
        names = [arg.upper() for arg in argv]
        unknown = [name for name in names if name not in CHECKS]
        if unknown:
            print(f"DECISION UNKNOWN: {' '.join(unknown)}", file=sys.stderr)
            print(f"identifiants valides : {', '.join(name for name, _ in catalog())}", file=sys.stderr)
            return 2
    results = [run(name) for name in names]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

