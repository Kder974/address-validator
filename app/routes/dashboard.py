"""GET /dashboard — tableau de bord d'exploitation (décision D32).

Trois exigences, dans cet ordre de priorité :

1. **Fermé par défaut.** Sans `DASHBOARD_PASSWORD`, la route répond `503` — pas
   `200`. Un tableau de bord public exposerait le chiffre d'affaires et les
   dernières transactions ; « oublié de mettre un mot de passe » doit fermer la
   porte, pas l'ouvrir.
2. **Autonome.** Aucune ressource externe (pas de CDN, pas de police distante,
   pas de script tiers) : la page fonctionne sur un serveur sans route sortante
   et ne fait fuiter aucune requête vers un tiers. Le graphique est un jeu de
   barres en CSS : ni image, ni police distante, ni script.
3. **Honnête.** Les chiffres viennent de `revenue_stats` (D31) ; si la base est
   muette, la page l'affiche au lieu d'un zéro trompeur.
4. **Séparé.** Le chiffre mis en avant est le revenu **réel** (D40) : les essais
   de recette sont exclus du revenu et affichés dans leur propre carte. Un
   tableau de bord d'exploitation qui gonflerait le chiffre d'affaires avec ses
   propres tests serait un instrument de mensonge.

La feuille de style vit dans `app/web.py` (D43), partagee avec les pages
publiques : une seule feuille, donc aucune derive possible entre
l'exploitation et le site commercial.

L'authentification est le couple `DASHBOARD_USER` / `DASHBOARD_PASSWORD` du
`.env`, comparé en temps constant (`secrets.compare_digest`). Depuis D67, comme
l'espace client (D62) : `LOGIN_MAX_FAILURES` mots de passe faux depuis une même
adresse IP bloquent `LOGIN_LOCK_MINUTES` minutes (`429` + `Retry-After`), sur
`/dashboard` et `/prospection` ensemble. Une requête sans identifiants (le premier
passage d'un navigateur) n'est pas un échec.
"""

from __future__ import annotations

import base64
import html
import secrets

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from app import ratelimit
from app.config import get_settings
from app.observability import client_ip
from app.routes.revenue import safe_revenue_stats
from app.web import DASHBOARD_CSS

router = APIRouter()

#: En-têtes communs aux trois réponses : une page d'exploitation (et un refus)
#: ne doit jamais rester dans un cache intermédiaire.
BASE_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}

#: La page est **autonome** : la CSP le rend vérifiable par le navigateur plutôt
#: que par la seule lecture du code. `style-src 'unsafe-inline'` est requis par
#: la feuille de style embarquée ; aucun script n'est autorisé.
PAGE_HEADERS = {
    **BASE_HEADERS,
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; "
        "form-action 'none'; frame-ancestors 'none'; base-uri 'none'"
    ),
}

UNAUTHORIZED_HEADERS = {**BASE_HEADERS, "WWW-Authenticate": 'Basic realm="address-validator"'}


def _decode_basic(header: str) -> tuple[str, str] | None:
    """Décode un en-tête `Authorization: Basic ...`. None si malformé."""
    if not header.lower().startswith("basic "):
        return None
    try:
        raw = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8")
    except Exception:  # noqa: BLE001 - tout en-tete illisible est un refus
        return None
    if ":" not in raw:
        return None
    user, _, password = raw.partition(":")
    return user, password


def _authorized(header: str, expected_user: str, expected_password: str) -> bool:
    credentials = _decode_basic(header)
    if credentials is None:
        return False
    user, password = credentials
    return secrets.compare_digest(user, expected_user) and secrets.compare_digest(
        password, expected_password
    )


def refus_acces(request: Request) -> Response | None:
    """`None` si l'identifiant et le mot de passe sont bons ; sinon le refus.

    `401` avec le défi Basic, ou `429` quand l'adresse IP a épuisé ses essais.
    L'appelant a déjà vérifié qu'un mot de passe est configuré (fermé par défaut).
    """
    reglages = get_settings()
    fenetre = reglages.login_lock_minutes * 60
    cle = f"exploitation:ip:{client_ip(request)}"
    if ratelimit.blocked(cle, reglages.login_max_failures, fenetre):
        attente = ratelimit.retry_after(cle, fenetre)
        return Response(
            status_code=429,
            content=f"Trop de mots de passe faux. Réessayez dans {max(1, (attente + 59) // 60)} min.",
            media_type="text/plain; charset=utf-8",
            headers={**BASE_HEADERS, "Retry-After": str(attente)},
        )
    entete = request.headers.get("authorization", "")
    if _authorized(entete, reglages.dashboard_user, reglages.dashboard_password):
        return None
    if entete:
        ratelimit.allow(cle, reglages.login_max_failures, fenetre)
    return Response(
        status_code=401,
        content="Authentification requise.",
        media_type="text/plain; charset=utf-8",
        headers=UNAUTHORIZED_HEADERS,
    )


def _bar(label: str, value: float, ceiling: float) -> str:
    width = 0 if ceiling <= 0 else max(2, round(100 * value / ceiling))
    return (
        f'<div class="bar"><span class="lbl">{html.escape(label)}</span>'
        f'<span class="track"><span class="fill" style="width:{width}%"></span></span>'
        f'<span class="val">{html.escape(f"{value:.2f}")} USDC</span></div>'
    )


