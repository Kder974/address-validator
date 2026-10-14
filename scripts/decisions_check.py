#!/usr/bin/env python
"""Oracle mecanique des decisions D1..D63, adosse au ledger GATES.md.

Les decisions de `DECISIONS.md` n'etaient verifiables que par lecture humaine :
chaque ligne du ledger demandait au relecteur de comparer deux fichiers a l'oeil.
Ce script transforme chaque decision en **oracle executable** : il observe le
depot (pins, DDL, shebang) *et* le comportement reel du code (HMAC de la preuve
de paiement, disjoncteur sous 8 threads, cooldown du cache Redis, /health borne,
ORM compare au DDL, degradation du geocodeur, signature EIP-712 x402).

    .venv\\Scripts\\python.exe scripts\\decisions_check.py D7      # une decision
    .venv\\Scripts\\python.exe scripts\\decisions_check.py --all   # les 63
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

Le durcissement W1 (12/10/2026, `AUDIT_TRAFIC_VPS.md` §5) est porte par la porte
D27 : la confirmation on-chain est **active par defaut** (le defaut `false`
etait un fail-open), et hors recette declaree la preuve a secret partage est
refusee — meme quand `X402_ONCHAIN_VERIFY=false`. Aucune porte ne suppose
l'ambiance : celles qui mesurent la voie heritee (D7, D15, D29, D34) declarent
leur regime avec `mode_recette()`, les autres mesurent la production.

Les durcissements W2 et W3 sont portes par les portes D41 et D42 : W2 refuse de
servir un paiement dont l'inscription au registre echoue (D41), et W3 mesure la
porte de paiement **au niveau de la route** (`POST /validate`) dans le regime du
VPS, la ou le client la rencontre (D42) — `tests/test_onchain.py` mesure la
couche, ce qui ne dit rien du contrat rendu.

La porte D43 couvre la premiere page **commerciale** (`/pricing`, campagne du
12/10/2026) : elle est publique, indexable et cachable, elle reste autonome
comme `/dashboard` (meme feuille de style, importee depuis `app/web.py`), les
prix unitaires affiches sont calcules depuis le plan, aucun secret de
configuration n'y apparait, et les temoignages d'exemple ne sont pas publies par
defaut — publier des retours inventes comme reels serait une pratique
commerciale trompeuse.

La porte D44 couvre l'**inscription self-service** (`/signup`, `/compte`) : un
mot de passe faible est refuse avant toute ecriture, la cle API est affichee une
seule fois et n'est stockee que sous forme d'empreinte, le mot de passe est une
empreinte bcrypt (jamais lisible), l'espace client est ferme par defaut et
n'affiche jamais la cle complete, les statistiques viennent des validations
reellement servies, regenerer la cle invalide l'ancienne, le plafond d'IP est
applique et annonce, et une base injoignable donne un `503` explicite.
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
from contextlib import contextmanager
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


@contextmanager
def mode_recette(actif: bool = True):
    """Place un controle dans le regime demande : recette declaree, ou production.

    Depuis le durcissement W1, la preuve a secret partage (`X-Payment-Proof`) du
    chemin herite D15 n'est servie **que** dans une recette declaree
    (`TEST_MODE=true`, `app/payment.py`) : hors recette, seule une confirmation
    on-chain ouvre un `200`. Les portes qui mesurent cette voie (D7, D15, D29,
    D34) doivent donc dire explicitement dans quel regime elles se placent — sans
    quoi elles mesureraient un refus de production en croyant mesurer une
    acceptation. `actif=False` place la porte en production **meme si** le shell
    ou le `.env` a herite d'un `TEST_MODE=true` : aucune porte ne depend de
    l'ambiance, et l'etat precedent est toujours restaure.
    """
    from app.config import get_settings

    settings = get_settings()
    avant = settings.test_mode
    settings.test_mode = bool(actif)
    try:
        yield settings
    finally:
        settings.test_mode = avant


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

    # W1 : la voie a secret partage (D15) ne se mesure qu'en recette declaree.
    # La porte lit donc les deux regimes — sinon elle ne mesurerait que
    # l'ambiance du shell, pas la decision.
    with mode_recette(True):
        guard(
            verify_payment(fake_request({PROOF_HEADER: build_payment_proof("0xabc")})) is True,
            "une preuve fraiche et bien signee est refusee en recette declaree",
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

    from app import onchain

    with mode_recette(False), mock.patch.object(onchain, "_rpc", lambda method, params: None):
        guard(
            verify_payment(fake_request({PROOF_HEADER: build_payment_proof("0xabc")})) is False,
            "hors recette, une preuve a secret partage est servie : W1 est casse",
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
    """La preuve partagee n'est servie qu'en recette ; la surface on-chain est bornee."""
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
    # W1 : cette voie de compatibilite ne s'ouvre qu'en recette declaree. La
    # porte mesure donc les deux regimes, au lieu de lire l'ambiance du shell.
    with mode_recette(True):
        guard(
            verify_payment(fake_request({PROOF_HEADER: proof})) is True,
            "une preuve signee par le secret partage est refusee : la voie de compatibilite D15 est cassee",
        )
    with mode_recette(False):
        guard(
            verify_payment(fake_request({PROOF_HEADER: proof})) is False,
            "hors recette, le secret partage ouvrirait encore la porte : W1 est casse",
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
    """L'anti-abus reste au proxy ; les plafonds **produit** vivent dans l'app.

    Révision datée du 12/10/2026 (voir D44 et « Notes de provenance » dans
    `DECISIONS.md`). D21 interdisait *toute* limitation de débit applicative :
    elle renvoyait à Nginx (`limit_req`), qui refuse sans rien expliquer. Deux
    plafonds **produit** — 5 inscriptions par IP et par heure, 10 essais gratuits
    par IP et par 24 h — doivent au contraire **parler** au client (`429` +
    `Retry-After`, « créez un compte pour continuer »), ce que `limit_req` ne
    sait pas faire. La porte garde donc l'interdiction qui compte vraiment :
    aucune bibliothèque de limitation tierce, et un **seul** module maison dont
    l'usage est borné aux deux réglages documentés — tout autre plafond ajouté
    ailleurs fait rougir la porte.
    """
    for token in ("slowapi", "Limiter(", "RateLimitMiddleware"):
        guard(
            token not in app_source(),
            f"bibliotheque de limitation de debit ajoutee dans l'application : {token}",
        )
    guard(
        "slowapi" not in (read("requirements.txt") + read("requirements-dev.txt")).lower(),
        "slowapi ajoute aux dependances",
    )

    maison = APP_DIR / "ratelimit.py"
    guard(
        maison.is_file(),
        "les plafonds produit n'ont plus de module dedie (app/ratelimit.py, D44)",
    )
    toleres = ("signup_rate_limit_per_hour", "validate_form_rate_limit_per_day")
    for fichier in app_py_files():
        if fichier.name == "ratelimit.py":
            continue
        for numero, ligne in enumerate(
            read(str(fichier.relative_to(ROOT))).splitlines(), 1
        ):
            if "rate_limit" in ligne:
                guard(
                    any(token in ligne for token in toleres),
                    f"plafond applicatif non documente : {fichier.name}:{numero}",
                )

    security = fold(read("SECURITY.md"))
    guard("rate limiting par ip" in security, "SECURITY.md ne traite plus la limitation par IP")
    guard(
        "limit_req" in security and "nginx" in security,
        "SECURITY.md ne nomme plus le proxy responsable de l'anti-abus",
    )
    guard(
        "app/ratelimit.py" in security,
        "SECURITY.md ne dit plus ou vivent les plafonds produit (D44)",
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
    """La confirmation on-chain est active par defaut, fail-closed et sans web3."""
    from app import onchain
    from app.config import Settings, get_settings
    from app.payment import (
        PROOF_HEADER,
        build_payment_proof,
        payment_result,
        verify_payment,
    )

    # --- 1. Le defaut est fail-closed (W1) ---------------------------------
    # Le defaut `false` etait un fail-open : un deploiement qui omettait la
    # variable servait le service contre une preuve synthetique
    # (`AUDIT_TRAFIC_VPS.md` §5). La porte echoue si le defaut repart en arriere.
    guard(
        Settings.model_fields["x402_onchain_verify"].default is True,
        "la verification on-chain n'est plus active par defaut : un deploiement qui "
        "omet la variable servirait le service contre une preuve synthetique (fail-open W1)",
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
    real_mode = settings.test_mode
    calls: list[str] = []
    try:
        settings.cdp_wallet_address = wallet
        # Production : la recette n'est pas declaree, la chaine est la seule
        # autorite (W1). Le regime est force plutot qu'herite de l'ambiance.
        settings.test_mode = False
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

        # --- 2. Hors recette, couper la couche ne rouvre pas la porte (W1) --
        # C'est exactement le fail-open corrige : des que la verification etait
        # desactivee, le service repondait sur une preuve synthetique. La porte
        # mesure l'inverse, et verifie qu'aucun appel reseau n'est fait.
        appels_avant = len(calls)
        settings.x402_onchain_verify = False
        refus = payment_result(fake_request({PROOF_HEADER: build_payment_proof("0xMADEUP")}))
        guard(
            refus.paid is False and "hors recette" in refus.reason,
            "hors recette, couper la verification on-chain rouvre la porte a une preuve "
            f"synthetique : {refus.reason}",
        )
        guard(
            len(calls) == appels_avant,
            "une couche coupee interroge quand meme la chaine",
        )

        # --- 3. La recette declaree rouvre la voie de compatibilite (D15) ---
        with mode_recette(True):
            guard(
                payment_result(
                    fake_request({PROOF_HEADER: build_payment_proof("0xMADEUP")})
                ).paid
                is True,
                "la recette declaree ne sert plus la preuve a secret partage : D15 est casse",
            )
    finally:
        settings.cdp_wallet_address = real_wallet
        settings.x402_onchain_verify = real_flag
        settings.test_mode = real_mode

    guard("D27" in read("DECISIONS.md"), "la decision D27 n'est plus consignee dans DECISIONS.md")
    guard(
        re.search(r"X402_ONCHAIN_VERIFY=true", read(".env.example")) is not None,
        "X402_ONCHAIN_VERIFY n'est plus active (true) dans .env.example : un deploiement "
        "neuf serait fail-open (W1)",
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
        # W1 : la voie a secret partage ne s'ouvre qu'en recette declaree ; la
        # porte declare donc son regime au lieu de lire l'ambiance du shell.
        mode_recette(True),
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

    def _enregistre(tx: str, amount: float, key: str) -> bool:
        ecritures.append(tx)
        return True  # D41 : le service n'est rendu que si la ligne est ecrite

    facilitateur = fake_facilitator()
    reset_nonce_cache()
    with (
        mock.patch("app.x402._post_facilitator", facilitateur),
        mock.patch("app.routes.validate.validate_full", lambda raw: dict(ORACLE_RESULT)),
        mock.patch("app.routes.validate.get_cached_result", lambda key: None),
        mock.patch("app.routes.validate.store_in_cache", lambda key, value: None),
        mock.patch("app.routes.validate.record_transaction", _enregistre),
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
    # W1 : cette voie suppose une recette declaree ; en production, la meme
    # preuve est refusee faute d'encaissement verifie. La porte lit les deux.
    from app import onchain

    with (
        mode_recette(True),
        mock.patch("app.routes.validate.validate_full", lambda raw: dict(ORACLE_RESULT)),
        mock.patch("app.routes.validate.record_transaction", _enregistre),
    ):
        heritage = client.post(
            "/validate", json=corps, headers={PROOF_HEADER: build_payment_proof("0xORACLE34")}
        )
    guard(heritage.status_code == 200, "la preuve HMAC historique ne sert plus en recette")
    guard(
        PAYMENT_RESPONSE_HEADER not in heritage.headers,
        "un paiement HMAC est annonce comme un reglement x402",
    )
    # Hors recette : la chaine est la seule autorite. Le RPC est muet (aucun
    # octet ne sort), donc la meme preuve valide ne doit plus ouvrir de 200.
    # Le hash est bien forme : le refus doit venir de l'absence d'encaissement,
    # pas d'un controle de forme qui masquerait le vrai motif.
    avec_hash = "0x" + "ef" * 32

    with (
        mode_recette(False),
        mock.patch.object(onchain, "_rpc", lambda method, params: None),
        mock.patch("app.routes.validate.validate_full", lambda raw: dict(ORACLE_RESULT)),
        mock.patch("app.routes.validate.record_transaction", _enregistre),
    ):
        avant_refus = len(ecritures)
        refus_heritage = client.post(
            "/validate", json=corps, headers={PROOF_HEADER: build_payment_proof(avec_hash)}
        )
    guard(
        refus_heritage.status_code == 402,
        "hors recette, une preuve a secret partage ouvre encore la porte : W1 est casse",
    )
    guard(
        len(ecritures) == avant_refus,
        "un paiement refuse hors recette a quand meme alimente le registre : un encaissement non verifie serait compte",
    )
    guard(
        "inconnue" in refus_heritage.json().get("error", ""),
        f"le refus ne dit pas pourquoi : {refus_heritage.json().get('error')!r}",
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

    def _enregistre(tx: str, amount: float, key: str) -> bool:
        ecritures.append(tx)
        return True  # D41 : l'outil n'est rendu que si la ligne est ecrite

    x402.reset_nonce_cache()
    with (
        mock.patch("app.x402._post_facilitator", fake_facilitator()),
        mock.patch("app.routes.mcp.perform_validation", lambda raw: (dict(ORACLE_RESULT), "cle-oracle")),
        mock.patch("app.routes.mcp.record_transaction", _enregistre),
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


def d41() -> None:
    """Un encaissement non inscrit au registre n'est pas servi, et l'incident est compte."""
    from fastapi.testclient import TestClient

    from app import x402
    from app.main import app
    from app.payment import PROOF_HEADER, build_payment_proof
    from app.routes.metrics import metrics

    client = TestClient(app)
    arguments = {
        "address": {"street": "1 A St", "city": "X", "state": "CA", "zip": "90001"}
    }
    reglement = "0x" + "ef" * 32

    # --- 1. Statique : le compteur existe, et les deux chemins paient la regle -
    guard(
        "ledger_write_failures_total" in read("app/routes/metrics.py"),
        "le compteur d'echecs d'ecriture n'est pas declare : un incident invisible est indiscernable d'une absence de trafic",
    )
    for chemin in ("app/routes/validate.py", "app/routes/mcp.py"):
        source = read(chemin)
        guard(
            re.search(r"not\s+record_transaction", source) is not None,
            f"{chemin} ignore le resultat de l'ecriture : le service serait rendu sans ligne au registre, donc encaisse sans trace",
        )
        guard(
            '"ledger_write_failures_total"' in source
            and re.search(r"status(_code)?=500", source) is not None,
            f"{chemin} ne refuse pas de servir (500) ou ne compte pas l'echec",
        )
    guard("D41" in read("DECISIONS.md"), "la decision D41 n'est pas consignee dans DECISIONS.md")
    guard("D41" in read("GATES.md"), "la porte D41 n'est pas consignee dans GATES.md")

    # --- 2. HTTP : paiement accepte, ecriture refusee -> 500, rien n'est servi
    # Les patchs sont poses dans l'ordre : `offline_route()` accepte l'ecriture,
    # le patch suivant la refuse (le dernier pose gagne).
    with (
        mode_recette(True),
        offline_route(),
        mock.patch("app.routes.validate.record_transaction", lambda tx, amount, key: False),
    ):
        avant = metrics.snapshot()["ledger_write_failures_total"]
        refus = client.post(
            "/validate",
            json=arguments,
            headers={PROOF_HEADER: build_payment_proof(reglement)},
        )
        apres = metrics.snapshot()["ledger_write_failures_total"]
    guard(
        refus.status_code == 500,
        f"un paiement non inscrit au registre est servi en HTTP {refus.status_code}",
    )
    corps = refus.json()
    guard("result" not in corps, "une validation non inscrite au registre est quand meme rendue")
    guard(
        corps["transaction_id"] == reglement,
        "l'encaissement a rapprocher n'est pas identifiable dans la reponse",
    )
    guard(
        apres == avant + 1,
        f"le compteur d'echecs d'ecriture n'a pas bouge ({avant} -> {apres})",
    )

    # --- 3. Le compteur est publie, donc observable hors du processus ---------
    guard(
        "ledger_write_failures_total" in client.get("/metrics").text,
        "/metrics n'expose pas le compteur d'echecs d'ecriture",
    )

    # --- 4. MCP : l'outil est le meme service, donc la meme regle ------------
    x402.reset_nonce_cache()
    with (
        mock.patch("app.x402._post_facilitator", fake_facilitator()),
        mock.patch(
            "app.routes.mcp.perform_validation",
            lambda raw: (dict(ORACLE_RESULT), "cle-oracle"),
        ),
        mock.patch("app.routes.mcp.record_transaction", lambda tx, amount, key: False),
    ):
        avant = metrics.snapshot()["ledger_write_failures_total"]
        outil = client.post(
            "/mcp",
            headers={x402.PAYMENT_HEADER: x402_payload()},
            json={
                "jsonrpc": "2.0",
                "id": 41,
                "method": "tools/call",
                "params": {"name": "validate_address", "arguments": arguments},
            },
        )
        apres = metrics.snapshot()["ledger_write_failures_total"]
    x402.reset_nonce_cache()
    guard(
        outil.status_code == 500,
        f"l'outil sert un encaissement non inscrit au registre (HTTP {outil.status_code})",
    )
    erreur = outil.json()["error"]
    guard(erreur["code"] == -32603, f"code JSON-RPC inattendu : {erreur['code']}")
    guard("result" not in outil.json(), "l'outil rend un resultat sans ligne au registre")
    guard(
        erreur["data"]["transaction"] == ORACLE_TX,
        "l'outil ne nomme pas le reglement a rapprocher",
    )
    guard(apres == avant + 1, "l'echec d'ecriture de l'outil n'est pas compte")

    # --- 5. Non-regression : une ecriture reussie sert toujours la validation -
    with mode_recette(True), offline_route():
        sert = client.post(
            "/validate",
            json=arguments,
            headers={PROOF_HEADER: build_payment_proof("0x" + "ee" * 32)},
        )
    guard(
        sert.status_code == 200 and "result" in sert.json(),
        f"une ecriture reussie ne sert plus la validation (HTTP {sert.status_code})",
    )


def d42() -> None:
    """La porte de paiement est mesuree a la route, dans le regime du VPS.

    W1 a rendu le `200` dependant d'un encaissement confirme par la chaine, et
    W2 (D41) d'une ligne au registre. Restait a mesurer ces deux regles **la ou
    le client les rencontre** : `tests/test_onchain.py` mesure la couche et
    `tests/test_payment.py` la forme de la preuve, aucune mesure ne portait sur
    le contrat rendu par `POST /validate` dans le regime du VPS (`TEST_MODE`
    absent) — celui ou l'audit du trafic a observe des `200` sans ligne au
    registre (`AUDIT_TRAFIC_VPS.md` §5.1).
    """
    import subprocess

    from fastapi.testclient import TestClient

    from app import onchain
    from app.config import get_settings
    from app.main import app
    from app.payment import PROOF_HEADER, build_payment_proof

    # --- 1. La mesure existe, elle interroge la route, et elle passe ---------
    mesure = "tests/test_onchain_payment_route.py"
    source = read(mesure)
    for attendu in ("from app.main import app", "TestClient", "test_mode"):
        guard(attendu in source, f"{mesure} n'observe plus {attendu!r}")
    sortie = subprocess.run(
        [sys.executable, "-m", "pytest", mesure, "-q"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        # Le regime de la suite ne doit pas dependre de l'ambiance du shell : la
        # mesure de route declare elle-meme les deux regimes qu'elle compare.
        env={**os.environ, "TEST_MODE": "true"},
        timeout=120,
    )
    guard(
        sortie.returncode == 0,
        "la mesure de route ne passe pas "
        f"({sortie.returncode}) : {(sortie.stdout + sortie.stderr).strip()[-300:]}",
    )

    # --- 2. Le point ouvert de l'audit est clos dans le document -------------
    audit = fold(read("AUDIT_TRAFIC_VPS.md"))
    guard("d42" in audit, "AUDIT_TRAFIC_VPS.md ne renvoie pas a la porte D42")
    guard(
        "a confirmer ou infirmer" not in audit,
        "l'audit garde un point ouvert que W1/W2 ont tranche",
    )
    guard("D42" in read("DECISIONS.md"), "la decision D42 n'est pas consignee dans DECISIONS.md")
    guard("D42" in read("GATES.md"), "la porte D42 n'est pas consignee dans GATES.md")

    # --- 3. Le contrat rendu : hors recette, la chaine decide ----------------
    settings = get_settings()
    client = TestClient(app)
    arguments = {"address": {"street": "1 A St", "city": "X", "state": "CA", "zip": "90001"}}
    wallet = "0x" + "aa" * 20
    tx = "0x" + "ab" * 32
    ecrites: list[tuple] = []
    appels: list[str] = []

    def _ecrit(tx_hash: str, montant: float, cle: str) -> bool:
        ecrites.append((tx_hash, montant, cle))
        return True

    def _recu(unites: int) -> dict:
        return {
            "status": "0x1",
            "blockNumber": hex(0x1000),
            "logs": [
                {
                    "address": settings.x402_usdc_contract,
                    "topics": [
                        onchain.TRANSFER_TOPIC,
                        "0x" + "00" * 12 + "22" * 20,
                        "0x" + "00" * 12 + wallet[2:],
                    ],
                    "data": "0x" + format(unites, "064x"),
                }
            ],
        }

    def _rpc(unites: int | None):
        def appel(method: str, params: list) -> object:
            appels.append(method)
            if method == "eth_getTransactionReceipt":
                return None if unites is None else _recu(unites)
            return hex(0x1005)

        return appel

    def _panne(method: str, params: list) -> object:
        raise RuntimeError("RPC injoignable")

    wallet_avant = settings.cdp_wallet_address
    flag_avant = settings.x402_onchain_verify
    regime_avant = settings.test_mode
    settings.cdp_wallet_address = wallet
    settings.x402_onchain_verify = True
    settings.test_mode = False
    try:
        with offline_route(), mock.patch("app.routes.validate.record_transaction", _ecrit):
            # 3a. Preuve signee, transaction inconnue de la chaine : 402, rien d'ecrit.
            with mock.patch("app.onchain._rpc", _rpc(None)):
                refus = client.post(
                    "/validate", json=arguments, headers={PROOF_HEADER: build_payment_proof(tx)}
                )
            guard(refus.status_code == 402, f"une tx inconnue est servie en HTTP {refus.status_code}")
            guard(not ecrites, "un refus de paiement a ecrit une ligne au registre (D34)")

            # 3b. Transfert USDC confirme : 200, et la ligne porte le hash confirme.
            with mock.patch("app.onchain._rpc", _rpc(onchain.usdc_units(settings.x402_price))):
                servie = client.post(
                    "/validate", json=arguments, headers={PROOF_HEADER: build_payment_proof(tx)}
                )
            guard(
                servie.status_code == 200 and "result" in servie.json(),
                f"un transfert confirme ne sert plus la validation (HTTP {servie.status_code})",
            )
            guard(
                servie.json()["price_paid"] == settings.x402_price,
                "le prix facture n'est plus le prix annonce",
            )
            guard(
                len(ecrites) == 1 and ecrites[0][0] == tx,
                f"le registre n'a pas recu le hash confirme : {ecrites}",
            )

            # 3c. Chaine injoignable : fail-closed, et toujours rien d'ecrit.
            with mock.patch("app.onchain._rpc", _panne):
                en_panne = client.post(
                    "/validate", json=arguments, headers={PROOF_HEADER: build_payment_proof(tx)}
                )
            guard(en_panne.status_code == 402, f"la panne RPC sert en HTTP {en_panne.status_code}")
            guard(len(ecrites) == 1, "une panne RPC a ecrit une ligne au registre")

            # 3d. Recette declaree : servie et journalisee, chaine jamais interrogee.
            settings.test_mode = True
            appels_avant = len(appels)
            with mock.patch("app.onchain._rpc", _panne):
                recette = client.post(
                    "/validate", json=arguments, headers={PROOF_HEADER: build_payment_proof(tx)}
                )
            guard(
                recette.status_code == 200,
                "la recette declaree ne sert plus la preuve a secret partage "
                f"(HTTP {recette.status_code})",
            )
            guard(len(appels) == appels_avant, "la recette declaree a interroge la chaine")
            guard(len(ecrites) == 2, "la recette declaree n'a rien inscrit au registre (D40)")
    finally:
        settings.cdp_wallet_address = wallet_avant
        settings.x402_onchain_verify = flag_avant
        settings.test_mode = regime_avant


def d43() -> None:
    """La page publique /pricing vend sans mentir et sans ressource externe."""
    from fastapi.testclient import TestClient

    from app import accounts, i18n
    from app.config import get_settings
    from app.main import app
    from app.routes.pricing import OFFRES
    from app.routes.pricing import page as page_tarifs

    settings = get_settings()
    client = TestClient(app)

    # 1. Publique : servie sans compte, indexable et cachable par le proxy.
    reponse = client.get("/pricing")
    guard(reponse.status_code == 200, f"/pricing repond {reponse.status_code} : la page n'est pas publique")
    guard(
        reponse.headers.get("content-type", "").startswith("text/html"),
        f"type de contenu inattendu : {reponse.headers.get('content-type')!r}",
    )
    guard(
        reponse.headers.get("cache-control") == "public, max-age=300",
        "une page commerciale n'est pas cachable par le proxy",
    )
    guard(
        reponse.headers.get("x-content-type-options") == "nosniff",
        "l'en-tete nosniff a disparu de la page publique",
    )
    csp = reponse.headers.get("content-security-policy", "")
    guard("form-action 'none'" in csp, f"/pricing accepte de poster ailleurs : {csp!r}")
    guard(
        'name="robots" content="index, follow"' in reponse.text,
        "une page commerciale non indexable ne sert a rien",
    )
    guard(client.get("/").json().get("pricing") == "/pricing", "/ ne pointe plus vers /pricing")

    # 2. Autonome, comme /dashboard (D32) : rien a charger, rien a executer. Depuis
    #    le dessin valide (D57), la feuille est celle de la maquette, ecrite dans la
    #    page : les seuls <link> sont les hreflang, aucune feuille ni police.
    page = reponse.text
    guard("<script" not in page.lower(), "la page embarque du script")
    for motif in ("http://", "https://", "//cdn", "@import", "url(http", "<img", "<iframe"):
        guard(motif not in page, f"la page charge une ressource externe ({motif})")
    guard(
        all('rel="alternate"' in lien for lien in re.findall(r"<link\b[^>]*>", page)),
        "/pricing charge une feuille ou une ressource par <link>",
    )
    source_dashboard = read("app/routes/dashboard.py")
    guard(
        "from app.web import DASHBOARD_CSS" in source_dashboard
        and "{DASHBOARD_CSS}" in source_dashboard,
        "dashboard.py ne partage plus la feuille commune (app/web.py)",
    )

    # 3. Ce qui est vendu est ce qui est livre : prix unitaire = prix / quota du
    #    plan, la division affichee a cote. Le texte se lit sans balises (le titre
    #    est decoupe mot a mot pour son animation).
    questions = page.count('<details class="qa"')
    guard(len(OFFRES) == 4 and questions == 5, f"{len(OFFRES)} offres et {questions} questions publiees")
    for offre in OFFRES:
        guard(offre["nom"] in page, f"l'offre {offre['nom']} a disparu de la page")
        guard(offre["prix"] in page, f"le prix de {offre['nom']} a disparu de la page")
        guard(offre["periode"] in page, f"le quota de {offre['nom']} a disparu de la page")
    for plan in ("starter", "pro"):
        unite = i18n.prix("fr", f"unitaire_{plan}")
        division = f"{i18n.prix('fr', plan)} ÷ {i18n.nombre('fr', accounts.PLANS[plan])} validations"
        guard(
            f">{unite}<" in page and division in page,
            f"le prix unitaire calcule de {plan} n'est plus affiche ({unite}, {division})",
        )
    guard("<b>Sur devis</b>" in page, "un plan sans quota fabrique un prix unitaire")
    texte = re.sub(r"<[^>]+>", "", page)
    for attendu in ("Arrêtez de perdre de l'argent", "Commencer l'essai gratuit", "RGPD"):
        guard(attendu in texte, f"la page ne contient plus : {attendu}")

    # 4. Une page indexable ne publie aucun secret.
    for secret in (
        settings.dashboard_password,
        settings.api_secret_key,
        settings.cdp_api_key_secret,
        settings.cdp_wallet_address,
    ):
        guard(
            not secret or secret not in page,
            "un secret de configuration apparait sur une page publique indexable",
        )

    # 5. Temoignages : aucun, dans aucune langue (maquette validee). Des exemples lus
    #    comme des clients reels seraient une pratique commerciale trompeuse ; le
    #    drapeau historique PRICING_SHOW_TESTIMONIALS reste lu, mais sans effet.
    drapeau = settings.pricing_show_testimonials
    try:
        for valeur in (False, True):
            settings.pricing_show_testimonials = valeur
            for lang in i18n.LANGUES:
                guard(
                    'id="temoignages"' not in page_tarifs(settings, lang),
                    f"des temoignages sont publies ({lang}, PRICING_SHOW_TESTIMONIALS={valeur})",
                )
    finally:
        settings.pricing_show_testimonials = drapeau

    # 6. La decision est consignee et le contenu reste echappe.
    guard("D43" in read("DECISIONS.md"), "la decision D43 n'est plus consignee dans DECISIONS.md")
    piege = '<b>"x"</b>@example.org'
    rendu = page_tarifs(settings.model_copy(update={"contact_email": piege}), "fr")
    guard(
        piege not in rendu and "&lt;b&gt;&quot;x&quot;&lt;/b&gt;@example.org" in rendu,
        "le contenu affiche n'est plus echappe",
    )


def d44() -> None:
    """L'inscription et l'espace client protegent ce qu'ils promettent."""
    import base64
    import re
    import tempfile
    from pathlib import Path

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.orm import sessionmaker

    from app import accounts, i18n, ratelimit
    from app.config import get_settings
    from app.main import app
    from app.models import Customer, CustomerValidation
    from app.routes import signup as signup_route

    settings = get_settings()
    # Fichier temporaire et non `sqlite://` en memoire : une base `:memory:` est
    # vide sur *chaque* connexion du pool, donc la table creee par l'oracle
    # n'existerait pas pour la session de la route (mesure faite le 12/10/2026).
    dossier = tempfile.mkdtemp(prefix="porte-d44-")
    moteur = create_engine(f"sqlite:///{Path(dossier) / 'clients.sqlite'}")
    Customer.__table__.create(moteur)
    CustomerValidation.__table__.create(moteur)
    fabrique = sessionmaker(bind=moteur)

    session_avant = signup_route.SessionLocal
    signup_route.SessionLocal = fabrique
    ratelimit.reset()
    client = TestClient(app)

    def auth(email: str, mot_de_passe: str) -> dict:
        jeton = base64.b64encode(f"{email}:{mot_de_passe}".encode()).decode()
        return {"Authorization": f"Basic {jeton}"}

    secret = "porte-d44-mot-de-passe"
    forme = {
        "email": "porte@example.re",
        "password": secret,
        "shop_name": "Boutique Porte",
        "platform": "shopify",
        "plan": "trial",
    }
    try:
        # 1. Le formulaire est public et porte les quatre champs de la spec.
        page = client.get("/signup")
        guard(page.status_code == 200, f"/signup repond {page.status_code}")
        for champ in ("email", "password", "shop_name", "platform"):
            guard(f'name="{champ}"' in page.text, f"le champ {champ} a disparu du formulaire")
        guard(
            "12 caractères minimum" in page.text,
            "la longueur minimale du mot de passe n'est plus annoncee au client",
        )

        # 2. Un mot de passe faible est refuse **avant** toute ecriture.
        faible = {**forme, "password": "court", "email": "faible@example.re"}
        refus = client.post("/signup", data=faible)
        guard(refus.status_code == 400, f"un mot de passe faible obtient {refus.status_code}")
        guard("12 caractères" in refus.text, "le refus ne dit pas pourquoi")
        guard(refus.headers.get("cache-control") == "no-store", "le refus est cachable")

        # 3. Inscription : la cle est montree une fois, seule son empreinte reste.
        reponse = client.post("/signup", data=forme)
        guard(reponse.status_code == 200, f"l'inscription repond {reponse.status_code}")
        guard(
            reponse.headers.get("cache-control") == "no-store",
            "la page qui affiche la cle API est cachable : elle peut finir dans un proxy",
        )
        trouvees = re.findall(r"sk_live_[A-Za-z0-9]{32}", reponse.text)
        guard(trouvees, "aucune cle sk_live_ de 32 caracteres n'est affichee")
        cle = trouvees[0]

        with fabrique() as session:
            compte = accounts.find_by_email(session, forme["email"])
            guard(compte is not None, "le compte n'a pas ete cree")
            guard(
                compte.api_key_hash == accounts.api_key_hash(cle),
                "la base ne contient pas l'empreinte de la cle affichee",
            )
            guard(cle not in compte.api_key_hash, "la cle API est stockee en clair")
            guard(
                compte.password_hash.startswith("$2b$12$"),
                "le mot de passe n'est pas une empreinte bcrypt de cout 12",
            )
            guard(secret not in compte.password_hash, "le mot de passe apparait en clair")
            guard(compte.plan == "trial", f"le plan d'inscription est {compte.plan}")
            guard(compte.trial_ends_at is not None, "la fin de l'essai n'est pas enregistree")
            identifiant, prefixe = compte.id, compte.api_key_prefix

        # 4. Email deja pris : 409 explicite, pas un doublon silencieux.
        doublon = client.post("/signup", data={**forme, "shop_name": "Autre"})
        guard(doublon.status_code == 409, f"un email en double obtient {doublon.status_code}")
        guard("existe déjà" in doublon.text, "le doublon n'est pas explique")

        # 5. /compte ferme par defaut, et n'affiche jamais la cle complete.
        # D62 : sans session, renvoi vers la page de connexion, jamais l'espace lui-meme.
        anonyme = client.get("/compte", follow_redirects=False)
        guard(
            anonyme.status_code == 303 and anonyme.headers.get("location") == "/compte/connexion",
            "l'espace client s'ouvre sans identifiants",
        )
        guard(
            client.get("/compte", headers=auth(forme["email"], "mauvais")).status_code == 401,
            "un mauvais mot de passe ouvre l'espace client",
        )
        espace = client.get("/compte", headers=auth(forme["email"], secret))
        guard(espace.status_code == 200, f"un couple valide est refuse ({espace.status_code})")
        guard(espace.headers.get("cache-control") == "no-store", "l'espace client est cachable")
        guard(cle not in espace.text, "l'espace client reaffiche la cle API")
        guard(prefixe in espace.text, "l'identifiant de la cle n'est plus affiche")
        guard("Aucune facturation" in espace.text, "la facturation vide n'est pas dite")
        # 6. Les statistiques viennent du service rendu, et la formule est montree.
        with fabrique() as session:
            for issue in (True, False, False):
                accounts.record_validation(session, identifiant, "cle-de-cache", issue)
            statut = accounts.plan_status(
                session, accounts.find_by_email(session, forme["email"])
            )
        guard(
            statut["validations"] == 3 and statut["invalides"] == 2,
            f"compteurs faux : {statut['validations']} validations, {statut['invalides']} invalides",
        )
        guard(statut["economies_usd"] == 34.0, f"estimation fausse : {statut['economies_usd']}")
        guard(statut["restant"] == 97, f"reste d'essai faux : {statut['restant']}")
        espace = client.get("/compte", headers=auth(forme["email"], secret)).text
        guard("2 adresses invalides" in espace, "la formule d'estimation n'est plus affichee")
        # D57 : le total s'affiche dans la monnaie de la page (fr : euros), invalides x
        # colis rate de `i18n` ; la base en dollars reste `COUT_ECHEC_USD` (34.0 ci-dessus).
        guard(
            f">{2 * i18n.MONTANTS['EUR']['colis_rate']} €<" in espace,
            "le total d'economies est faux ou a disparu",
        )

        # 7. Regeneration : la nouvelle cle remplace l'ancienne immediatement.
        nouvelle_page = client.post("/compte/cle", headers=auth(forme["email"], secret))
        guard(
            nouvelle_page.status_code == 200,
            f"la regeneration repond {nouvelle_page.status_code}",
        )
        nouvelle = re.findall(r"sk_live_[A-Za-z0-9]{32}", nouvelle_page.text)[0]
        guard(nouvelle != cle, "la regeneration renvoie la meme cle")
        with fabrique() as session:
            guard(
                accounts.find_by_api_key(session, cle) is None,
                "l'ancienne cle fonctionne encore apres regeneration",
            )
            guard(
                accounts.find_by_api_key(session, nouvelle) is not None,
                "la nouvelle cle ne trouve plus le compte",
            )

        # 8. Plafond par IP : le 6e essai est refuse, et annonce.
        ratelimit.reset()
        limite = settings.signup_rate_limit_per_hour
        for index in range(limite):
            accepte = client.post(
                "/signup", data={**forme, "email": f"porte{index}@example.re"}
            )
            guard(
                accepte.status_code == 200,
                f"l'inscription {index} du quota obtient {accepte.status_code}",
            )
        bloque = client.post("/signup", data={**forme, "email": "porte-trop@example.re"})
        guard(
            bloque.status_code == 429,
            f"l'inscription {limite + 1} obtient {bloque.status_code} au lieu de 429",
        )
        guard(
            bloque.headers.get("retry-after") == "3600",
            "le client refuse ne sait pas quand reessayer",
        )
        guard(ratelimit.fallback_count() > 0, "le repli sans Redis n'est pas compte")

        # 9. Base injoignable : 503 explicite, jamais des zeros decoratifs.
        ratelimit.reset()  # sinon le plafond de l'etape 8 masque la panne mesuree

        def _fabrique_en_panne():
            raise OperationalError("SELECT 1", {}, Exception("panne (porte D44)"))

        signup_route.SessionLocal = _fabrique_en_panne
        try:
            indisponible = client.post("/signup", data={**forme, "email": "panne@example.re"})
        finally:
            signup_route.SessionLocal = fabrique
        guard(
            indisponible.status_code == 503,
            f"base injoignable : {indisponible.status_code} au lieu de 503",
        )
        guard("indisponible" in fold(indisponible.text), "le 503 n'explique rien au client")
    finally:
        signup_route.SessionLocal = session_avant
        ratelimit.reset()




    # 10. Le schema, les dependances et la doc disent la meme chose que le code.
    ddl = read("app/database.py")
    orm = read("app/models.py")
    guard("CREATE TABLE IF NOT EXISTS customers" in ddl, "la table customers n'est plus dans le DDL")
    guard(
        "CREATE TABLE IF NOT EXISTS customer_validations" in ddl,
        "la table des validations clients a disparu du DDL",
    )
    for colonne in (
        "email",
        "password_hash",
        "shop_name",
        "platform",
        "api_key_hash",
        "api_key_prefix",
        "plan",
        "trial_ends_at",
        "created_at",
    ):
        guard(colonne in ddl, f"la colonne {colonne} a disparu du DDL")
        guard(colonne in orm, f"la colonne {colonne} a disparu des modeles ORM (D13)")
    requirements = read("requirements.txt")
    for paquet in ("bcrypt==", "python-multipart=="):
        guard(paquet in requirements, f"{paquet.rstrip('=')} n'est plus epingle")
    guard(
        "signup_rate_limit_per_hour" in read("app/config.py"),
        "le plafond d'inscription n'est plus configurable",
    )
    guard("D44" in read("DECISIONS.md"), "la decision D44 n'est plus consignee dans DECISIONS.md")


