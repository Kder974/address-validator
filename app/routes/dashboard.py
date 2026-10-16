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

import asyncio
import base64
import html
import secrets

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from app import accounts, i18n, ratelimit
from app.config import get_settings
from app.exploitation import PERIODES, collecter, maintenant
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


#: Thème du pilotage (D68) : sombre, aux couleurs du site, animé en CSS seul (la
#: CSP n'autorise aucun script). `prefers-reduced-motion` coupe les animations.
PILOTE_CSS = """
.pilote{--c1:#ff8a3d;--c2:#5b7cff;--c3:#2fd4c4;--ok:#34d399;--ko:#fb7185;--line:rgba(140,165,255,.16);--panel:rgba(18,26,48,.72);
 background:#060b18 radial-gradient(1200px 600px at 10% -10%,rgba(91,124,255,.18),transparent 60%) no-repeat;min-height:100vh;padding:20px clamp(14px,3vw,36px) 40px}
.pilote::before{content:"";position:fixed;inset:0;pointer-events:none;z-index:-1;background-image:linear-gradient(rgba(140,165,255,.06) 1px,transparent 1px),linear-gradient(90deg,rgba(140,165,255,.06) 1px,transparent 1px);background-size:44px 44px;-webkit-mask-image:radial-gradient(ellipse at 50% 0,#000 30%,transparent 75%);mask-image:radial-gradient(ellipse at 50% 0,#000 30%,transparent 75%);animation:p-grille 40s linear infinite}
@keyframes p-grille{to{background-position:44px 44px}}
.p-hud{position:relative;display:flex;flex-wrap:wrap;align-items:center;gap:12px 20px;padding:14px 18px;border:1px solid var(--line);border-radius:16px;background:var(--panel);overflow:hidden}
.p-hud::after{content:"";position:absolute;inset:0;pointer-events:none;background:linear-gradient(90deg,transparent,rgba(47,212,196,.12),transparent);transform:translateX(-100%);animation:p-balayage 6s ease-in-out infinite}
@keyframes p-balayage{to{transform:translateX(100%)}}
.p-marque{display:flex;align-items:center;gap:10px;margin:0;font-size:16px;font-weight:650;letter-spacing:.02em}
.p-marque b{background:linear-gradient(90deg,var(--c1),var(--c2),var(--c3));-webkit-background-clip:text;background-clip:text;color:transparent}
.p-orbe{width:14px;height:14px;border-radius:50%;background:conic-gradient(var(--c1),var(--c2),var(--c3),var(--c1));box-shadow:0 0 14px rgba(91,124,255,.6);animation:p-tour 6s linear infinite}
@keyframes p-tour{to{transform:rotate(1turn)}}
.p-live{display:flex;align-items:center;gap:8px;font-size:12.5px;color:#9fb0d4}
.p-point{width:8px;height:8px;border-radius:50%;background:var(--ok);animation:p-pouls 1.8s infinite}
@keyframes p-pouls{0%{box-shadow:0 0 0 0 rgba(52,211,153,.6)}70%,100%{box-shadow:0 0 0 9px rgba(52,211,153,0)}}
.p-periodes{margin-left:auto;display:flex;flex-wrap:wrap;gap:6px}
.p-periodes a{padding:6px 12px;border:1px solid var(--line);border-radius:999px;color:#c9d4ee;font-size:13px;text-decoration:none}
.p-periodes a[aria-current]{background:linear-gradient(90deg,rgba(91,124,255,.4),rgba(47,212,196,.28));border-color:transparent;color:#fff}
.p-titre{display:flex;align-items:center;gap:10px;margin:30px 0 12px;font-size:13px;letter-spacing:.18em;text-transform:uppercase;color:#8ea2cc}
.p-titre::after{content:"";flex:1;height:1px;background:linear-gradient(90deg,var(--line),transparent)}
.p-grille{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,200px),1fr));gap:12px}
.p-carte,.p-panneau{position:relative;padding:14px 16px;border:1px solid var(--line);border-radius:14px;background:var(--panel);animation:p-monte .6s cubic-bezier(.2,.7,.2,1) both;animation-delay:calc(var(--i,0)*55ms)}
.p-carte::before{content:"";position:absolute;inset:-1px;padding:1px;border-radius:inherit;pointer-events:none;opacity:.4;background:linear-gradient(130deg,var(--c1),var(--c2),var(--c3));-webkit-mask:linear-gradient(#000 0 0) content-box,linear-gradient(#000 0 0);-webkit-mask-composite:xor;mask-composite:exclude}
@keyframes p-monte{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
.p-k{font-size:11.5px;letter-spacing:.08em;text-transform:uppercase;color:#8ea2cc}
.p-v{margin-top:4px;font-size:clamp(20px,2.4vw,26px);font-weight:650;font-variant-numeric:tabular-nums}
.p-s{margin-top:2px;font-size:12.5px;color:#9fb0d4}
.p-vert .p-v{color:var(--ok);text-shadow:0 0 18px rgba(52,211,153,.35)}
.p-rouge .p-v{color:var(--ko);text-shadow:0 0 18px rgba(251,113,133,.35)}
.p-panneau{margin-top:12px}
.p-panneau h3{margin:0 0 10px;font-size:13px;font-weight:600;color:#c9d4ee}
.p-duo{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,320px),1fr));gap:12px}
.p-ligne{display:grid;grid-template-columns:minmax(0,11em) 1fr auto;align-items:center;gap:10px;margin:7px 0;font-size:13px}
.p-piste{height:8px;overflow:hidden;border-radius:99px;background:rgba(140,165,255,.12)}
.p-jauge{display:block;height:100%;border-radius:inherit;background:linear-gradient(90deg,var(--c2),var(--c3));transform-origin:left;animation:p-pousse 1s cubic-bezier(.2,.7,.2,1) both}
@keyframes p-pousse{from{transform:scaleX(0)}}
.p-courbe{display:block;width:100%;height:120px}
.p-courbe .trait{fill:none;stroke-width:2;vector-effect:non-scaling-stroke}
.p-courbe .trait,.p-courbe polygon{animation:p-revele 1.6s ease-out both}
@keyframes p-revele{from{clip-path:inset(0 100% 0 0)}}
.p-alerte{margin:12px 0 0;padding:12px 14px;border:1px solid rgba(251,113,133,.35);border-radius:12px;background:rgba(251,113,133,.1);color:#ffd1d9;animation:p-alarme 2.4s ease-in-out infinite}
@keyframes p-alarme{50%{box-shadow:0 0 22px rgba(251,113,133,.25)}}
.p-calme{margin:12px 0 0;padding:10px 14px;border:1px solid rgba(52,211,153,.25);border-radius:12px;background:rgba(52,211,153,.08);color:#bdf5dc}
.p-puces{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}
.p-puce{display:inline-flex;align-items:center;gap:7px;padding:6px 11px;border:1px solid var(--line);border-radius:999px;background:rgba(10,16,32,.6);font-size:12.5px}
.p-puce i{width:7px;height:7px;border-radius:50%;background:var(--ok);box-shadow:0 0 8px var(--ok)}
.p-puce.ko i{background:var(--ko);box-shadow:0 0 8px var(--ko)}
.p-liste{margin:0;padding:0;list-style:none;font-size:13px}
.p-liste li{display:flex;justify-content:space-between;gap:10px;padding:7px 0;border-bottom:1px solid rgba(140,165,255,.08)}
.p-vide{color:#7f90b6;font-size:13px}
@media (prefers-reduced-motion:reduce){.pilote *,.pilote::before,.pilote *::before,.pilote *::after{animation:none!important}}
"""