def _origine(item: dict) -> str:
    """Origine d'une transaction : « recette, exclue » ou « client » (D40)."""
    return "recette, exclue" if item.get("test_mode") else "client"


def _page(stats: dict) -> str:
    """Construit la page. Aucune ressource externe, aucune donnee inventee."""
    ledger = stats.get("ledger", {})
    windows = stats.get("windows", {})
    recent = stats.get("recent", [])

    if ledger.get("available"):
        status = (
            f'<p class="ok">Registre PostgreSQL : disponible — '
            f'{ledger.get("total_validations_paid", ledger.get("validations_paid", 0))} '
            f'paiements enregistrés.</p>'
        )
    else:
        status = (
            f'<p class="ko">Registre indisponible : '
            f'{html.escape(str(ledger.get("error")))}<br>'
            f'Les chiffres ci-dessous sont donc <strong>incomplets</strong> '
            f'(seuls les compteurs de la session en cours restent).</p>'
        )

    def _reel(bloc: dict) -> float:
        """Revenu publiable d'un bloc (D40) ; repli sur l'alias D31."""
        return float(bloc.get("real_revenue_usdc", bloc.get("revenue_usdc", 0)) or 0)

    # D40 : le graphique montre le revenu **publiable**. La recette a sa propre
    # carte, chiffrée : elle n'est jamais mélangée au réel, ni visuellement.
    ceiling = max([_reel(w) for w in windows.values()] or [0])
    bars = "".join(
        _bar(name, _reel(windows.get(name, {})), ceiling)
        for name in ("24h", "7d", "30d")
    )

    if recent:
        rows = "".join(
            "<tr>"
            f'<td class="mono">{html.escape(str(item.get("tx_hash")))}</td>'
            f'<td>{html.escape(str(item.get("created_at")))}</td>'
            f'<td class="num">{item.get("amount_usdc", 0):.2f}</td>'
            f'<td>{html.escape(_origine(item))}</td>'
            "</tr>"
            for item in recent
        )
        table = (
            "<table><thead><tr><th>Preuve (tx_hash)</th><th>Horodatage</th>"
            "<th class=\"num\">USDC</th><th>Origine</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
        )
    else:
        table = '<p class="muted">Aucune transaction enregistrée.</p>'

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Address Validator — exploitation</title>
<style>
{DASHBOARD_CSS}</style>
</head>
<body>
<h1>Address Validator — exploitation</h1>
<p class="muted">Généré le {html.escape(str(stats.get("generated_at")))} ·
prix unitaire {html.escape(str(stats.get("price_per_validation_usdc")))} USDC ·
source {html.escape(str(stats.get("source")))}</p>
{status}
<div class="grid">
  <div class="card"><div class="k">Validations payées (réelles)</div>
    <div class="v">{ledger.get("real_validations_paid", ledger.get("validations_paid", 0))}</div></div>
  <div class="card"><div class="k">Revenu réel</div>
    <div class="v">{_reel(ledger):.2f} USDC</div></div>
  <div class="card"><div class="k">dont recette, exclue (D40)</div>
    <div class="v">{ledger.get("test_validations_paid", 0)} · {ledger.get("test_revenue_usdc", 0.0):.2f} USDC</div></div>
  <div class="card"><div class="k">Revenu total du registre</div>
    <div class="v">{ledger.get("total_revenue_usdc", ledger.get("revenue_usdc", 0.0)):.2f} USDC</div></div>
  <div class="card"><div class="k">Preuves uniques</div>
    <div class="v">{ledger.get("unique_proofs", 0)}</div></div>
  <div class="card"><div class="k">Clés d'adresse</div>
    <div class="v">{ledger.get("unique_address_keys", 0)}</div></div>
</div>
<h2 style="font-size:15px">Revenu par fenêtre</h2>
{bars}
<h2 style="font-size:15px">Dernières transactions</h2>
{table}
<footer>Protégé par HTTP Basic Auth (DASHBOARD_USER / DASHBOARD_PASSWORD).
Aucune ressource externe. Les adresses IP ne figurent pas ici : cf. /metrics/revenue.
Les essais de recette (D40) sont exclus du revenu réel et comptés à part.
Pays des visites et des appels (D67) : IP Geolocation by DB-IP (db-ip.com), licence CC BY 4.0.</footer>
</body>
</html>
"""


@router.get("/dashboard")
async def dashboard(request: Request) -> Response:
    settings = get_settings()
    if not settings.dashboard_password:
        return JSONResponse(
            status_code=503,
            headers=BASE_HEADERS,
            content={
                "error": "dashboard disabled",
                "detail": "DASHBOARD_PASSWORD absent : /dashboard est fermé par défaut (D32).",
            },
        )
    refus = refus_acces(request)
    if refus is not None:
        return refus
    stats = await safe_revenue_stats(include_recent=True)
    return HTMLResponse(content=_page(stats), status_code=200, headers=PAGE_HEADERS)