def d45() -> None:
    """Une cle API ouvre /validate dans les limites du compte, et sans mentir.

    Deux facons de payer coexistent sur la meme route : un abonnement (`X-API-Key`)
    et un encaissement a l'appel (x402, D34). La porte mesure ce qui les distingue,
    et ce que le client voit de chacune :

      * une cle valide sert, debite le compte, et **n'ecrit rien** au registre USDC
        — un abonnement n'est pas un encaissement, l'y inscrire ferait mentir le
        revenu publie (D40), et ce qui avance est le compteur du compte ;
      * une cle inconnue vaut `401` sans qu'aucune validation soit calculee : aucun
        fournisseur n'est interroge pour un compte non autorise ;
      * un essai termine vaut `403`, un quota du mois epuise vaut `402` : deux
        situations, deux codes, sinon le client cherche une faute de cle ;
      * la date d'essai ne ferme la porte qu'a l'**essai** : un plan payant dont la
        date d'essai est passee reste servi ;
      * le quota publie sur /pricing est **mensuel**, et c'est celui-la qui est
        applique ; le compteur a vie reste une statistique, pas une porte ;
      * une base de comptes injoignable vaut `503`, **jamais** `402` : une cle qui
        n'a pas pu etre verifiee n'est pas une cle refusee.
    """
    import tempfile
    from datetime import datetime, timedelta, timezone
    from pathlib import Path
    from unittest import mock

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.orm import sessionmaker

    from app import accounts
    from app.main import app
    from app.models import Customer, CustomerValidation
    from app.payment import PROOF_HEADER, build_payment_proof
    from app.routes import pricing as pricing_route
    from app.routes import validate as validate_route
    from app.routes.metrics import metrics

    # Fichier et non `:memory:` (meme mesure que D44) : une base en memoire est vide
    # sur *chaque* connexion du pool, donc la table creee ici n'existerait pas pour
    # la session qu'ouvre la route.
    dossier = tempfile.mkdtemp(prefix="porte-d45-")
    moteur = create_engine(f"sqlite:///{Path(dossier) / 'comptes.sqlite'}")
    Customer.__table__.create(moteur)
    CustomerValidation.__table__.create(moteur)
    fabrique = sessionmaker(bind=moteur)

    session_avant = validate_route.SessionLocal
    validate_route.SessionLocal = fabrique
    client = TestClient(app)

    corps = {
        "address": {
            "street": "1 Rue Oracle",
            "city": "Saint-Denis",
            "state": "RE",
            "zip": "97400",
            "country": "RE",
        }
    }
    resultat = {
        "validated": True,
        "standardized": {
            "line1": "1 Rue Oracle",
            "line2": "",
            "city": "Saint-Denis",
            "state": "RE",
            "zip": "97400",
            "country": "RE",
        },
        "latitude": -20.88,
        "longitude": 55.45,
        "deliverability": "residential",
        "confidence": 0.99,
        "degraded": False,
    }
    appels: list[dict] = []
    registre: list[tuple] = []

    def _valide(address: dict) -> dict:
        appels.append(address)
        return dict(resultat)

    def _enregistre(tx_hash: str, montant: float, adresse: str) -> bool:
        registre.append((tx_hash, montant, adresse))
        return True

    def naif(moment):
        """Horodatage ramene a la convention des colonnes `TIMESTAMP` (UTC naif)."""
        return moment.astimezone(timezone.utc).replace(tzinfo=None)

    def nouveau_compte(email: str, *, plan: str = "trial", fin_essai=None):
        """Compte reel en base, et sa cle en clair (la seule fois ou elle existe)."""
        with fabrique() as session:
            compte, cle = accounts.create_customer(
                session,
                email=email,
                password="porte-d45-mot-de-passe",
                shop_name="Boutique Porte",
                platform="shopify",
                plan=plan,
            )
            if fin_essai is not None:
                compte.trial_ends_at = fin_essai
                session.commit()
            return cle, compte.id

    def appel(cle: str):
        return client.post("/validate", json=corps, headers={"X-API-Key": cle})

    try:
        with (
            mock.patch("app.routes.validate.validate_full", _valide),
            mock.patch("app.routes.validate.get_cached_result", lambda key: None),
            mock.patch("app.routes.validate.store_in_cache", lambda key, value: None),
            mock.patch("app.routes.validate.record_transaction", _enregistre),
        ):
            # 1. Cle valide : servie, debitee, et rien au registre USDC.
            cle, identifiant = nouveau_compte("porte45@example.re")
            avant = metrics.snapshot()
            servie = appel(cle)
            guard(servie.status_code == 200, f"une cle valide obtient {servie.status_code}")
            corps_ok = servie.json()
            guard(
                corps_ok["result"]["validated"] is True,
                "la validation servie n'est pas le resultat du pipeline partage",
            )
            guard(
                corps_ok["price_paid"] == 0.0 and corps_ok["transaction_id"] is None,
                "une validation couverte par un abonnement annonce un encaissement USDC",
            )
            guard(
                servie.headers.get("x-payment-mode") == "api_key",
                "l'appel ne dit pas comment il a ete paye",
            )
            guard(
                servie.headers.get("x-plan") == "trial"
                and servie.headers.get("x-quota-remaining") == "99",
                "l'etat du compte n'est pas annonce : plan="
                f"{servie.headers.get('x-plan')} reste={servie.headers.get('x-quota-remaining')}",
            )
            guard(len(appels) == 1, "le pipeline de validation n'a pas ete traverse une fois")
            guard(not registre, f"un abonnement a ecrit au registre USDC : {registre}")
            with fabrique() as session:
                compte = session.get(Customer, identifiant)
                guard(
                    accounts.consumption(session, compte)["validations"] == 1,
                    "le compte n'est pas debite de la validation servie",
                )
            apres = metrics.snapshot()
            guard(
                apres["api_key_requests"] - avant["api_key_requests"] == 1
                and apres["api_key_validations"] - avant["api_key_validations"] == 1,
                "la validation par cle n'est pas comptee dans /metrics",
            )

            # 2. Cle inconnue : 401, aucune validation calculee, rien d'ecrit.
            appels.clear()
            avant = metrics.snapshot()
            inconnue = "sk_live_" + "Qq7Z" + "x" * 28
            refus = appel(inconnue)
            guard(refus.status_code == 401, f"une cle inconnue obtient {refus.status_code}")
            corps_refus = refus.json()
            guard(
                corps_refus["reason"] == "cle_inconnue" and corps_refus["mode"] == "api_key",
                "le refus d'une cle inconnue n'est pas identifiable",
            )
            guard(
                corps_refus["prefix"] == "sk_live_Qq7Z" and len(corps_refus["prefix"]) == 12,
                f"le prefixe rendu n'identifie pas la cle : {corps_refus['prefix']}",
            )
            guard(inconnue not in refus.text, "le corps d'un refus rend la cle presentee")
            guard(not appels, "une cle inconnue a fait calculer la validation")
            guard(not registre, "une cle inconnue a ecrit au registre USDC")
            guard(
                metrics.snapshot()["api_key_denied"] - avant["api_key_denied"] == 1,
                "le refus n'est pas compte dans /metrics",
            )

            # 3. Essai termine : 403. Le compte existe, il n'ouvre plus le service.
            veille = naif(datetime.now(timezone.utc) - timedelta(days=1))
            cle_expiree, _ = nouveau_compte("expire45@example.re", fin_essai=veille)
            appels.clear()
            expire = appel(cle_expiree)
            guard(expire.status_code == 403, f"un essai termine obtient {expire.status_code}")
            corps_expire = expire.json()
            guard(
                corps_expire["reason"] == "compte_expire" and corps_expire["plan"] == "trial",
                "le refus d'un essai termine n'est pas identifiable",
            )
            guard(
                corps_expire["upgrade"].endswith("/pricing"),
                "le refus d'un essai termine ne dit pas comment continuer",
            )
            guard(not appels and not registre, "un compte expire a consomme du service")

            # 4. Plan payant dont la date d'essai est passee : servi quand meme. La
            #    date dit l'essai, pas le droit de service : bloquer ici fermerait la
            #    porte a quelqu'un qui paie.
            cle_payante, id_payant = nouveau_compte(
                "payant45@example.re", plan="starter", fin_essai=veille
            )
            payee = appel(cle_payante)
            guard(payee.status_code == 200, f"un plan payant obtient {payee.status_code}")
            guard(
                payee.headers.get("x-plan") == "starter",
                f"le plan annonce est {payee.headers.get('x-plan')}",
            )
            guard(
                payee.headers.get("x-quota-remaining") == "1999",
                f"le reste du plan payant est {payee.headers.get('x-quota-remaining')}",
            )
            with fabrique() as session:
                statut = accounts.plan_status(session, session.get(Customer, id_payant))
            guard(
                statut["trial_expire"] is True and statut["expire"] is False,
                "le fait (date d'essai depassee) et la consequence (compte ferme) "
                "sont confondus",
            )
            guard(not registre, "un plan payant a ecrit au registre USDC")

            # 5. Quota du mois atteint : 402. C'est un plafond d'usage, pas un defaut
            #    d'authentification : un 401 ferait chercher une faute de cle.
            cle_quota, id_quota = nouveau_compte("quota45@example.re")
            with fabrique() as session:
                session.add_all(
                    [
                        CustomerValidation(
                            customer_id=id_quota, cache_key="porte-d45", validated=True
                        )
                        for _ in range(accounts.PLANS["trial"])
                    ]
                )
                session.commit()
            appels.clear()
            bloque = appel(cle_quota)
            guard(bloque.status_code == 402, f"un quota epuise obtient {bloque.status_code}")
            corps_quota = bloque.json()
            guard(
                corps_quota["reason"] == "quota_atteint"
                and corps_quota["quota"] == 100
                and corps_quota["validations_this_month"] == 100,
                "le refus de quota annonce "
                f"{corps_quota.get('validations_this_month')} validations "
                f"sur {corps_quota.get('quota')}",
            )
            guard(not appels and not registre, "un compte au quota a consomme du service")

            # 6. Le quota publie est mensuel : hors du mois, il ne compte plus. Un
            #    compteur a vie fermerait la porte pour toujours a un client en regle
            #    — une offre annoncee et non tenue, dans l'autre sens.
            maintenant = datetime.now(timezone.utc)
            debut_mois = datetime(
                maintenant.year, maintenant.month, 1, tzinfo=timezone.utc
            )
            cle_mois, id_mois = nouveau_compte("mois45@example.re")
            with fabrique() as session:
                session.add_all(
                    [
                        CustomerValidation(
                            customer_id=id_mois,
                            cache_key="porte-d45-mois-passe",
                            validated=True,
                            created_at=naif(debut_mois - timedelta(days=1)),
                        )
                        for _ in range(accounts.PLANS["trial"] + 5)
                    ]
                )
                # Une validation datee du 1er du mois a minuit, elle, compte : la borne
                # est inclusive, sinon la premiere heure du mois serait perdue.
                session.add(
                    CustomerValidation(
                        customer_id=id_mois,
                        cache_key="porte-d45-debut-mois",
                        validated=True,
                        created_at=naif(debut_mois),
                    )
                )
                session.commit()
                statut = accounts.plan_status(session, session.get(Customer, id_mois))
            guard(
                statut["validations"] == accounts.PLANS["trial"] + 6
                and statut["validations_mois"] == 1,
                "la fenetre mensuelle ne separe pas le mois en cours de la vie du "
                f"compte : {statut['validations']} a vie, "
                f"{statut['validations_mois']} ce mois",
            )
            guard(
                accounts.month_start(maintenant) == debut_mois,
                "le debut du mois calcule par le code est "
                f"{accounts.month_start(maintenant)}",
            )
            appels.clear()
            servie = appel(cle_mois)
            guard(
                servie.status_code == 200,
                "des validations du mois dernier ferment encore la porte : "
                f"{servie.status_code}",
            )
            # 98 : la validation semee au 1er du mois, plus celle qui vient d'etre
            # servie — le mois en cours en compte deux, la vie du compte en compte 106.
            guard(
                servie.headers.get("x-quota-remaining") == str(accounts.PLANS["trial"] - 2),
                f"le reste annonce est {servie.headers.get('x-quota-remaining')}",
            )

            # 7. Les deux en-tetes ensemble : la cle tranche, le paiement n'est meme
            #    pas examine. Encaisser un client qui paie deja un abonnement serait le
            #    facturer deux fois pour le meme service.
            registre.clear()
            appels.clear()

            def _paiement_interdit(request):  # pragma: no cover - ne doit jamais servir
                raise AssertionError("le paiement est examine malgre une cle API presente")

            with mock.patch("app.routes.validate.payment_result", _paiement_interdit):
                double = client.post(
                    "/validate",
                    json=corps,
                    headers={"X-API-Key": cle, "X-PAYMENT": "nimportequoi"},
                )
            guard(double.status_code == 200, f"cle + paiement : {double.status_code}")
            guard(
                double.headers.get("x-payment-mode") == "api_key" and not registre,
                "un appel porteur d'une cle et d'un paiement n'est pas servi par "
                "l'abonnement",
            )

            # 8. Sans aucun en-tete, le 402 x402 d'avant : le mode cle n'a rien retire
            #    aux agents autonomes.
            appels.clear()
            anonyme = client.post("/validate", json=corps)
            guard(anonyme.status_code == 402, f"sans en-tete : {anonyme.status_code}")
            corps_anonyme = anonyme.json()
            guard(
                corps_anonyme.get("x402Version") == 1
                and corps_anonyme.get("accepts")
                and "mode" not in corps_anonyme,
                "le 402 n'est plus un PaymentRequired x402 v1",
            )
            guard(not appels and not registre, "un appel non paye a consomme du service")

            # 9. Temoin du double : un paiement accepte ecrit, lui, au registre. Sans ce
            #    temoin, « rien au registre » pourrait vouloir dire « double non
            #    branche » plutot que « l'abonnement n'y ecrit pas ».
            registre.clear()
            with mode_recette():
                paye = client.post(
                    "/validate",
                    json=corps,
                    headers={PROOF_HEADER: build_payment_proof("0x" + "11" * 32)},
                )
            guard(paye.status_code == 200, f"un paiement en recette obtient {paye.status_code}")
            guard(
                len(registre) == 1 and paye.json()["price_paid"] > 0,
                f"le double du registre n'est pas branche : {registre}",
            )
            registre.clear()
            servie = appel(cle)
            guard(
                servie.status_code == 200 and not registre,
                "un abonnement est compte comme un encaissement USDC",
            )

            # 10. Base des comptes injoignable : 503 explicite, jamais 402. Demander un
            #     paiement sur une panne pousserait un client en regle a payer deux fois.
            def _en_panne():
                raise OperationalError("SELECT 1", {}, Exception("panne (porte D45)"))

            appels.clear()
            registre.clear()
            avant = metrics.snapshot()
            validate_route.SessionLocal = _en_panne
            try:
                panne = appel(cle)
            finally:
                validate_route.SessionLocal = fabrique
            guard(
                panne.status_code == 503,
                f"base injoignable : {panne.status_code} au lieu de 503",
            )
            corps_panne = panne.json()
            guard(
                corps_panne["mode"] == "api_key" and "accepts" not in corps_panne,
                "une panne des comptes est presentee comme une demande de paiement",
            )
            guard(not appels and not registre, "une panne a quand meme consomme du service")
            guard(
                metrics.snapshot()["api_key_unavailable"] - avant["api_key_unavailable"] == 1,
                "la panne n'est pas comptee dans /metrics",
            )
    finally:
        validate_route.SessionLocal = session_avant

    # 11. Contrat publie : l'en-tete documente est celui du code, la grille de
    #     /pricing est celle qui est appliquee, et la decision est consignee. Une offre
    #     affichee qui ne serait pas celle du code serait un mensonge.
    offres = {offre["nom"]: offre for offre in pricing_route.OFFRES}
    for libelle, plan in (
        ("Essai gratuit", "trial"),
        ("Starter", "starter"),
        ("Pro", "pro"),
        ("Enterprise", "enterprise"),
    ):
        offre = offres.get(libelle)
        guard(
            offre is not None and offre["validations"] == accounts.PLANS[plan],
            f"/pricing publie {offre and offre['validations']} validations pour "
            f"{libelle}, le code applique {accounts.PLANS[plan]}",
        )
        guard(
            accounts.PLAN_LIBELLES[plan] == libelle,
            f"le plan {plan} s'appelle {accounts.PLAN_LIBELLES[plan]} dans le code et "
            f"{libelle} sur /pricing",
        )
        if plan in ("starter", "pro"):
            guard(
                "mois" in offre["periode"],
                f"l'offre {libelle} ne dit plus que son quota est mensuel",
            )
    guard(
        "X-API-Key" in read("app/routes/validate.py"),
        "l'en-tete d'authentification a disparu du code",
    )
    guard(
        "X-API-Key" in read("app/routes/signup.py"),
        "l'espace client ne dit plus a son client quel en-tete envoyer",
    )
    for fichier in ("README.md", "SECURITY.md"):
        guard(
            "X-API-Key" in read(fichier),
            f"l'authentification par cle API n'est pas documentee dans {fichier}",
        )
    guard(
        "D45" in read("DECISIONS.md"),
        "la decision D45 n'est plus consignee dans DECISIONS.md",
    )
    compteurs = read("app/routes/metrics.py")
    for compteur in (
        "api_key_requests",
        "api_key_validations",
        "api_key_denied",
        "api_key_unavailable",
    ):
        guard(compteur in compteurs, f"le compteur {compteur} a disparu de /metrics")