def _e(valeur) -> str:
    return html.escape(str(valeur))


def _fr(valeur: float, decimales: int = 2) -> str:
    return f"{valeur:,.{decimales}f}".replace(",", " ").replace(".", ",")


def _euros(valeur) -> str:
    return "—" if valeur is None else f"{_fr(valeur)} €"


def _pct(valeur) -> str:
    return "—" if valeur is None else f"{_fr(100 * valeur, 0)} %"


def _pays(code: str) -> str:
    """« 🇫🇷 FR », ou « Inconnu » : le drapeau est fait des deux lettres du code."""
    if not code or code == "??" or len(code) != 2 or not code.isalpha():
        return "Inconnu"
    return "".join(chr(0x1F1E6 + ord(c) - 65) for c in code.upper()) + " " + code.upper()


def _pluriel(n: int, un: str, plusieurs: str) -> str:
    return f"{n} {un if n == 1 else plusieurs}"


def _kpi(titre: str, valeur: str, sous: str = "", ton: str = "", i: int = 0) -> str:
    classe = f"p-carte {ton}".strip()
    return (f'<div class="{classe}" style="--i:{i}"><div class="p-k">{_e(titre)}</div>'
            f'<div class="p-v">{_e(valeur)}</div>{f"<div class=p-s>{_e(sous)}</div>" if sous else ""}</div>')


