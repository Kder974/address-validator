#!/usr/bin/env python
"""Oracle mecanique des decisions D1..D27, adosse au ledger GATES.md.

Les decisions de `DECISIONS.md` n'etaient verifiables que par lecture humaine :
chaque ligne du ledger demandait au relecteur de comparer deux fichiers a l'oeil.
Ce script transforme chaque decision en **oracle executable** : il observe le
depot (pins, DDL, shebang) *et* le comportement reel du code (HMAC de la preuve
de paiement, disjoncteur sous 8 threads, cooldown du cache Redis, /health borne,
ORM compare au DDL, degradation du geocodeur).

    .venv\\Scripts\\python.exe scripts\\decisions_check.py D7      # une decision
    .venv\\Scripts\\python.exe scripts\\decisions_check.py --all   # les 26
    .venv\\Scripts\\python.exe scripts\\decisions_check.py --list

Sortie (ASCII, une ligne par porte) :

    DECISION OK D7
    DECISION FAIL D7: <raison>          (code 1)

Codes de retour : 0 vert, 1 porte rouge, 2 usage ou identifiant inconnu.

Aucun acces reseau n'est effectue : les seules I/O sortantes sont les sondes
bornees du chemin degrade (init_db au demarrage, /health) qui sont justement ce
que D14/D16/D22 mesurent.
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
    """Le paiement est une preuve a secret partage, pas une verification on-chain."""
    source = app_source()
    for token in ("transferWithAuthorization", "import web3", "from web3", "eth_account", "eth_utils"):
        guard(token not in source, f"du code on-chain est apparu sans decision : {token}")
    from app.config import get_settings
    from app.payment import build_payment_proof, verify_payment, PROOF_HEADER

    secret = get_settings().api_secret_key
    proof = build_payment_proof("0xMADEUP")
    guard(
        verify_payment(fake_request({PROOF_HEADER: proof})) is True,
        "une preuve signee par le secret partage est refusee : la limite D15 n'est plus celle documentee",
    )
    guard(secret != "", "aucun secret partage : la preuve n'a plus de cle")
    guard("D15" in read("DECISIONS.md"), "la limite D15 n'est plus consignee dans DECISIONS.md")
    guard(
        "mise à niveau" in read("README.md"),
        "README.md ne decrit plus le chemin de mise a niveau vers un facilitateur x402",
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


CHECKS = {f"D{index}": globals()[f"d{index}"] for index in range(1, 28)}


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