def d46() -> None:
    """L'essai gratuit est ouvert, plafonne, et sans mensonge.

    Ce que la porte mesure, dans l'ordre ou un visiteur le rencontre :

      * `GET /validate-form` est une page **publique** : indexable, cachable
        quelques minutes, et qui ne charge **rien** (aucun script, aucune
        ressource externe) ;
      * un essai valide affiche le resultat du **pipeline de la route payante**
        (le meme objet, verifie), avance les compteurs `/metrics`, et n'ecrit
        rien : ni base des comptes (le module n'importe meme pas `SessionLocal`),
        ni registre des paiements (D29/D40) ;
      * une adresse incomplete est refusee `400` **sans** interroger le
        fournisseur : un essai gratuit ne doit pas couter un appel payant pour
        rien. Le temoin positif du meme double (l'essai valide compte un appel)
        prouve que l'absence mesuree en est bien une ;
      * la valeur saisie est echappee : le formulaire reaffiche la saisie du
        visiteur, jamais du HTML ;
      * le plafond est **applique et annonce** : `validate_form_rate_limit_per_day`
        essais par IP et par 24 h (la valeur configuree, pas une valeur de test),
        puis `429` avec un `Retry-After` exact et borne par la fenetre, la raison,
        et une porte de sortie (creer un compte) ;
      * un refus ne consomme pas d'essai, et deux IP ont deux fenetres ;
      * un resultat **degrade** n'est pas un verdict sur l'adresse ;
      * le contrat publie suit : la page est liee depuis la navigation, le
        plafond vit dans la configuration et dans `.env.example`, les trois
        compteurs sont dans `/metrics`, et la decision est consignee.
    """
    import asyncio
    from unittest import mock

    from fastapi.testclient import TestClient

    from app import ratelimit
    from app.config import get_settings
    from app.main import app
    from app.routes import validate as validate_route
    from app.routes import validate_form as form_route
    from app.routes.metrics import metrics

    reglages = get_settings()
    plafond = reglages.validate_form_rate_limit_per_day
    guard(plafond > 0, f"plafond d'essais gratuit invalide : {plafond}")

    resultat = {
        "validated": True,
        "standardized": {
            "line1": "1 Rue Oracle",
            "line2": "",
            "city": "Saint-Denis",
            "state": "RE",
            "zip": "97400",
            "country": "RE",
        },
        "latitude": -20.88,
        "longitude": 55.45,
        "deliverability": "residential",
        "confidence": 0.99,
        "degraded": False,
    }
    adresse = {
        "street": "1 Rue Oracle",
        "city": "Saint-Denis",
        "state": "RE",
        "zip": "97400",
        "country": "RE",
    }

    # Le pipeline est partage : une seule validation, un seul cache, donc un seul
    # verdict possible entre l'essai et le service vendu.
    guard(
        form_route.perform_validation is validate_route.perform_validation,
        "l'essai gratuit a sa propre copie du pipeline : les deux peuvent diverger",
    )

    appels: list[dict] = []
    resultats: list[dict] = [resultat]

    def _valide(brut: dict) -> tuple[dict, str]:
        appels.append(dict(brut))
        return dict(resultats[0]), "cle-de-cache-oracle"

    ratelimit.reset()
    client = TestClient(app)

    with mock.patch("app.routes.validate_form.perform_validation", _valide):
        # 1. La page publique : rien a charger, et tout est dit au visiteur.
        page = client.get("/validate-form")
        guard(page.status_code == 200, f"/validate-form repond {page.status_code}")
        guard(
            page.headers.get("cache-control") == "public, max-age=300",
            f"la page publique est servie en {page.headers.get('cache-control')}",
        )
        guard(
            "form-action 'self'" in page.headers.get("content-security-policy", ""),
            "la CSP n'autorise plus le formulaire a poster sur lui-meme",
        )
        guard(
            'name="robots" content="index, follow"' in page.text,
            "la page d'essai n'est plus indexable",
        )
        for champ in ("street", "city", "state", "zip", "country"):
            guard(f'name="{champ}"' in page.text, f"le champ {champ} a disparu")
        guard(
            'action="/validate-form"' in page.text,
            "le formulaire ne poste plus sur la route",
        )
        guard("<script" not in page.text, "la page d'essai charge un script")
        guard(
            "http://" not in page.text and "https://" not in page.text,
            "la page d'essai renvoie vers une ressource externe",
        )
        guard(
            str(plafond) in page.text,
            f"le plafond annonce ne correspond plus au reglage ({plafond})",
        )
        guard(
            'href="/signup"' in page.text and 'href="/pricing"' in page.text,
            "la page d'essai n'oriente plus vers l'inscription ou les tarifs",
        )

        # 2. Un essai valide : servi par le pipeline partage, compte, rien d'ecrit.
        avant = metrics.snapshot()
        servie = client.post("/validate-form", data=adresse)
        guard(servie.status_code == 200, f"un essai valide obtient {servie.status_code}")
        guard(
            servie.headers.get("cache-control") == "no-store",
            "la reponse d'essai, qui contient l'adresse du visiteur, est cachable",
        )
        guard(
            'name="robots" content="noindex, nofollow"' in servie.text,
            "la reponse d'essai est indexable",
        )
        guard("Adresse reconnue" in servie.text, "le verdict de l'essai a disparu")
        guard(
            "1 Rue Oracle" in servie.text and "Saint-Denis" in servie.text,
            "l'adresse standardisee n'est pas affichee",
        )
        guard("residential" in servie.text, "la livrabilite n'est pas affichee")
        guard(len(appels) == 1, f"le pipeline a ete traverse {len(appels)} fois")
        guard(appels and appels[0] == adresse, f"le pipeline a recu {appels}")
        apres = metrics.snapshot()
        guard(
            apres["validate_form_requests"] - avant["validate_form_requests"] == 1
            and apres["validate_form_validations"]
            - avant["validate_form_validations"]
            == 1,
            "l'essai n'est pas compte dans /metrics",
        )
        source = read("app/routes/validate_form.py")
        guard(
            "SessionLocal" not in source and "record_transaction" not in source,
            "l'essai gratuit a gagne un acces a la base ou au registre des paiements",
        )
        guard(
            not hasattr(form_route, "SessionLocal"),
            "l'essai gratuit importe la session de la base des comptes",
        )

        # 3. Une adresse incomplete : refusee avant tout appel paye.
        appels.clear()
        incomplete = client.post("/validate-form", data={**adresse, "street": "  "})
        guard(
            incomplete.status_code == 400,
            f"une adresse incomplete obtient {incomplete.status_code}",
        )
        guard(not appels, "une adresse incomplete a interroge le fournisseur payant")
        guard(
            "Saint-Denis" in incomplete.text,
            "la saisie n'est plus reaffichee au visiteur qui doit corriger",
        )

        # 4. La saisie du visiteur ne devient jamais du HTML.
        appels.clear()
        piege = client.post(
            "/validate-form", data={**adresse, "street": "<script>alert(1)</script>"}
        )
        guard(
            "<script>" not in piege.text and "&lt;script&gt;" in piege.text,
            "la valeur saisie n'est plus echappee dans la page",
        )

        # 5. Un resultat degrade n'est pas un verdict sur l'adresse.
        appels.clear()
        resultats[0] = {**resultat, "validated": False, "degraded": True}
        try:
            degrade = client.post("/validate-form", data=adresse)
        finally:
            resultats[0] = resultat
        guard(
            "pas répondu" in degrade.text,
            "un resultat degrade ne dit plus qu'il est degrade",
        )
        guard(
            "pas une preuve d'inexistence" in degrade.text,
            "un resultat degrade peut passer pour une adresse invalide",
        )
        guard(
            "Adresse non reconnue" not in degrade.text,
            "une panne du fournisseur est presentee comme une adresse invalide",
        )

        # 6. Le plafond : applique, annonce, exact, par IP, sans consommer d'essai.
        appels.clear()
        avant = metrics.snapshot()
        for index in range(1, plafond + 1):
            consomme = client.post(
                "/validate-form", data=adresse, headers={"X-Forwarded-For": "203.0.113.9"}
            )
            guard(
                consomme.status_code == 200,
                f"l'essai {index} du plafond obtient {consomme.status_code}",
            )
        bloque = client.post(
            "/validate-form", data=adresse, headers={"X-Forwarded-For": "203.0.113.9"}
        )
        guard(
            bloque.status_code == 429,
            f"l'essai {plafond + 1} obtient {bloque.status_code} au lieu de 429",
        )
        attente = int(bloque.headers.get("retry-after", "0"))
        guard(
            0 < attente <= 24 * 60 * 60,
            f"Retry-After annonce {attente} s : nul, ou au-dela de la fenetre",
        )
        guard(
            f"{plafond} essais gratuits" in bloque.text,
            "le refus de plafond n'annonce plus la limite configuree",
        )
        guard(
            'href="/signup"' in bloque.text,
            "le refus de plafond ne propose pas de creer un compte",
        )
        guard(
            metrics.snapshot()["validate_form_denied"] - avant["validate_form_denied"] == 1,
            "le refus de plafond n'est pas compte dans /metrics",
        )
        encore = client.post(
            "/validate-form", data=adresse, headers={"X-Forwarded-For": "203.0.113.9"}
        )
        guard(
            encore.status_code == 429
            and int(encore.headers["retry-after"]) <= attente,
            "un refus de plafond repousse la fin de la fenetre du visiteur",
        )
        voisin = client.post(
            "/validate-form", data=adresse, headers={"X-Forwarded-For": "198.51.100.9"}
        )
        guard(
            voisin.status_code == 200,
            "le plafond n'est plus par IP : un visiteur bloquerait les autres",
        )
        guard(
            ratelimit.fallback_count() > 0,
            "le repli local du plafond n'est pas compte : le garde-fou redevient invisible",
        )

    # 7. Le contrat publie suit le code.
    guard(
        plafond
        == form_route.get_settings().validate_form_rate_limit_per_day,
        "le plafond lu par la page n'est plus celui de la configuration",
    )
    guard(
        "validate_form" in read("app/main.py"),
        "/validate-form n'est plus monte par l'application",
    )
    guard(
        "validate_form_rate_limit_per_day" in read("app/config.py"),
        "le plafond des essais gratuits n'est plus configurable",
    )
    guard(
        "VALIDATE_FORM_RATE_LIMIT_PER_DAY" in read(".env.example"),
        "le plafond des essais gratuits a disparu du modele d'environnement",
    )
    guard(
        "/validate-form" in read("app/web.py"),
        "la page d'essai n'est plus dans la navigation du site",
    )
    guard(
        "/validate-form" in read("app/ratelimit.py"),
        "le second usage du plafond (essai gratuit) n'est plus documente",
    )
    from app.routes import metrics as metrics_module

    payload = asyncio.run(metrics_module.get_metrics())
    for compteur in (
        "validate_form_requests",
        "validate_form_validations",
        "validate_form_denied",
    ):
        guard(compteur in payload, f"le compteur {compteur} est absent de /metrics")
    guard(
        "D46" in read("DECISIONS.md"),
        "la decision D46 n'est plus consignee dans DECISIONS.md",
    )
    guard(
        "/validate-form" in read("README.md"),
        "la page d'essai n'est plus documentee dans README.md",
    )
    guard(
        "essais gratuits" in read("SECURITY.md"),
        "le plafond d'essais gratuits n'est plus documente dans SECURITY.md",
    )