def _barres(titre: str, lignes: list, format_valeur, vide: str) -> str:
    plafond = max([v for _, v in lignes] or [0])
    if not lignes or plafond <= 0:
        corps = f'<p class="p-vide">{_e(vide)}</p>'
    else:
        corps = "".join(
            f'<div class="p-ligne"><span>{_e(nom)}</span><span class="p-piste"><span class="p-jauge" '
            f'style="width:{max(2, round(100 * v / plafond))}%"></span></span><span>{_e(format_valeur(v))}</span></div>'
            for nom, v in lignes
        )
    return f'<div class="p-panneau"><h3>{_e(titre)}</h3>{corps}</div>'


def _courbe(nom: str, valeurs: list[float], c1: str, c2: str) -> str:
    """Courbe des 30 derniers jours, en SVG, qui se dessine à l'ouverture."""
    haut = max(valeurs or [0]) or 1
    pas = 300 / max(len(valeurs) - 1, 1)
    points = " ".join(f"{k * pas:.1f},{96 - 88 * v / haut:.1f}" for k, v in enumerate(valeurs))
    return (f'<svg class="p-courbe" viewBox="0 0 300 100" preserveAspectRatio="none" role="img" aria-label="{_e(nom)}">'
            f'<defs><linearGradient id="g-{nom}" x1="0" x2="1"><stop offset="0" stop-color="{c1}"/><stop offset="1" stop-color="{c2}"/></linearGradient>'
            f'<linearGradient id="a-{nom}" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="{c2}" stop-opacity=".28"/><stop offset="1" stop-color="{c2}" stop-opacity="0"/></linearGradient></defs>'
            f'<polygon points="0,100 {points} 300,100" fill="url(#a-{nom})"/>'
            f'<polyline class="trait" points="{points}" stroke="url(#g-{nom})"/></svg>')