def d47() -> None:
    """Un resultat degrade ne se garde pas trente jours.

    `degraded: true` ne dit pas « adresse invalide », il dit « le fournisseur n'a
    pas repondu » (D23). Le garder aussi longtemps qu'un verdict le transformerait
    en reponse fausse d'un mois, sur une adresse que le client paie a chaque appel
    (ou consomme a son quota), et sans aucun moyen de forcer une nouvelle
    verification. La porte mesure les deux cotes avec le **meme** double de cache
    (un double qui retient les TTL demandes) :

      * un resultat **degrade** est ecrit avec `CACHE_TTL_DEGRADED` (5 minutes) ;
      * il est **bien** garde ces cinq minutes : une deuxieme demande identique est
        servie depuis le cache, sans rappeler le fournisseur (c'est la protection
        mesuree par D23/D25, qui ne doit pas disparaitre en corrigeant le TTL) ;
      * un resultat **verifie** est ecrit avec `CACHE_TTL` (30 jours) : le temoin
        positif du meme double, sans lequel « 5 minutes » pourrait vouloir dire
        « ce double n'ecrit rien » ;
      * l'entree courte expiree, la meme adresse est **re-verifiee** : une panne
        passee ne fige pas l'adresse pour un mois.
    """
    from unittest import mock

    from fastapi.testclient import TestClient

    from app import cache as cache_module
    from app.main import app
    from app.payment import build_payment_proof

    guard(
        cache_module.CACHE_TTL == 60 * 60 * 24 * 30,
        f"TTL du cache des resultats verifies = {cache_module.CACHE_TTL} s",
    )
    guard(
        cache_module.CACHE_TTL_DEGRADED == 5 * 60,
        f"TTL d'un resultat degrade = {cache_module.CACHE_TTL_DEGRADED} s",
    )
    guard(
        cache_module.CACHE_TTL_DEGRADED < cache_module.CACHE_TTL,
        "un resultat degrade est garde aussi longtemps qu'un resultat verifie",
    )

    class _Redis:
        """Double minimal : retient ce qui est ecrit, et le TTL demande."""

        def __init__(self) -> None:
            self.store: dict = {}
            self.ttls: dict = {}

        def get(self, key):
            return self.store.get(key)

        def setex(self, key, ttl, value):
            self.store[key] = value
            self.ttls[key] = ttl

        def ping(self):
            return True

    resultat = {
        "validated": True,
        "standardized": {
            "line1": "1 Rue Oracle",
            "line2": "",
            "city": "Saint-Denis",
            "state": "RE",
            "zip": "97400",
            "country": "RE",
        },
        "latitude": -20.88,
        "longitude": 55.45,
        "deliverability": "residential",
        "confidence": 0.99,
        "degraded": False,
    }

    def corps(rue: str) -> dict:
        return {
            "address": {
                "street": rue,
                "city": "Saint-Denis",
                "state": "RE",
                "zip": "97400",
                "country": "RE",
            }
        }

    double = _Redis()
    panne = {"en_panne": True}
    appels = {"fournisseur": 0}

    def _valide(brut: dict) -> dict:
        appels["fournisseur"] += 1
        if panne["en_panne"]:
            return {**resultat, "validated": False, "degraded": True}
        return dict(resultat)

    client = TestClient(app)
    with (
        mode_recette(),
        mock.patch("app.routes.validate.validate_full", _valide),
        mock.patch(
            "app.routes.validate.record_transaction", lambda tx, montant, cle: True
        ),
        mock.patch.object(cache_module, "redis_client", double),
    ):
        cache_module.reset_backend_state()
        entetes = {"X-Payment-Proof": build_payment_proof("0x" + "22" * 32)}
        try:
            degradee = client.post(
                "/validate", json=corps("1 Rue Degradee"), headers=entetes
            )
            guard(
                degradee.status_code == 200,
                f"un appel pendant une panne fournisseur obtient {degradee.status_code}",
            )
            corps_degrade = degradee.json()["result"]
            guard(
                corps_degrade["degraded"] is True,
                "le double n'a pas produit de resultat degrade : la suite ne mesure rien",
            )
            cle_degradee = corps_degrade["cache_key"]
            guard(
                double.ttls.get(f"addr:{cle_degradee}") == cache_module.CACHE_TTL_DEGRADED,
                "TTL ecrit pour un resultat degrade : "
                f"{double.ttls.get('addr:' + cle_degradee)} s",
            )

            # Il est bien garde ces cinq minutes : c'est ce qui evite de marteler un
            # fournisseur en panne (D23/D25), et ca ne doit pas disparaitre.
            avant = appels["fournisseur"]
            repetee = client.post(
                "/validate", json=corps("1 Rue Degradee"), headers=entetes
            )
            guard(
                repetee.json()["result"]["cache_key"] == cle_degradee
                and appels["fournisseur"] == avant,
                "la reponse degradee gardee cinq minutes est recalculee a chaque appel",
            )

            # Temoin positif du meme double : un verdict, lui, se garde trente jours.
            panne["en_panne"] = False
            verifiee = client.post(
                "/validate", json=corps("2 Rue Verifiee"), headers=entetes
            )
            corps_verifie = verifiee.json()["result"]
            guard(corps_verifie["degraded"] is False, "le double n'a pas rendu de verdict")
            guard(
                double.ttls.get(f"addr:{corps_verifie['cache_key']}")
                == cache_module.CACHE_TTL,
                "TTL ecrit pour un resultat verifie : "
                f"{double.ttls.get('addr:' + corps_verifie['cache_key'])} s",
            )

            # Et la fenetre courte laisse revenir la verite : l'entree expiree, la
            # meme adresse repasse par le fournisseur.
            double.store.pop(f"addr:{cle_degradee}")
            reprise = client.post(
                "/validate", json=corps("1 Rue Degradee"), headers=entetes
            )
            guard(
                reprise.json()["result"]["degraded"] is False
                and reprise.json()["result"]["validated"] is True,
                "une adresse marquee degradee reste fausse apres la fenetre courte",
            )
        finally:
            cache_module.reset_backend_state()

    # La decision est consignee, et le contrat publie la dit.
    guard("CACHE_TTL_DEGRADED" in read("app/cache.py"), "le TTL degrade a disparu du cache")
    guard("D47" in read("DECISIONS.md"), "la decision D47 n'est plus consignee dans DECISIONS.md")
    guard(
        "5 minutes" in read("README.md") and "dégradé" in read("README.md"),
        "README.md ne dit plus combien de temps un resultat degrade est garde",
    )
    guard(
        "degrade" in fold(read("PRIVACY.md")),
        "PRIVACY.md ne dit plus qu'un resultat degrade est garde brievement",
    )


def d48() -> None:
    """Le guide d'integration dit la verite, et ne dit rien de secret.

    Un guide est un contrat : s'il promet un en-tete que le serveur ne renvoie
    pas, ou un code que la route n'emet jamais, il coute une journee a
    l'integrateur. La porte mesure donc quatre choses, sur la page reellement
    servie :

      * elle est **publique** : `200`, indexable, cachable cinq minutes, et elle
        ne charge **rien** (aucun script, aucune ressource externe) ;
      * elle ne publie **aucun secret** : une page indexable est publique par
        construction, donc la cle partagee et le mot de passe du tableau de bord
        ne doivent pas y figurer, et la cle d'exemple reste un gabarit ;
      * elle dit les faits du **code** : base publique de la configuration (aucun
        gabarit non remplace), prix, plafond de l'essai gratuit, en-tetes
        reellement poses par la route, plans reellement appliques, et les huit
        codes de retour ;
      * elle est **atteignable** : liee depuis la navigation partagee, donc depuis
        `/pricing` et `/validate-form`, et la barre de `/pricing` couvre chaque
        lien de la navigation partagee (une seule liste de pages, D60).
    """
    from fastapi.testclient import TestClient

    from app import i18n
    from app.config import get_settings
    from app.main import app
    from app.routes.guide import CODES
    from app.web import site_head

    reglages = get_settings()
    client = TestClient(app)

    page = client.get("/guide")
    guard(page.status_code == 200, f"/guide repond {page.status_code}")
    guard(
        page.headers.get("cache-control") == "public, max-age=300",
        f"/guide est servi en {page.headers.get('cache-control')}",
    )
    csp = page.headers.get("content-security-policy", "")
    guard("form-action 'none'" in csp, f"/guide accepte de poster ailleurs : {csp!r}")
    guard(
        'name="robots" content="index, follow"' in page.text,
        "le guide n'est plus indexable",
    )
    for interdit in ("<script", "<img", "src=", "@import", "url("):
        guard(
            interdit not in page.text,
            f"le guide charge quelque chose ({interdit}) : il n'est plus autonome",
        )
    # D57 : des <link>, mais seulement les hreflang (comme la vitrine, D53 amende).
    guard(
        all('rel="alternate"' in lien for lien in re.findall(r"<link\b[^>]*>", page.text)),
        "le guide charge une ressource par <link>",
    )

    # 2. Aucun secret sur une page indexable.
    if reglages.api_secret_key:
        guard(
            reglages.api_secret_key not in page.text,
            "le secret de signature est publie sur une page indexable",
        )
    if reglages.dashboard_password:
        guard(
            reglages.dashboard_password not in page.text,
            "le mot de passe du tableau de bord est publie sur une page indexable",
        )
    guard(
        "sk_live_VOTRE_CLE" in page.text,
        "l'exemple de cle n'est plus un gabarit reconnaissable",
    )

    # 3. Les faits cites sont ceux du code.
    base = reglages.public_base_url.rstrip("/")
    guard(base in page.text, f"la base publique du guide n'est plus {base}")
    guard("BASE_URL" not in page.text, "un gabarit d'URL est reste non remplace")
    guard(
        f"{i18n.nombre('fr', reglages.x402_price)} USDC" in page.text,
        "le prix publie n'est plus celui de la configuration",
    )
    guard(
        str(reglages.validate_form_rate_limit_per_day) in page.text,
        "le plafond de l'essai gratuit n'est plus celui de la configuration",
    )
    for en_tete in ("X-Payment-Mode", "X-Plan", "X-Quota-Remaining"):
        guard(en_tete in page.text, f"l'en-tete {en_tete} a disparu du guide")
        guard(
            en_tete in read("app/routes/validate.py"),
            f"l'en-tete {en_tete} est documente mais plus pose par la route",
        )
    for code, _situation, _quoi in CODES:
        guard(f">{code}<" in page.text, f"le code {code} a disparu du guide")
    for plan in ("trial", "starter", "pro", "enterprise"):
        guard(plan in page.text, f"le plan {plan} n'est plus cite par le guide")

    # 4. Il est atteignable, et la navigation n'est plus recopiee.
    for chemin in ("/pricing", "/validate-form"):
        vue = client.get(chemin)
        guard(
            'href="/guide"' in vue.text,
            f"le guide n'est pas atteignable depuis {chemin}",
        )
    # La barre de /pricing vient du dessin valide (D60) : elle couvre chaque lien de site_head().
    tarifs = client.get("/pricing").text
    guard(
        all(f'href="{lien}"' in tarifs for lien in re.findall(r'href="(/[^"]*)"', site_head())),
        "/pricing a perdu un lien de site_head() : les pages peuvent diverger",
    )

    # 5. La decision est consignee.
    guard("D48" in read("DECISIONS.md"), "la decision D48 n'est plus consignee dans DECISIONS.md")
    guard("/guide" in read("README.md"), "le guide n'est plus documente dans README.md")


def d49() -> None:
    """L'email de bienvenue promet exactement ce que le service applique.

    Un email commercial est une promesse : s'il annonce des jours d'essai, un quota
    ou un prix que le service ne tient pas, le premier client le decouvre a ses
    frais. La porte mesure donc, dans le texte reellement expedie :

      * les chiffres de l'offre sont ceux du **code** : jours d'essai et quota du
        plan d'essai (`accounts.JOURS_ESSAI`, `accounts.VALIDATIONS_ESSAI`), et les
        deux prix mensuels de `/pricing` (29 EUR / 2 000, 79 EUR / 10 000) ;
      * le mode est celui d'une **boutique** : `X-API-Key`, jamais un paiement a
        l'appel — un commercant n'a pas de portefeuille a brancher, et l'email ne
        doit pas lui apprendre une voie qui n'est pas la sienne ;
      * il ne contient **aucun secret** : ni cle `sk_live_` complete, ni valeur par
        defaut du depot (`change_me`) ;
      * les routes citees existent vraiment (`/signup`, `/compte`, `/guide`,
        `/validate-form`) ;
      * il dit ses **limites** : aucun prelevement automatique, un resultat
        `degraded` qui n'est pas un verdict, et une empreinte (pas une adresse)
        conservee — avec la voie d'effacement ;
      * l'exemplaire « pret a envoyer » ne contient plus **aucune** variable de
        fusion, et chaque variable du gabarit est expliquee dans la table ;
      * la decision est consignee, et `README.md` dit ou vit le message.
    """
    from app import accounts
    from app.routes import pricing as pricing_route

    email = read("EMAIL_BIENVENUE.md")

    # 1. Les chiffres sont ceux du code, pas ceux du marketing.
    guard(
        str(accounts.JOURS_ESSAI) in email,
        f"l'email n'annonce plus l'essai de {accounts.JOURS_ESSAI} jours",
    )
    guard(
        str(accounts.VALIDATIONS_ESSAI) in email,
        f"l'email n'annonce plus les {accounts.VALIDATIONS_ESSAI} validations offertes",
    )
    for offre in pricing_route.OFFRES:
        if offre.get("prix_eur") is None:
            continue
        prix = f"{offre['prix_eur']:.0f}"
        quota = f"{offre['validations']:,}".replace(",", " ")
        guard(
            f"{prix} €" in email and quota in email,
            f"l'offre {offre['nom']} de /pricing ({prix} EUR / {quota}) "
            "n'apparait plus dans l'email",
        )
    guard(
        "/mois" in email,
        "l'email ne dit plus que les plans sont mensuels (le quota applique l'est)",
    )

    # 2. Le mode est celui d'une boutique, et lui seul.
    guard("X-API-Key" in email, "l'email n'indique plus l'en-tete X-API-Key")
    for hors_sujet in ("X-Payment-Proof", "tx_hash", "X-PAYMENT"):
        guard(
            hors_sujet not in email,
            f"l'email d'une boutique parle de {hors_sujet} : ce n'est pas sa voie",
        )

    # 3. Aucun secret, ni cle complete, ni valeur par defaut du depot.
    guard(
        re.search(r"sk_live_[A-Za-z0-9]{32}", email) is None,
        "une cle API complete figure dans l'email",
    )
    guard("change_me" not in email, "une valeur par defaut du depot figure dans l'email")
    guard(
        "empreinte" in fold(email),
        "l'email ne dit plus que seule une empreinte de la cle est conservee",
    )

    # 4. Les routes citees existent.
    for route in ("/signup", "/compte", "/guide", "/validate-form"):
        guard(route in email, f"l'email ne cite plus {route}")
        guard(
            route.lstrip("/") in read("app/main.py"),
            f"la route {route} citee par l'email n'est plus montee par l'application",
        )

    # 5. Les limites sont dites.
    guard(
        "prelevement automatique" in fold(email),
        "l'email ne dit plus qu'aucun prelevement automatique n'a lieu",
    )
    guard(
        "degraded" in email and "ne dit rien" in fold(email),
        "l'email ne dit plus qu'un resultat degrade n'est pas un verdict",
    )
    guard("cache_key" in email, "l'email ne dit plus comment demander un effacement")

    # 6. Le gabarit est complet, et l'exemplaire est pret a envoyer.
    variables = set(re.findall(r"\{\{(\w+)\}\}", email))
    guard(variables, "le gabarit ne contient plus aucune variable de fusion")
    exemplaire = email.split("## 2.")[-1].split("## 3.")[0]
    guard(
        "{{" not in exemplaire,
        "l'exemplaire « pret a envoyer » contient encore une variable de fusion",
    )
    table = email.split("## 3.")[-1].split("## 4.")[0]
    for variable in variables:
        guard(
            f"{{{{{variable}}}}}" in table,
            f"la variable {{{{{variable}}}}} n'est expliquee nulle part",
        )

    # 7. La decision est consignee, et le depot dit ou vit le message.
    guard("D49" in read("DECISIONS.md"), "la decision D49 n'est plus consignee dans DECISIONS.md")
    guard(
        "EMAIL_BIENVENUE.md" in read("README.md"),
        "README.md ne dit plus ou vit l'email de bienvenue",
    )