def _pilotage(d: dict, sante: dict) -> str:
    """Les écrans argent, API et clients (D68), au-dessus du registre des paiements."""
    from app.exploitation import PERIODES

    periodes = "".join(
        f'<a href="?p={cle}"{" aria-current=page" if cle == d.get("periode") else ""}>{_e(lib)}</a>'
        for cle, (lib, _jours) in PERIODES.items()
    )
    heure = d["moment"].strftime("%H:%M") if d.get("moment") else "—"
    tete = (f'<header class="p-hud"><h1 class="p-marque"><span class="p-orbe"></span>Address Validator <b>Pilotage</b></h1>'
            f'<div class="p-live"><span class="p-point"></span>En direct · {heure} UTC · actualisé chaque minute</div>'
            f'<nav class="p-periodes" aria-label="Période">{periodes}</nav></header>')
    puces = "".join(
        f'<span class="p-puce{"" if ok else " ko"}"><i></i>{_e(nom)} : {"OK" if ok else "en panne"}</span>'
        for nom, ok in sante.items()
    )
    if "erreur" in d:
        return (tete + f'<p class="p-alerte">Chiffres indisponibles : la base ne répond pas ({_e(d["erreur"])}).</p>'
                f'<div class="p-puces">{puces}</div>')
    alertes = "".join(f'<p class="p-alerte">{_e(a)}</p>' for a in d["alertes"]) or '<p class="p-calme">Tout va bien : aucune alerte.</p>'

    a, api, cl = d["argent"], d["api"], d["clients"]
    taux = f"1 € = {_fr(float(i18n.TAUX['USD']))} $ ({i18n.DATE_TAUX})"
    resultat = a["resultat_mois_eur"]
    abos = ", ".join(f"{n} {accounts.PLAN_LIBELLES[p]}" for p, n in a["abonnements"].items() if n) or "aucun abonné"
    argent = "".join([
        _kpi("Encaissé · " + PERIODES[d["periode"]][0], f'{_fr(a["usdc_periode"])} USDC', f'≈ {_euros(a["eur_periode"])}', i=0),
        _kpi("Abonnements à facturer", _euros(a["a_facturer_eur"]) + " / mois", abos, i=1),
        _kpi("Coûts du mois", _euros(a["couts_mois_eur"]) if a["couts_renseignes"] else "à renseigner",
             _pluriel(a["appels_fournisseur_mois"], "appel au fournisseur", "appels au fournisseur") if a["couts_renseignes"] else "COST_* dans .env", i=2),
        _kpi("Résultat du mois", _euros(resultat) if resultat is not None else "non calculé",
             "revenus moins coûts" if resultat is not None else "il faut les coûts",
             "" if resultat is None else ("p-vert" if resultat >= 0 else "p-rouge"), i=3),
        _kpi("Prévision fin de mois", _euros(a["prevision_resultat_eur"] if a["prevision_resultat_eur"] is not None else a["prevision_revenus_eur"]),
             "résultat au rythme actuel" if a["prevision_resultat_eur"] is not None else "revenus au rythme actuel", i=4),
    ])
    statuts = api["statuts"]
    api_cartes = "".join([
        _kpi("Appels · " + PERIODES[d["periode"]][0], _fr(api["total"], 0), f'{_pct(api["taux_reussite"])} réussis', i=0),
        _kpi("Refusés sans paiement", _fr(statuts.get("paiement", 0), 0), "agents qui ont trouvé le service", i=1),
        _kpi("Erreurs", _fr(statuts.get("client", 0) + statuts.get("serveur", 0), 0), f'dont {statuts.get("serveur", 0)} côté serveur',
             "p-rouge" if statuts.get("serveur", 0) else "", i=2),
        _kpi("Temps de réponse", "—" if api["duree_moyenne_ms"] is None else f'{api["duree_moyenne_ms"]} ms',
             "" if api["duree_p95_ms"] is None else f'95 % sous {api["duree_p95_ms"]} ms', i=3),
        _kpi("Cache", _pct(api["taux_cache"]), "économie " + _euros(api["economie_cache_eur"]) if api["economie_cache_eur"] is not None else "appels évités au fournisseur", i=4),
        _kpi("Résultats dégradés", _fr(api["degrades"], 0), "fournisseur muet, à rejouer", i=5),
    ])
    payants = cl["payants"]
    clients_cartes = "".join([
        _kpi("Inscrits · " + PERIODES[d["periode"]][0], _fr(cl["inscrits_periode"], 0), f'{cl["total"]} comptes en tout', i=0),
        _kpi("Essais en cours", _fr(cl["essais_en_cours"], 0), f'{len(cl["essais_finissant"])} finissent sous 3 jours', i=1),
        _kpi("Clients payants", _fr(sum(payants.values()), 0), ", ".join(f"{n} {accounts.PLAN_LIBELLES[p]}" for p, n in payants.items()) or "aucun pour l'instant", i=2),
        _kpi("Essai → payant", _pct(cl["conversion"]), "part des comptes devenus payants", i=3),
    ])

    def liste(titre, elements, vide):
        corps = "".join(f"<li><span>{_e(g)}</span><span>{_e(dr)}</span></li>" for g, dr in elements)
        return f'<div class="p-panneau"><h3>{_e(titre)}</h3>' + (f'<ul class="p-liste">{corps}</ul>' if corps else f'<p class="p-vide">{_e(vide)}</p>') + "</div>"

    return "".join([
        tete, alertes, f'<div class="p-puces">{puces}</div>',
        '<h2 class="p-titre">Argent</h2>', f'<div class="p-grille">{argent}</div>',
        f'<div class="p-panneau"><h3>Encaissé jour par jour · 30 jours (USDC)</h3>{_courbe("encaisse", a["courbe_usdc"], "#ff8a3d", "#5b7cff")}</div>',
        '<div class="p-duo">',
        _barres("Revenu par pays (€)", [(_pays(c), v) for c, v in a["par_pays_eur"]], _euros, "Pas encore de revenu rattaché à un pays."),
        _barres("Revenu par canal (€)", a["par_canal_eur"], _euros, "Pas encore de revenu."),
        "</div>",
        f'<p class="p-vide">USDC converti au taux des prix du site : {_e(taux)}. Abonnements : à facturer, pas encaissés automatiquement.</p>',
        '<h2 class="p-titre">API</h2>', f'<div class="p-grille">{api_cartes}</div>',
        f'<div class="p-panneau"><h3>Appels jour par jour · 30 jours</h3>{_courbe("appels", api["courbe_appels"], "#5b7cff", "#2fd4c4")}</div>',
        '<div class="p-duo">',
        _barres("Par où arrivent les appels", api["canaux"], lambda v: _fr(v, 0), "Aucun appel sur la période."),
        _barres("Pays d'où viennent les appels", [(_pays(c), v) for c, v in api["pays_appels"]], lambda v: _fr(v, 0), "Aucun appel sur la période."),
        _barres("Pays des adresses vérifiées", [(_pays(c), v) for c, v in api["pays_adresses"] if c != "??"], lambda v: _fr(v, 0), "Aucune adresse vérifiée sur la période."),
        "</div>",
        '<h2 class="p-titre">Clients</h2>', f'<div class="p-grille">{clients_cartes}</div>',
        '<div class="p-duo">',
        liste("Essais qui finissent sous 3 jours", [(n, f.strftime("%d/%m %H:%M")) for n, f in cl["essais_finissant"]], "Aucun essai ne finit bientôt."),
        liste("Presque au bout du quota", [(n, f"{c} / {q}") for n, _p, c, q in cl["proches_quota"]], "Personne n'est proche de son quota."),
        liste("Inscrits qui n'ont jamais appelé l'API", [(n, "à relancer") for n in cl["jamais_appele"][:8]], "Tous les inscrits ont déjà appelé l'API."),
        liste("Ceux qui utilisent le plus le service (ce mois)", [(n, f"{c} validations") for n, _p, c in cl["top"]], "Aucune validation ce mois-ci."),
        "</div>",
    ])