def d50() -> None:
    """Le suivi client dit l'etat servi, dans l'ordre d'urgence, sans secret ni ecriture.

    Un tableau de suivi ne vaut que par son **accord** avec ce qui est reellement
    servi : un rapport qui affiche « actif » un compte dont les appels recoivent
    `403` rassure au lieu de decider — c'est pire que pas de rapport. La porte
    mesure donc, sur une base SQLite reelle (les deux tables des comptes) :

      * **l'accord etat / refus, et son inverse** : un compte dont
        `accounts.refusal_reason` renvoie `compte_expire` est exactement un compte
        annonce « essai fini » (idem `quota_atteint` / « quota atteint »), avec le
        code HTTP lu dans `REFUS_HTTP` (`app/routes/validate.py`) ; et un compte
        servi n'entre dans aucun etat de refus ;
      * le cas que D44/D45 protegent : un **client payant dont la date d'essai est
        passee** n'est pas « essai fini » — sinon le rapport ferait appeler au
        telephone un client en regle ;
      * l'**ordre publie** est un ordre : priorites uniques et croissantes dans
        `suivi.ETATS`, l'etat sans action en dernier, et le classement reel suit
        cet ordre d'urgence (un essai qui se termine passe avant un compte jamais
        servi) ;
      * **aucun secret** : le rapport porte le prefixe de cle et jamais la cle
        complete, ni une empreinte ;
      * `app/suivi.py` est en **lecture seule** (aucun `add`, `commit`, `flush` ni
        `delete`) et ne laisse aucun objet en attente dans la session ;
      * une base **sans table** fait sortir `scripts/suivi_clients.py` en code `2`
        (lancement reel du script) au lieu de presenter zero comme un resultat,
        tandis qu'une base garnie sort en code `0` avec un vrai rapport — CSV
        compris, en-tete du module ;
      * la decision est consignee (`DECISIONS.md`, `README.md`).
    """
    import subprocess
    import tempfile
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app import accounts, suivi
    from app.models import Customer, CustomerValidation
    from app.routes.validate import REFUS_HTTP

    maintenant = datetime(2026, 10, 13, 9, 0, tzinfo=timezone.utc)
    cles: list[str] = []

    def compte(
        session,
        email: str,
        *,
        plan: str = "trial",
        cree_il_y_a: int = 0,
        essai_dans: int = accounts.JOURS_ESSAI,
    ) -> Customer:
        """Un compte reel en base.

        L'empreinte du mot de passe est factice : le suivi ne verifie aucun mot de
        passe, payer un hachage bcrypt ici ne mesurerait rien de plus.
        """
        cle, empreinte, prefixe = accounts.new_api_key()
        client = Customer(
            email=email,
            password_hash="x" * 60,
            shop_name=email.split("@")[0].capitalize(),
            platform="shopify",
            api_key_hash=empreinte,
            api_key_prefix=prefixe,
            plan=plan,
            trial_ends_at=maintenant + timedelta(days=essai_dans),
            created_at=(maintenant - timedelta(days=cree_il_y_a)).replace(tzinfo=None),
        )
        session.add(client)
        session.commit()
        cles.append(cle)
        return client

    def usage(
        session, client: Customer, *, il_y_a_jours: int = 1, combien: int = 1
    ) -> None:
        """Des validations reellement servies, a une date choisie."""
        quand = (maintenant - timedelta(days=il_y_a_jours)).replace(tzinfo=None)
        for _ in range(combien):
            session.add(
                CustomerValidation(
                    customer_id=client.id,
                    cache_key=None,
                    validated=True,
                    created_at=quand,
                )
            )
        session.commit()

    with tempfile.TemporaryDirectory() as dossier:
        base = Path(dossier) / "suivi.sqlite"
        url = f"sqlite:///{base}"
        moteur = create_engine(url)
        Customer.__table__.create(moteur)
        CustomerValidation.__table__.create(moteur)
        session = sessionmaker(bind=moteur)()

        expire = compte(session, "expire@example.re", essai_dans=-2)
        epuise = compte(session, "epuise@example.re", essai_dans=10)
        usage(session, epuise, combien=accounts.VALIDATIONS_ESSAI)
        abonne = compte(
            session, "abonne@example.re", plan="starter", essai_dans=-10, cree_il_y_a=40
        )
        usage(session, abonne, il_y_a_jours=2)
        sain = compte(session, "sain@example.re", essai_dans=10)
        usage(session, sain, combien=3)

        # 1. L'etat affiche est l'etat servi : refus et etat coincident, et le code
        #    HTTP affiche est celui que la route pose vraiment.
        for client, attendu, refus_attendu in (
            (expire, "essai_fini", accounts.REFUS_COMPTE_EXPIRE),
            (epuise, "quota_atteint", accounts.REFUS_QUOTA_ATTEINT),
        ):
            refus = accounts.refusal_reason(session, client, maintenant)
            guard(
                refus == refus_attendu,
                f"{client.email} n'est plus refuse ({refus}) : l'oracle ne mesure plus rien",
            )
            ligne = suivi.etat_client(session, client, maintenant=maintenant)
            guard(
                ligne["etat"] == attendu,
                f"{client.email} est classe {ligne['etat']} alors que la route renvoie {refus}",
            )
            guard(
                ligne["refus_http"] == REFUS_HTTP[refus],
                f"{client.email} : le code HTTP affiche n'est plus celui de la route",
            )

        # 2. Et l'inverse, plus le cas que D44/D45 protegent : un abonne dont la
        #    date d'essai est passee est servi, donc il n'est pas « essai fini ».
        for client in (sain, abonne):
            guard(
                accounts.refusal_reason(session, client, maintenant) is None,
                f"{client.email} est refuse alors que le rapport le dit servi",
            )
            ligne = suivi.etat_client(session, client, maintenant=maintenant)
            guard(
                ligne["refus"] is None and ligne["refus_http"] is None,
                f"{client.email} : un refus est affiche pour un compte servi",
            )
            guard(
                ligne["etat"] not in ("essai_fini", "quota_atteint"),
                f"{client.email} est annonce refuse alors que ses appels passent",
            )
        guard(
            suivi.etat_client(session, abonne, maintenant=maintenant)["etat"]
            == suivi.ETAT_OK,
            "un abonne dont la date d'essai est passee est annonce en essai fini (D44)",
        )

        # 3. L'ordre publie est un ordre, et le rapport le suit reellement.
        priorites = [etat["priorite"] for etat in suivi.ETATS.values()]
        guard(
            priorites == sorted(priorites) == list(range(1, len(priorites) + 1)),
            "les priorites des etats de suivi ne sont plus un ordre strict",
        )
        guard(
            suivi.ETATS[suivi.ETAT_OK]["priorite"] == max(priorites),
            "l'etat sans action n'est plus le dernier de l'ordre publie",
        )
        compte(session, "bientot@example.re", essai_dans=2)
        compte(session, "muet@example.re", cree_il_y_a=9)
        lignes = suivi.suivi(session, maintenant=maintenant)
        attendus = {
            "expire@example.re": "essai_fini",
            "epuise@example.re": "quota_atteint",
            "abonne@example.re": suivi.ETAT_OK,
            "sain@example.re": suivi.ETAT_OK,
            "bientot@example.re": "essai_bientot",
            "muet@example.re": "jamais_utilise",
        }
        guard(
            {ligne["email"]: ligne["etat"] for ligne in lignes} == attendus,
            "le suivi ne classe plus les comptes stockes comme la porte l'exige",
        )
        rangs = {ligne["email"]: index for index, ligne in enumerate(lignes)}
        guard(
            rangs["bientot@example.re"] < rangs["muet@example.re"],
            "un essai qui se termine n'est plus traite avant un compte jamais servi",
        )
        guard(
            [ligne["priorite"] for ligne in lignes]
            == sorted(ligne["priorite"] for ligne in lignes),
            "le rapport ne suit plus l'ordre d'urgence publie",
        )
        compteurs = suivi.resume(lignes)
        guard(
            compteurs["total"] == len(attendus)
            and compteurs["a_joindre"]
            == sum(1 for etat in attendus.values() if etat != suivi.ETAT_OK),
            "les compteurs du rapport ne correspondent plus aux comptes stockes",
        )

        # 4. Aucun secret dans ce qui sort, et un module qui ne fait que lire.
        texte = suivi.rapport_texte(lignes, maintenant)
        tableau = suivi.rapport_csv(lignes)
        guard(
            expire.api_key_prefix in texte and expire.api_key_prefix in tableau,
            "le rapport ne porte plus le prefixe de cle : un compte n'est plus identifiable",
        )
        guard(
            "cle_prefixe" in suivi.COLONNES_CSV,
            "la colonne de prefixe de cle a disparu de l'export",
        )
        for cle in cles:
            guard(
                cle not in texte and cle not in tableau,
                "une cle API complete apparait dans le rapport",
            )
        code = read("app/suivi.py")
        for interdit in (".add(", ".commit(", ".flush(", ".delete("):
            guard(
                interdit not in code,
                f"app/suivi.py n'est plus en lecture seule ({interdit})",
            )
        guard(
            not session.new and not session.dirty and not session.deleted,
            "le suivi a laisse des objets en attente dans la session : il ecrit",
        )

        # 5. Un rapport vide n'est pas un « tout va bien » : le script doit le dire
        #    en code 2 quand rien ne peut etre mesure, et sortir 0 sinon.
        script = ROOT / "scripts" / "suivi_clients.py"
        vide = Path(dossier) / "sans-table.sqlite"
        echec = subprocess.run(
            [sys.executable, str(script), "--url", f"sqlite:///{vide}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=ROOT,
        )
        guard(
            echec.returncode == 2,
            f"une base sans table sort en code {echec.returncode} au lieu de 2",
        )
        guard(
            "compte(s) dans la base" not in echec.stdout,
            "une base sans table est presentee comme un rapport vide (zero = tout va bien)",
        )
        guard(
            "INDISPONIBLE" in echec.stderr,
            "l'echec de mesure n'est plus dit explicitement sur la sortie d'erreur",
        )

        succes = subprocess.run(
            [
                sys.executable,
                str(script),
                "--url",
                url,
                "--maintenant",
                maintenant.isoformat(),
                "--format",
                "csv",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=ROOT,
        )
        guard(
            succes.returncode == 0,
            f"le suivi sort en code {succes.returncode} sur une base garnie",
        )
        lignes_csv = succes.stdout.strip().splitlines()
        guard(
            lignes_csv[0].split(";") == list(suivi.COLONNES_CSV),
            "l'en-tete CSV du script n'est plus celui du module",
        )
        guard(
            len(lignes_csv) == len(lignes) + 1,
            "le CSV ne contient plus une ligne par compte",
        )
        for cle in cles:
            guard(cle not in succes.stdout, "une cle API complete sort du script")

        session.close()
        moteur.dispose()

    # 6. La decision est consignee, et le depot dit comment lancer le suivi.
    guard(
        "D50" in read("DECISIONS.md"),
        "la decision D50 n'est plus consignee dans DECISIONS.md",
    )
    guard(
        "suivi_clients.py" in read("README.md"),
        "README.md ne dit plus comment produire le suivi client",
    )
    guard(
        "app/suivi.py" in read("README.md"),
        "README.md ne dit plus ou vivent les regles du suivi",
    )


def d51() -> None:
    """Les adresses US et hors US sont standardisees sans deformation (mots entiers, USPS = US seulement, St = Saint en tete)."""
    from app.validation.parser import parse_address
    from app.validation.standardizer import standardize_address

    def passer(rue: str, pays: str) -> dict:
        return standardize_address(parse_address({"street": rue, "country": pays}))

    attendus = [
        ("100 Stewart St", "US", "100 Stewart Street", ""),
        ("1 Florida Ave", "US", "1 Florida Avenue", ""),
        ("12 Résidence Les Flamboyants", "RE", "12 Résidence Les Flamboyants", ""),
        ("5 Rue St Joseph", "RE", "5 Rue St Joseph", ""),
        ("123 St Charles Ave", "US", "123 St Charles Avenue", ""),
        ("123 Main St Apt 4B", "US", "123 Main Street", "Apartment 4B"),
        ("500 Fifth Ave #12", "US", "500 Fifth Avenue", "Apartment 12"),
        ("500 Fifth Ave # 12", "US", "500 Fifth Avenue", "Apartment 12"),
        ("100 N Main St", "US", "100 North Main Street", ""),
        ("1 grange road", "US", "1 Grange Road", ""),
    ]
    for rue, pays, ligne1, ligne2 in attendus:
        sortie = passer(rue, pays)
        guard(
            sortie["line1"] == ligne1 and sortie["line2"] == ligne2,
            f"{rue!r} ({pays}) donne {sortie['line1']!r} / {sortie['line2']!r} au lieu de {ligne1!r} / {ligne2!r}",
        )

    guard(
        "D51" in read("DECISIONS.md"),
        "la decision D51 n'est plus consignee dans DECISIONS.md",
    )


def d52() -> None:
    """Les textes publics decrivent les deux voies de paiement : x402 sans compte, abonnement par cle d'API."""
    from fastapi.testclient import TestClient

    from app.main import app

    anciennes = (
        "pas d'inscription, pas de cle d'API",
        "Pas d'abonnement, pas de compte.",
        "Aucun abonnement, aucune cle d'API, aucun compte",
    )
    for name in ("llms.txt", "AGENTS.md"):
        texte = read(name)
        for phrase in anciennes:
            guard(phrase not in texte, f"{name} affirme encore {phrase!r} alors que /signup existe (D44)")
        guard("X-API-Key" in texte, f"{name} ne dit plus que l'abonnement passe par X-API-Key")
        guard("kder-ai.com/signup" in texte, f"{name} ne cite plus /signup")

    reponse = TestClient(app).get("/manifest")
    guard(reponse.status_code == 200, f"/manifest repond {reponse.status_code}")
    limites = " ".join(reponse.json().get("limitations", []))
    guard(
        "pas de compte, pas de nom, pas d'e-mail. Le revenu" not in limites,
        "le manifeste affirme encore qu'aucun compte n'existe alors que /signup collecte un e-mail",
    )
    guard("X-API-Key" in limites, "le manifeste ne dit plus que l'abonnement cree un compte client")

    guard(
        "pièce fragile" not in read("EMAIL_BIENVENUE.md"),
        "l'email de Tiitoune Art parle encore de piece fragile (elle vend du henne)",
    )
    guard("D52" in read("DECISIONS.md"), "la decision D52 n'est plus consignee dans DECISIONS.md")


def d53() -> None:
    """La page d'accueil sert la vitrine aux navigateurs et garde le JSON pour les programmes, chiffres lus dans le code."""
    from fastapi.testclient import TestClient

    from app import accounts
    from app.config import get_settings
    from app.main import app
    from app.routes import accueil
    from app.routes.validate_form import CHAMPS

    client = TestClient(app)
    navigateur = client.get("/", headers={"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})
    guard(
        navigateur.status_code == 200 and navigateur.headers["content-type"].startswith("text/html"),
        f"un navigateur ne recoit pas la vitrine sur / : {navigateur.status_code}",
    )
    guard(navigateur.headers.get("vary") == "Accept", "la vitrine ne porte pas Vary: Accept")
    csp = navigateur.headers.get("content-security-policy", "")
    guard("default-src 'none'" in csp and "form-action 'self'" in csp, f"CSP de la vitrine inattendue : {csp}")
    texte = navigateur.text
    for interdit in ("<script", "@import", "url(", "{{"):
        guard(interdit not in texte, f"la vitrine contient {interdit!r}")
    guard(not re.search(r"https?://", texte), "la vitrine cite une URL externe")
    # D57 : des photos, mais seulement celles du site ; des <link>, mais seulement hreflang.
    guard(
        all(src.startswith("/images/") for src in re.findall(r'<img\b[^>]*\bsrc="([^"]*)"', texte)),
        "la vitrine charge une image qui n'est pas celle du site",
    )
    guard(
        all('rel="alternate"' in lien for lien in re.findall(r"<link\b[^>]*>", texte)),
        "la vitrine charge une ressource par <link>",
    )
    noms = re.findall(r'<input[^>]*\bname="(\w+)"', texte)
    guard(noms == [nom for nom, _, _ in CHAMPS], f"champs du formulaire de la vitrine : {noms}")

    programme = client.get("/")
    guard(programme.headers.get("content-type") == "application/json", "un programme ne recoit plus du JSON sur /")
    guard(programme.headers.get("vary") == "Accept", "le JSON de / ne porte pas Vary: Accept")
    guard(programme.json().get("service") == "Address Validator API", f"charge utile de / : {programme.json()}")

    guard(
        f"{accounts.JOURS_ESSAI} jours" in texte and f"{accounts.VALIDATIONS_ESSAI} validations" in texte,
        "l'essai affiche par la vitrine n'est pas celui d'accounts.py",
    )
    autre = accueil.page(get_settings().model_copy(update={"x402_price": 0.05}))
    guard("0,05 USDC" in autre and "0,02 USDC" not in autre, "le prix x402 de la vitrine ne suit pas la configuration")

    guard("D53" in read("DECISIONS.md"), "la decision D53 n'est plus consignee dans DECISIONS.md")


def d54() -> None:
    """L'inscription libre ne cree que des comptes d'essai, quel que soit le plan poste."""
    import tempfile
    from pathlib import Path

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app import accounts, ratelimit
    from app.main import app
    from app.models import Customer, CustomerValidation
    from app.routes import signup as signup_route

    dossier = tempfile.mkdtemp(prefix="porte-d54-")
    moteur = create_engine(f"sqlite:///{Path(dossier) / 'clients.sqlite'}")
    Customer.__table__.create(moteur)
    CustomerValidation.__table__.create(moteur)
    fabrique = sessionmaker(bind=moteur)

    session_avant = signup_route.SessionLocal
    signup_route.SessionLocal = fabrique
    ratelimit.reset()
    client = TestClient(app)
    try:
        formulaire = client.get("/signup?plan=enterprise")
        guard('name="plan"' not in formulaire.text, "le formulaire d'inscription porte encore un plan choisi par le client")
        for numero, plan in enumerate(p for p in accounts.PLANS if p != accounts.PLAN_PAR_DEFAUT):
            email = f"porte-d54-{numero}@example.re"
            reponse = client.post(
                "/signup",
                data={
                    "email": email,
                    "password": "un-mot-de-passe-solide-12",
                    "shop_name": "Porte D54",
                    "platform": "shopify",
                    "plan": plan,
                },
            )
            guard(reponse.status_code == 200, f"inscription avec plan={plan} : {reponse.status_code}")
            with fabrique() as session:
                compte = accounts.find_by_email(session, email)
                guard(compte is not None, f"aucun compte cree pour plan={plan}")
                guard(
                    compte.plan == accounts.PLAN_PAR_DEFAUT,
                    f"plan={plan} poste par le client donne un compte {compte.plan!r} au lieu de l'essai",
                )
                guard(compte.trial_ends_at is not None, f"plan={plan} : compte sans date de fin d'essai")
    finally:
        signup_route.SessionLocal = session_avant
        ratelimit.reset()
        moteur.dispose()

    guard("D54" in read("DECISIONS.md"), "la decision D54 n'est plus consignee dans DECISIONS.md")


def d55() -> None:
    """Le contact publie est l'adresse du domaine, la meme dans security.txt et dans la configuration."""
    from app.config import Settings

    defaut = Settings.model_fields["contact_email"].default
    guard(defaut.endswith("@kder-ai.com"), f"le contact par defaut n'est pas une adresse du domaine : {defaut}")
    trouve = re.search(r"(?m)^Contact:\s*mailto:(\S+)", read(".well-known/security.txt"))
    guard(trouve is not None, "security.txt sans champ Contact")
    guard(trouve.group(1) == defaut, f"security.txt publie {trouve.group(1)} mais la configuration {defaut}")
    guard("Kdera597420@gmail.com" not in read("PRIVACY.md"), "PRIVACY.md cite encore l'ancienne adresse Gmail")
    guard("D55" in read("DECISIONS.md"), "la decision D55 n'est plus consignee dans DECISIONS.md")


def d56() -> None:
    """L'IP d'un client est celle que Nginx voit : une fausse IP glissee devant ne contourne aucun plafond."""
    from types import SimpleNamespace
    from unittest import mock

    from fastapi.testclient import TestClient

    from app import observability, ratelimit
    from app.config import get_settings
    from app.main import app
    from app.routes import validate_form as form_route

    def requete(entete):
        return SimpleNamespace(headers={"X-Forwarded-For": entete}, client=SimpleNamespace(host="127.0.0.1"))

    guard(
        observability.client_ip(requete("6.6.6.6, 203.0.113.9")) == "203.0.113.9",
        "client_ip lit encore l'entree choisie par le client au lieu de celle ajoutee par Nginx",
    )
    guard(observability.client_ip(requete("203.0.113.9")) == "203.0.113.9", "client_ip perd une entree unique")

    resultat = {
        "validated": True,
        "standardized": {"line1": "1 Rue Oracle", "line2": "", "city": "Seattle", "state": "WA", "zip": "98101", "country": "US"},
        "latitude": None, "longitude": None, "deliverability": "residential", "confidence": 0.99, "degraded": False,
    }
    plafond = get_settings().validate_form_rate_limit_per_day
    adresse = {"street": "1 Rue Oracle", "city": "Seattle", "state": "WA", "zip": "98101", "country": "US"}
    ratelimit.reset()
    try:
        with mock.patch.object(form_route, "perform_validation", return_value=(resultat, "cle")):
            client = TestClient(app)
            codes = [
                client.post(
                    "/validate-form", data=adresse,
                    headers={"X-Forwarded-For": "198.51.100.%d, 203.0.113.9" % (index % 250)},
                ).status_code
                for index in range(plafond + 2)
            ]
    finally:
        ratelimit.reset()
    guard(
        codes[:plafond] == [200] * plafond and codes[plafond:] == [429, 429],
        f"une IP falsifiee devant l'IP reelle ouvre encore de nouveaux essais : {codes}",
    )

    guard(
        "proxy_set_header X-Forwarded-For $remote_addr;" in read("nginx.conf"),
        "nginx.conf ajoute encore l'IP reelle a l'en-tete du client au lieu de le remplacer",
    )
    guard("D56" in read("DECISIONS.md"), "la decision D56 n'est plus consignee dans DECISIONS.md")


def d57() -> None:
    """Le site parle six langues : six vitrines servies, photos du site sous /images, CSP img-src 'self', prix convertis depuis les euros du code."""
    from decimal import Decimal

    from fastapi.testclient import TestClient

    from app import accounts, i18n
    from app.main import app
    from app.routes.pricing import OFFRES

    client = TestClient(app)
    guard(i18n.chemin("fr", "pricing") == "/pricing", "le francais n'est plus a la racine : les adresses publiees changent")
    for lang in i18n.LANGUES:
        adresse = i18n.chemin(lang)
        reponse = client.get(adresse, headers={"Accept": "text/html,*/*;q=0.8"})
        guard(
            reponse.status_code == 200 and reponse.headers.get("content-type", "").startswith("text/html"),
            f"{adresse} ne sert pas la vitrine : {reponse.status_code}",
        )
        csp = reponse.headers.get("content-security-policy", "")
        guard("img-src 'self'" in csp, f"{adresse} : la CSP n'autorise pas les photos du site : {csp}")
        guard("default-src 'none'" in csp and "script-src" not in csp, f"{adresse} : CSP elargie : {csp}")
        texte = reponse.text
        balise = re.search(r'<html lang="([\w-]+)"( dir="rtl")?>', texte)
        guard(balise is not None and balise.group(1).split("-")[0] == lang, f"{adresse} n'est pas en {lang}")
        guard(bool(balise.group(2)) == (lang in i18n.RTL), f"{adresse} : sens d'ecriture faux")
        for interdit in ("<script", "{{", "@import", "url("):
            guard(interdit not in texte, f"{adresse} contient {interdit!r}")
        guard(not re.search(r"https?://", texte), f"{adresse} cite une URL externe")
        guard(
            all('rel="alternate"' in lien for lien in re.findall(r"<link\b[^>]*>", texte)),
            f"{adresse} charge une ressource par <link>",
        )
        alternatives = dict(re.findall(r'<link rel="alternate" hreflang="([\w-]+)" href="([^"]*)">', texte))
        guard(
            alternatives == {**{autre: i18n.chemin(autre) for autre in i18n.LANGUES}, "x-default": i18n.chemin("en")},
            f"{adresse} : hreflang inattendus : {alternatives}",
        )
        sources = re.findall(r'<img\b[^>]*\bsrc="([^"]*)"', texte)
        guard(len(sources) == 4, f"{adresse} montre {len(sources)} photos au lieu de 4")
        for src in sources:
            guard(src.startswith("/images/"), f"{adresse} charge une image hors du site : {src}")
            photo = client.get(src)
            guard(
                photo.status_code == 200 and photo.headers.get("content-type") == "image/jpeg",
                f"{src} n'est pas servie en image/jpeg : {photo.status_code}",
            )
            guard(photo.headers.get("x-content-type-options") == "nosniff", f"{src} sans nosniff")
        for cle in ("essai", "starter", "pro"):
            guard(f">{i18n.prix(lang, cle)}<" in texte, f"{adresse} n'affiche pas {i18n.prix(lang, cle)}")
        guard(i18n.mention_euros(lang) in texte, f"{adresse} : la mention de facturation en euros manque")

    offres = {offre["nom"]: offre for offre in OFFRES}
    for cle, nom in (("starter", "Starter"), ("pro", "Pro")):
        guard(i18n.prix("fr", cle) == offres[nom]["prix"], f"le prix {nom} en euros n'est plus celui de pricing.OFFRES")
        euros = Decimal(str(offres[nom]["prix_eur"]))
        for devise, taux in i18n.TAUX.items():
            converti, attendu = i18n.MONTANTS[devise][cle], euros * taux
            guard(
                abs(converti - attendu) <= attendu / 100,
                f"{nom} en {devise} : {converti} affiche pour {euros} EUR factures (~{attendu:.0f}) : table a refaire",
            )
        for devise, montants in i18n.MONTANTS.items():
            guard(
                montants[f"unitaire_{cle}"] == montants[cle] / accounts.PLANS[cle],
                f"prix unitaire {nom} en {devise} : n'est pas le prix divise par le quota",
            )
    guard(
        i18n.MONTANTS["USD"]["colis_rate"] == Decimal(str(accounts.COUT_ECHEC_USD)),
        "le colis rate en dollars n'est plus celui d'accounts.COUT_ECHEC_USD",
    )
    guard("D57" in read("DECISIONS.md"), "la decision D57 n'est plus consignee dans DECISIONS.md")


def d58() -> None:
    """Le guide d'integration parle six langues et dit dans chacune ce que le code applique : base, prix, plafonds, essai, cache et version x402 injectes, aucun secret."""
    from fastapi.testclient import TestClient

    from app import accounts, cache, i18n, x402
    from app.config import get_settings
    from app.main import app
    from app.routes import guide
    from app.routes.guide import CODES

    reglages = get_settings()
    client = TestClient(app)
    base = reglages.public_base_url.rstrip("/")
    for lang in i18n.LANGUES:
        adresse = i18n.chemin(lang, "guide")
        reponse = client.get(adresse)
        guard(reponse.status_code == 200, f"{adresse} repond {reponse.status_code}")
        guard(
            reponse.headers.get("cache-control") == "public, max-age=300",
            f"{adresse} est servi en {reponse.headers.get('cache-control')}",
        )
        csp = reponse.headers.get("content-security-policy", "")
        guard(
            "default-src 'none'" in csp and "form-action 'none'" in csp and "script-src" not in csp,
            f"{adresse} : CSP inattendue : {csp}",
        )
        texte = reponse.text
        balise = re.search(r'<html lang="([\w-]+)"( dir="rtl")?>', texte)
        guard(balise is not None and balise.group(1).split("-")[0] == lang, f"{adresse} n'est pas en {lang}")
        guard(bool(balise.group(2)) == (lang in i18n.RTL), f"{adresse} : sens d'ecriture faux")
        for interdit in ("<script", "<img", "src=", "@import", "url(", "<form", "{{", "BASE_URL"):
            guard(interdit not in texte, f"{adresse} contient {interdit!r}")
        guard(
            all('rel="alternate"' in lien for lien in re.findall(r"<link\b[^>]*>", texte)),
            f"{adresse} charge une ressource par <link>",
        )
        adresses = re.findall(r"https?://[^\s\"'<]+", texte)
        guard(
            adresses and all(url == f"{base}/validate" for url in adresses),
            f"{adresse} cite une autre URL que {base}/validate : {sorted(set(adresses))}",
        )
        alternatives = dict(re.findall(r'<link rel="alternate" hreflang="([\w-]+)" href="([^"]*)">', texte))
        guard(
            alternatives == {**{autre: i18n.chemin(autre, "guide") for autre in i18n.LANGUES}, "x-default": i18n.chemin("en", "guide")},
            f"{adresse} : hreflang inattendus : {alternatives}",
        )
        guard(not re.search(r"sk_live_[A-Za-z0-9]{16,}", texte), f"{adresse} publie une cle sk_live_ complete")
        for secret in (reglages.api_secret_key, reglages.dashboard_password):
            guard(not secret or secret not in texte, f"{adresse} publie un secret de la configuration")
        for en_tete in ("X-Payment-Mode", "X-Plan", "X-Quota-Remaining"):
            guard(
                en_tete in texte and en_tete in read("app/routes/validate.py"),
                f"{adresse} : l'en-tete {en_tete} manque au guide ou n'est plus pose par la route",
            )
        guard(
            texte.count('class="code-row') == len(CODES) and all(f'id="code-{code}"' in texte for code, _, _ in CODES),
            f"{adresse} ne decrit plus les huit codes de retour",
        )

    # D48 : changer le code change la page. Valeurs reconnaissables, chacune a sa place.
    sauve = accounts.JOURS_ESSAI, accounts.VALIDATIONS_ESSAI, cache.CACHE_TTL, cache.CACHE_TTL_DEGRADED, x402.X402_VERSION
    accounts.JOURS_ESSAI, accounts.VALIDATIONS_ESSAI = 8642, 7531
    cache.CACHE_TTL, cache.CACHE_TTL_DEGRADED, x402.X402_VERSION = 864 * 86400, 975 * 60, 7
    autres = reglages.model_copy(
        update={
            "x402_price": 0.05,
            "validate_form_rate_limit_per_day": 9753,
            "public_base_url": "https://exemple.test",
            "contact_email": "contact@exemple.test",
        }
    )
    try:
        for lang in i18n.LANGUES:
            page = guide.page(autres, lang)
            for valeur, fois in ((8642, 1), (7531, 2), (9753, 1), (864, 3), (975, 1), (0.05, 5)):
                vu = page.count(i18n.nombre(lang, valeur))
                guard(vu == fois, f"guide {lang} : {valeur} affiche {vu} fois au lieu de {fois} : un chiffre est recopie dans le gabarit")
            guard(page.count('{"x402Version": 7}') == 2, f"guide {lang} : la version x402 n'est pas celle du code")
            guard(
                page.count("https://exemple.test/validate") == 4 and "kder-ai.com" not in page,
                f"guide {lang} : la base des exemples n'est pas public_base_url",
            )
            guard(page.count("contact@exemple.test") == 2, f"guide {lang} : le contact n'est pas celui de la configuration")
    finally:
        accounts.JOURS_ESSAI, accounts.VALIDATIONS_ESSAI, cache.CACHE_TTL, cache.CACHE_TTL_DEGRADED, x402.X402_VERSION = sauve

    guard("D58" in read("DECISIONS.md"), "la decision D58 n'est plus consignee dans DECISIONS.md")
# --- fin du texte proposé -----------------------------------------------------


def d59() -> None:
    """L'essai gratuit parle six langues : chaque page poste sur sa propre adresse, montre le vrai resultat dans sa langue, et le plafond reste par IP."""
    from unittest import mock

    from fastapi.testclient import TestClient

    from app import i18n, ratelimit
    from app.config import get_settings
    from app.main import app
    from app.routes import validate as validate_route
    from app.routes import validate_form as form_route

    guard(
        form_route.perform_validation is validate_route.perform_validation,
        "l'essai gratuit a sa propre copie du pipeline : les deux peuvent diverger",
    )
    reconnu = {
        "validated": True,
        "standardized": {"line1": "100 Stewart Street", "line2": "", "city": "Seattle", "state": "WA", "zip": "98101", "country": "US"},
        "latitude": 47.6105, "longitude": -122.341, "deliverability": "residential", "confidence": 1.0, "degraded": False,
    }
    degrade = {**reconnu, "validated": False, "deliverability": "invalid", "confidence": 0.05, "degraded": True}
    adresse = {"street": "100 stewart st", "city": "seattle", "state": "wa", "zip": "98101", "country": "US"}
    noms = [nom for nom, _libelle, _exemple in form_route.CHAMPS]
    plafond = get_settings().validate_form_rate_limit_per_day
    guard(plafond > 1, f"plafond d'essais gratuits trop bas pour etre mesure : {plafond}")
    client = TestClient(app)

    ratelimit.reset()
    try:
        for index, lang in enumerate(i18n.LANGUES):
            chemin, textes = i18n.chemin(lang, "validate-form"), form_route.TEXTES[lang]
            page = client.get(chemin)
            guard(page.status_code == 200, f"{chemin} repond {page.status_code}")
            guard(page.headers.get("cache-control") == "public, max-age=300", f"{chemin} : cache {page.headers.get('cache-control')}")
            guard("form-action 'self'" in page.headers.get("content-security-policy", ""), f"{chemin} : la CSP n'autorise plus le formulaire")
            balise = re.search(r'<html lang="([\w-]+)"( dir="rtl")?>', page.text)
            guard(balise is not None and balise.group(1).split("-")[0] == lang, f"{chemin} n'est pas en {lang}")
            guard(bool(balise.group(2)) == (lang in i18n.RTL), f"{chemin} : sens d'ecriture faux")
            for interdit in ("<script", "{{", "http://", "https://"):
                guard(interdit not in page.text, f"{chemin} contient {interdit!r}")
            guard(f'method="post" action="{chemin}"' in page.text, f"{chemin} : le formulaire ne poste pas sur sa propre langue")
            champs = re.findall(r'<input[^>]*\bname="(\w+)"', page.text)
            guard(champs == noms, f"{chemin} : champs du formulaire {champs}")
            guard(form_route._texte(lang, "essais", plafond) in page.text, f"{chemin} n'annonce pas le plafond configure ({plafond})")

            ip = {"X-Forwarded-For": f"203.0.113.{100 + index}"}
            with mock.patch.object(form_route, "perform_validation", return_value=(reconnu, "cle")):
                servie = client.post(chemin, data=adresse, headers=ip)
            guard(
                servie.status_code == 200 and servie.headers.get("cache-control") == "no-store",
                f"{chemin} : un essai obtient {servie.status_code} en {servie.headers.get('cache-control')}",
            )
            guard(
                textes["ok"] in servie.text and "100 Stewart Street" in servie.text and ">residential<" in servie.text,
                f"{chemin} : le vrai resultat du pipeline n'est pas affiche dans la langue",
            )
            guard(form_route._texte(lang, "reste", plafond - 1) in servie.text, f"{chemin} : essais restants faux")
            with mock.patch.object(form_route, "perform_validation", return_value=(degrade, "cle")):
                panne = client.post(chemin, data=adresse, headers=ip)
            guard(
                textes["degrade"] in panne.text and textes["ko"] not in panne.text and 'class="big ko"' not in panne.text,
                f"{chemin} : une panne du fournisseur est presentee comme une adresse invalide",
            )

        # Le plafond est par IP, toutes langues confondues, et le 429 parle la langue de son adresse.
        ip = {"X-Forwarded-For": "203.0.113.200"}
        with mock.patch.object(form_route, "perform_validation", return_value=(reconnu, "cle")):
            codes = [
                client.post(i18n.chemin(i18n.LANGUES[n % len(i18n.LANGUES)], "validate-form"), data=adresse, headers=ip).status_code
                for n in range(plafond)
            ]
            bloque = client.post(i18n.chemin("ar", "validate-form"), data=adresse, headers=ip)
    finally:
        ratelimit.reset()
    guard(codes == [200] * plafond, f"essais servis avant le plafond : {codes}")
    guard(bloque.status_code == 429, f"changer de langue ouvre de nouveaux essais : {bloque.status_code}")
    attente = int(bloque.headers.get("retry-after", "0"))
    guard(0 < attente <= form_route.FENETRE_ESSAI_SECONDES, f"Retry-After annonce {attente} s")
    minutes = max(1, (attente + 59) // 60)
    guard(
        form_route._texte("ar", "attente", minutes) in bloque.text and f"Retry-After: {attente}" in bloque.text,
        "le 429 ne donne pas l'attente exacte dans la langue de son adresse",
    )
    guard("<form" not in bloque.text, "le 429 garde le formulaire au lieu de la page dediee")
    guard("D59" in read("DECISIONS.md"), "la decision D59 n'est plus consignee dans DECISIONS.md")


def d60() -> None:
    """La page des tarifs parle six langues : adresses litterales, chiffres du code, aucun temoignage."""
    from fastapi.testclient import TestClient

    from app import accounts, i18n
    from app.config import get_settings
    from app.main import app
    from app.routes import pricing

    reglages = get_settings()
    client = TestClient(app)
    chemins = [getattr(route, "path", "") for route in app.routes]
    guard(
        not [chemin for chemin in chemins if "{" in chemin and "pricing" in chemin],
        "une route /{lang}/pricing avalerait d'autres adresses : chemins litteraux exiges (D57)",
    )
    autre = reglages.model_copy(
        update={"trial_days": 21, "x402_price": 0.05, "contact_email": "porte@example.org"}
    )
    for lang in i18n.LANGUES:
        adresse = i18n.chemin(lang, "pricing")
        guard(chemins.count(adresse) == 1, f"{adresse} n'est pas une route litterale unique")
        reponse = client.get(adresse)
        guard(reponse.status_code == 200, f"{adresse} repond {reponse.status_code}")
        guard(
            reponse.headers.get("cache-control") == "public, max-age=300",
            f"{adresse} n'est pas cachable comme une page publique",
        )
        csp = reponse.headers.get("content-security-policy", "")
        guard(
            "default-src 'none'" in csp and "form-action 'none'" in csp and "script-src" not in csp,
            f"{adresse} : CSP inattendue : {csp}",
        )
        texte = reponse.text
        balise = re.search(r'<html lang="([\w-]+)"( dir="rtl")?>', texte)
        guard(balise is not None and balise.group(1).split("-")[0] == lang, f"{adresse} n'est pas en {lang}")
        guard(bool(balise.group(2)) == (lang in i18n.RTL), f"{adresse} : sens d'ecriture faux")
        guard('name="robots" content="index, follow"' in texte, f"{adresse} n'est pas indexable")
        for interdit in ("<script", "{{", "@import", "url(", "<img", "<form"):
            guard(interdit not in texte, f"{adresse} contient {interdit!r}")
        guard(not re.search(r"https?://", texte), f"{adresse} cite une URL externe")

        # 1. Les chiffres du code : prix de la langue, prix unitaire = prix / quota
        #    avec sa division affichee, quotas d'accounts.PLANS, mention en euros.
        for cle in ("essai", "starter", "pro", "unitaire_starter", "unitaire_pro"):
            guard(f">{i18n.prix(lang, cle)}<" in texte, f"{adresse} n'affiche pas {i18n.prix(lang, cle)}")
        for plan in ("starter", "pro"):
            quota = i18n.nombre(lang, accounts.PLANS[plan])
            guard(
                f">{quota}<" in texte and f"{i18n.prix(lang, plan)} ÷ {quota}" in texte,
                f"{adresse} : le quota ou la division du prix {plan} ne sont plus ceux du code",
            )
        guard(i18n.mention_euros(lang) in texte, f"{adresse} : la mention de facturation en euros manque")

        # 2. Les boutons : essai, Starter et Pro vers l'inscription de la langue (D54 :
        #    aucun plan transmis), Enterprise vers le contact de la configuration.
        grille = re.search(r'<ol class="tiers">(.*?)</ol>', texte, re.S)
        boutons = re.findall(r'<a class="btn [^"]*" href="([^"]*)"', grille.group(1) if grille else "")
        guard(
            boutons == [i18n.chemin(lang, "signup")] * 3 + [f"mailto:{reglages.contact_email}"],
            f"{adresse} : boutons des offres inattendus : {boutons}",
        )

        # 3. Ce qui est montre est ce qui est applique (D48) : changer la configuration
        #    change la page, donc aucun de ces chiffres n'est ecrit dans le gabarit.
        lu = re.sub(r'<style>.*?</style>|<svg\b.*?</svg>|\sstyle="[^"]*"', "", pricing.page(autre, lang), flags=re.S)
        guard(
            i18n.nombre(lang, 0.05) in lu and i18n.nombre(lang, reglages.x402_price) not in lu,
            f"{adresse} : le prix x402 affiche n'est pas celui de la configuration",
        )
        guard(
            "porte@example.org" in lu and reglages.contact_email not in lu,
            f"{adresse} : le contact affiche n'est pas celui de la configuration",
        )
        guard(
            re.search(r"(?<![\d.,])21(?![\d.,])", lu) is not None
            and re.search(rf"(?<![\d.,]){reglages.trial_days}(?![\d.,])", lu) is None,
            f"{adresse} : la duree d'essai affichee n'est pas celle de la configuration",
        )

        # 4. Aucun temoignage, meme drapeau leve (corrections d'honnetete validees).
        avec_drapeau = pricing.page(reglages.model_copy(update={"pricing_show_testimonials": True}), lang)
        guard('id="temoignages"' not in avec_drapeau, f"{adresse} publie des temoignages")

    guard("D60" in read("DECISIONS.md"), "la decision D60 n'est plus consignee dans DECISIONS.md")


def d61() -> None:
    """L'inscription, la page de la cle et l'espace client tiennent leurs regles dans les six langues."""
    import base64
    import tempfile
    from pathlib import Path

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app import accounts, i18n, ratelimit
    from app.main import app
    from app.models import Customer, CustomerValidation
    from app.routes import signup as signup_route

    dossier = tempfile.mkdtemp(prefix="porte-lot-signup-")
    moteur = create_engine(f"sqlite:///{Path(dossier) / 'clients.sqlite'}")
    Customer.__table__.create(moteur)
    CustomerValidation.__table__.create(moteur)
    fabrique = sessionmaker(bind=moteur)
    session_avant = signup_route.SessionLocal
    signup_route.SessionLocal = fabrique
    client = TestClient(app)
    try:
        for lang in i18n.LANGUES:
            ratelimit.reset()
            inscription, compte = i18n.chemin(lang, "signup"), i18n.chemin(lang, "compte")
            # 1. Le formulaire : dans sa langue, postant dans sa langue, sans plan (D54), sans script.
            texte = client.get(inscription + "?plan=enterprise").text
            balise = re.search(r'<html lang="([\w-]+)"( dir="rtl")?>', texte)
            guard(balise is not None and balise.group(1).split("-")[0] == lang, f"{inscription} n'est pas en {lang}")
            guard(bool(balise.group(2)) == (lang in i18n.RTL), f"{inscription} : sens d'ecriture faux")
            guard(f'method="post" action="{inscription}"' in texte, f"{inscription} ne poste pas dans sa langue")
            noms = re.findall(r'<(?:input|select|textarea)\b[^>]*\bname="(\w+)"', texte)
            guard(noms == ["email", "password", "shop_name", "platform"], f"{inscription} : champs {noms} (D54 : jamais de plan)")
            for interdit in ("<script", "{{", "<!--@"):
                guard(interdit not in texte, f"{inscription} contient {interdit!r}")
            guard(not re.search(r"https?://", texte), f"{inscription} cite une URL externe")

            # 2. L'inscription : un essai, meme si un plan est poste ; la cle montree une fois, no-store,
            #    noindex, la vraie cle dans le premier appel, seule son empreinte en base.
            email, secret = f"porte-lot-{lang}@example.re", "porte-lot-mot-de-passe"
            cree = client.post(inscription, data={
                "email": email, "password": secret, "shop_name": "<b>Porte</b>", "platform": "shopify", "plan": "enterprise",
            })
            guard(cree.status_code == 200, f"POST {inscription} : {cree.status_code}")
            guard(cree.headers.get("cache-control") == "no-store", f"la page de la cle ({lang}) est cachable")
            guard('name="robots" content="noindex, nofollow"' in cree.text, f"la page de la cle ({lang}) est indexable")
            guard("<b>Porte</b>" not in cree.text, f"la page de la cle ({lang}) n'echappe pas le nom de la boutique")
            cles = re.findall(r"sk_live_[A-Za-z0-9]{32}", cree.text)
            guard(bool(cles) and f'<span class="key-l">{cles[0]}</span>' in cree.text,
                  f"la vraie cle n'est pas dans l'exemple de premier appel ({lang})")
            with fabrique() as session:
                compte_db = accounts.find_by_email(session, email)
                guard(compte_db.plan == accounts.PLAN_PAR_DEFAUT, f"inscription {lang} : plan {compte_db.plan!r} (D54)")
                guard(compte_db.api_key_hash == accounts.api_key_hash(cles[0]) and cles[0] not in compte_db.api_key_hash,
                      f"inscription {lang} : la base ne garde pas l'empreinte de la cle affichee")

            # 3. L'espace client : ferme par defaut, prive, sans la cle, regeneration dans sa langue,
            #    estimation dans la monnaie de la langue.
            anonyme = client.get(compte, follow_redirects=False)
            guard(anonyme.status_code == 303 and anonyme.headers.get("location") == compte + "/connexion"
                  and "www-authenticate" not in anonyme.headers,
                  f"{compte} s'ouvre sans identifiants : {anonyme.status_code} (D62 : page de connexion)")
            jeton = base64.b64encode(f"{email}:{secret}".encode()).decode()
            espace = client.get(compte, headers={"Authorization": f"Basic {jeton}"})
            guard(espace.status_code == 200 and espace.headers.get("cache-control") == "no-store", f"{compte} : {espace.status_code}")
            guard(cles[0] not in espace.text and compte_db.api_key_prefix in espace.text, f"{compte} : cle complete ou prefixe absent")
            guard(f'action="{compte}/cle"' in espace.text, f"{compte} : la regeneration ne poste pas dans sa langue")
            guard(f">{i18n.prix(lang, 'colis_rate')}<" in espace.text, f"{compte} : l'estimation n'est pas dans la monnaie de la langue")
    finally:
        signup_route.SessionLocal = session_avant
        ratelimit.reset()
        moteur.dispose()
    guard("D61" in read("DECISIONS.md"), "la decision D61 n'est plus consignee dans DECISIONS.md")


def d62() -> None:
    """L'espace client a une page de connexion et une session : jamais le defi Basic pour un navigateur, cookie sur, echecs bloques, autre site refuse."""
    import hashlib
    import tempfile
    from pathlib import Path

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app import i18n, ratelimit
    from app.config import get_settings
    from app.main import app
    from app.models import Customer, CustomerSession, CustomerValidation
    from app.routes import connexion as connexion_route
    from app.routes import signup as signup_route

    reglages = get_settings()
    moteur = create_engine(f"sqlite:///{Path(tempfile.mkdtemp(prefix='porte-d62-')) / 'clients.sqlite'}")
    for modele in (Customer, CustomerValidation, CustomerSession):
        modele.__table__.create(moteur)
    fabrique = sessionmaker(bind=moteur)
    avant = signup_route.SessionLocal, connexion_route.SessionLocal, connexion_route.get_settings
    signup_route.SessionLocal = connexion_route.SessionLocal = fabrique
    try:
        for lang in i18n.LANGUES:
            ratelimit.reset()
            client = TestClient(app, base_url="https://testserver")
            compte = i18n.chemin(lang, "compte")
            connexion = compte + "/connexion"
            email, secret = f"porte-d62-{lang}@example.re", "porte-d62-mot-de-passe"
            cree = client.post(i18n.chemin(lang, "signup"), data={"email": email, "password": secret, "shop_name": "Porte", "platform": "shopify"})
            guard(cree.status_code == 200, f"inscription {lang} : {cree.status_code}")

            # 1. Sans session : la page de connexion de la langue, jamais le defi Basic.
            anonyme = client.get(compte, follow_redirects=False)
            guard(
                anonyme.status_code == 303 and anonyme.headers.get("location") == connexion and "www-authenticate" not in anonyme.headers,
                f"{compte} sans session : {anonyme.status_code} {anonyme.headers.get('www-authenticate')}",
            )
            page = client.get(connexion)
            guard(
                page.status_code == 200 and page.headers.get("cache-control") == "no-store" and 'content="noindex, nofollow"' in page.text,
                f"{connexion} n'est pas une page privee",
            )
            for interdit in ("<script", "{{", "<!--@"):
                guard(interdit not in page.text, f"{connexion} contient {interdit!r}")
            guard(f'method="post" action="{connexion}"' in page.text, f"{connexion} ne poste pas dans sa langue")

            # 2. Echecs : N mots de passe faux bloquent, meme le bon ensuite.
            for _ in range(reglages.login_max_failures):
                faux = client.post(connexion, data={"email": email, "password": "faux"}, follow_redirects=False)
                guard(faux.status_code == 400 and "set-cookie" not in faux.headers, f"{connexion} : un mot de passe faux obtient {faux.status_code}")
            bloque = client.post(connexion, data={"email": email, "password": secret}, follow_redirects=False)
            guard(
                bloque.status_code == 429 and 0 < int(bloque.headers.get("retry-after", "0")) <= reglages.login_lock_minutes * 60,
                f"{connexion} : apres {reglages.login_max_failures} echecs, le bon mot de passe obtient {bloque.status_code}",
            )
            ratelimit.reset()

            # 3. Formulaire d'un autre site : refuse, sans cookie.
            etranger = client.post(connexion, data={"email": email, "password": secret}, headers={"Origin": "https://pirate.example"}, follow_redirects=False)
            guard(etranger.status_code == 403 and "set-cookie" not in etranger.headers, f"{connexion} accepte un formulaire d'un autre site")

            # 4. Connexion : cookie sur, empreinte seule en base, espace ouvert.
            entree = client.post(connexion, data={"email": email, "password": secret}, follow_redirects=False)
            cookie = entree.headers.get("set-cookie", "")
            trouve = re.match(rf"{connexion_route.COOKIE}=([^;]+);", cookie)
            guard(entree.status_code == 303 and trouve is not None, f"{connexion} : connexion {entree.status_code}")
            for attribut in ("httponly", "secure", "path=/", "samesite=lax", f"max-age={reglages.session_hours * 3600}"):
                guard(attribut in cookie.lower(), f"cookie de session sans {attribut}")
            with fabrique() as session:
                empreintes = [ligne.token_hash for ligne in session.query(CustomerSession).all()]
            guard(
                hashlib.sha256(trouve.group(1).encode()).hexdigest() in empreintes and trouve.group(1) not in empreintes,
                "la base ne garde pas seulement l'empreinte du jeton de session",
            )
            espace = client.get(compte)
            guard(espace.status_code == 200 and espace.headers.get("cache-control") == "no-store", f"{compte} avec session : {espace.status_code}")

            # 5. Deconnexion : la copie du cookie ne rouvre rien.
            client.post(compte + "/deconnexion", follow_redirects=False)
            copie = TestClient(app, base_url="https://testserver", cookies={connexion_route.COOKIE: trouve.group(1)})
            guard(copie.get(compte, follow_redirects=False).status_code == 303, f"{compte} : un cookie de session ferme rouvre l'espace")

        # 6. Ce qui est montre est ce qui est applique (D48) : changer la configuration change la page.
        autre = reglages.model_copy(update={"session_hours": 7, "login_max_failures": 9, "login_lock_minutes": 23})
        connexion_route.get_settings = lambda: autre
        for lang in i18n.LANGUES:
            texte = connexion_route.page(lang).body.decode()
            for valeur in (7, 9, 23):
                guard(re.search(rf"(?<![\d.,]){valeur}(?![\d.,])", texte) is not None, f"connexion {lang} : {valeur} n'est pas affiche")
    finally:
        signup_route.SessionLocal, connexion_route.SessionLocal, connexion_route.get_settings = avant
        ratelimit.reset()
        moteur.dispose()
    guard("D62" in read("DECISIONS.md"), "la decision D62 n'est plus consignee dans DECISIONS.md")


def d63() -> None:
    """Mot de passe oublie : meme reponse compte connu ou non, lien sur l'adresse officielle, empreinte seule, une seule fois, sessions fermees."""
    import tempfile
    from pathlib import Path

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app import accounts, i18n, ratelimit
    from app.config import get_settings
    from app.main import app
    from app.models import Customer, CustomerSession, CustomerValidation, PasswordReset
    from app.routes import connexion as connexion_route
    from app.routes import mot_de_passe as route
    from app.routes import signup as signup_route

    reglages = get_settings()
    moteur = create_engine(f"sqlite:///{Path(tempfile.mkdtemp(prefix='porte-d63-')) / 'clients.sqlite'}")
    for modele in (Customer, CustomerValidation, CustomerSession, PasswordReset):
        modele.__table__.create(moteur)
    fabrique = sessionmaker(bind=moteur)
    mails: list = []
    modules = (signup_route, connexion_route, route)
    avant = [module.SessionLocal for module in modules], route.envoyer_mail, route.get_settings
    for module in modules:
        module.SessionLocal = fabrique
    route.envoyer_mail = lambda *mail: mails.append(mail)
    try:
        for lang in i18n.LANGUES:
            ratelimit.reset()
            mails.clear()
            client = TestClient(app, base_url="https://testserver")
            email, ancien, nouveau = f"porte-d63-{lang}@example.re", "porte-d63-ancien-mot", "porte-d63-nouveau-mot"
            client.post(i18n.chemin(lang, "signup"), data={"email": email, "password": ancien, "shop_name": "Porte", "platform": "shopify"})

            # 1. Meme reponse, mot pour mot, que le compte existe ou non ; un seul mail.
            connu = client.post(route.chemin_oubli(lang), data={"email": email}, headers={"Host": "pirate.example"})
            inconnu = client.post(route.chemin_oubli(lang), data={"email": f"inconnu-{lang}@example.re"})
            guard(connu.status_code == inconnu.status_code == 200 and connu.text == inconnu.text,
                  f"{route.chemin_oubli(lang)} : la reponse dit si le compte existe")
            guard(len(mails) == 1 and mails[0][0] == email, f"{route.chemin_oubli(lang)} : mails envoyes {[m[0] for m in mails]}")

            # 2. Le lien : adresse officielle (jamais Host), empreinte seule en base.
            prefixe = reglages.public_base_url.rstrip("/") + route.chemin_nouveau(lang) + "?jeton="
            trouve = re.search(re.escape(prefixe) + r"([\w-]+)", mails[0][2])
            guard(trouve is not None and "pirate.example" not in mails[0][2], f"lien {lang} : pas sur public_base_url")
            jeton = trouve.group(1)
            with fabrique() as session:
                empreintes = [ligne.token_hash for ligne in session.query(PasswordReset).all()]
            guard(accounts._empreinte_jeton(jeton) in empreintes and jeton not in empreintes,
                  f"lien {lang} : la base ne garde pas que l'empreinte")

            # 3. Nouveau mot de passe : connecte, ancien mot refuse, lien use.
            page = client.get(f"{route.chemin_nouveau(lang)}?jeton={jeton}")
            guard(page.status_code == 200 and page.headers.get("cache-control") == "no-store", f"lien {lang} : {page.status_code}")
            fait = client.post(route.chemin_nouveau(lang), data={"jeton": jeton, "password": nouveau, "password2": nouveau}, follow_redirects=False)
            guard(fait.status_code == 303 and connexion_route.COOKIE in fait.headers.get("set-cookie", ""), f"nouveau mot de passe {lang} : {fait.status_code}")
            refuse = client.post(connexion_route.chemin_connexion(lang), data={"email": email, "password": ancien}, follow_redirects=False)
            guard(refuse.status_code == 400, f"{lang} : l'ancien mot de passe ouvre encore le compte")
            reutilise = client.post(route.chemin_nouveau(lang), data={"jeton": jeton, "password": nouveau, "password2": nouveau}, follow_redirects=False)
            guard(reutilise.status_code == 400, f"{lang} : un lien deja servi marche encore")

            # 4. Formulaire d'un autre site : refuse, aucun mail.
            mails.clear()
            etranger = client.post(route.chemin_oubli(lang), data={"email": email}, headers={"Origin": "https://pirate.example"})
            guard(etranger.status_code == 403 and not mails, f"{route.chemin_oubli(lang)} accepte un formulaire d'un autre site")

        # 5. Ce qui est montre est ce qui est applique (D48) : la duree du lien vient de la configuration.
        autre = reglages.model_copy(update={"password_reset_minutes": 47})
        route.get_settings = lambda: autre
        for lang in i18n.LANGUES:
            guard(i18n.nombre(lang, 47) in route._oubli_page(lang).body.decode(), f"oubli {lang} : la duree du lien n'est pas celle de la configuration")
    finally:
        for module, session_local in zip(modules, avant[0]):
            module.SessionLocal = session_local
        route.envoyer_mail, route.get_settings = avant[1], avant[2]
        ratelimit.reset()
        moteur.dispose()
    guard("D63" in read("DECISIONS.md"), "la decision D63 n'est plus consignee dans DECISIONS.md")


CHECKS = {f"D{index}": globals()[f"d{index}"] for index in range(1, 64)}


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