def _origine(item: dict) -> str:
    """Origine d'une transaction : « recette, exclue » ou « client » (D40)."""
    return "recette, exclue" if item.get("test_mode") else "client"


def _page(stats: dict, pilotage: dict | None = None, sante: dict | None = None) -> str:
    """Construit la page. Aucune ressource externe, aucune donnee inventee.

    Avec `pilotage` (D68), les écrans argent, API et clients passent en tête, la
    page se rafraîchit chaque minute et le registre des paiements suit.
    """
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
{'<meta http-equiv="refresh" content="60">' if pilotage is not None else ""}
<style>
{DASHBOARD_CSS}{PILOTE_CSS if pilotage is not None else ""}</style>
</head>
<body{' class="pilote"' if pilotage is not None else ""}>
{_pilotage(pilotage, sante or {}) + '<h2 class="p-titre">Registre des paiements</h2>' if pilotage is not None else "<h1>Address Validator — exploitation</h1>"}
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
    periode = request.query_params.get("p", "mois")
    pilotage, sante = await _pilotage_donnees(periode if periode in PERIODES else "mois", stats)
    return HTMLResponse(content=_page(stats, pilotage, sante), status_code=200, headers=PAGE_HEADERS)


def _redis_ok() -> bool:
    try:
        from app.cache import redis_client

        return bool(redis_client.ping())
    except Exception:  # noqa: BLE001 - un cache muet est un état à afficher, pas une erreur
        return False


async def _pilotage_donnees(periode: str, stats: dict) -> tuple[dict, dict]:
    """Les chiffres du pilotage (D68), lus hors de la boucle et bornés à 5 s."""
    from app.database import SessionLocal

    def lire() -> dict:
        with SessionLocal() as session:
            return collecter(session, maintenant(), periode, get_settings())

    try:
        donnees = await asyncio.wait_for(asyncio.to_thread(lire), timeout=5)
    except Exception as exc:  # noqa: BLE001 - base muette : la page le dit
        donnees = {"erreur": type(exc).__name__, "periode": periode, "moment": maintenant()}
    sante = {
        "Base de données": "erreur" not in donnees,
        "Cache Redis": await asyncio.to_thread(_redis_ok),
        "Registre des paiements": bool(stats.get("ledger", {}).get("available")),
    }
    return donnees, sante
