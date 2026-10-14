"""Coquille HTML et feuille de styles partagées par les pages servies en HTML.

Deux régimes, un seul dessin. Le dessin (couleurs, cartes, tableau) vient de la
page d'exploitation `/dashboard` (D32) : `DASHBOARD_CSS` a été **extrait
textuellement** de `app/routes/dashboard.py` le 12/10/2026 (1441 octets,
`sha256=3437cb4b9d55396e9e35f985706ab8c18f848bac38c5f082f86f6476c9afe3fa`) et
c'est désormais **ce module** qui en est la source : `dashboard.py` l'importe.
Une seule feuille, donc aucune dérive possible entre l'exploitation et le site
commercial.

Ce qui diffère, en revanche, est délibéré et mesurable :

  * `/dashboard` (D32) est une page d'**exploitation** : fermée par défaut,
    `Cache-Control: no-store`, `robots: noindex` ;
  * les pages **publiques** (D43 : `/pricing`, puis `/signup` et
    `/validate-form`) doivent au contraire être indexables et servies sans
    compte — mais elles restent **autonomes** : aucune ressource externe (pas de
    CDN, pas de police distante, pas de script). La FAQ utilise `<details>`, qui
    fonctionne sans JavaScript. La CSP le rend vérifiable par le navigateur
    plutôt que par la seule lecture du code. Seule ouverture (D57) : les photos
    du site, servies par l'application elle-même sous `/images/`
    (`img-src 'self'`) ; aucune image d'un tiers.

`public_headers()` porte donc la seule différence autorisée entre une page
publique et une page d'exploitation : la politique de cache, `img-src 'self'`
et `form-action` (`'none'` sur une page sans formulaire, `'self'` sur `/signup`
et `/validate-form`).
"""

from __future__ import annotations

import html

#: Feuille de style de `/dashboard` (D32), importée par `app/routes/dashboard.py`.
DASHBOARD_CSS = """  :root { color-scheme: light dark; }
  body { font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
         margin: 0; padding: 24px; background: #0f1115; color: #e6e8eb; }
  h1 { font-size: 20px; margin: 0 0 4px; }
  .muted, .muted * { color: #9aa4b2; }
  .grid { display: flex; flex-wrap: wrap; gap: 12px; margin: 18px 0; }
  .card { background: #171a21; border: 1px solid #242833; border-radius: 10px;
           padding: 12px 16px; min-width: 168px; }
  .card .k { font-size: 12px; text-transform: uppercase; letter-spacing: .04em;
              color: #9aa4b2; }
  .card .v { font-size: 22px; font-weight: 600; }
  .ok { color: #6ee7a8; }
  .ko { color: #ffb4a2; }
  .bar { display: flex; align-items: center; gap: 10px; margin: 6px 0; }
  .bar .lbl { width: 42px; color: #9aa4b2; }
  .bar .track { flex: 1; height: 12px; background: #242833; border-radius: 6px; }
  .bar .fill { display: block; height: 12px; border-radius: 6px; background: #4f8cff; }
  .bar .val { width: 110px; text-align: right; }
  table { width: 100%; border-collapse: collapse; margin-top: 10px; }
  th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #242833; }
  th { font-size: 12px; text-transform: uppercase; color: #9aa4b2; }
  .num { text-align: right; }
  .mono { font-family: ui-monospace, "Cascadia Mono", Consolas, monospace;
           font-size: 12px; }
  footer { margin-top: 22px; font-size: 12px; color: #9aa4b2; }
"""

#: Composants du site public (D43) : héros, offres, étapes, FAQ. Ils n'utilisent
#: que les jetons de `DASHBOARD_CSS` (mêmes fonds, mêmes bordures, même accent).
SITE_CSS = """  .site { max-width: 980px; margin: 0 auto; padding: 0 20px; }
  .site-head { display: flex; align-items: center; gap: 16px; flex-wrap: wrap;
               padding: 14px 0; border-bottom: 1px solid #242833; }
  .brand { font-weight: 600; }
  .brand span { color: #4f8cff; }
  .site-head nav { margin-left: auto; display: flex; gap: 14px; flex-wrap: wrap; }
  a { color: #4f8cff; }
  .hero { padding: 30px 0 6px; }
  .hero h1 { font-size: 30px; line-height: 1.22; margin: 0 0 10px; max-width: 720px; }
  .lead { font-size: 17px; color: #9aa4b2; max-width: 680px; margin: 0 0 14px; }
  .actions { display: flex; gap: 10px; flex-wrap: wrap; margin: 16px 0 6px; }
  .btn { display: inline-block; padding: 10px 16px; border-radius: 8px;
         font-weight: 600; text-decoration: none; color: #e6e8eb;
         border: 1px solid #242833; background: #171a21; }
  .btn.primary { background: #4f8cff; border-color: #4f8cff; color: #0f1115; }
  .btn.ghost { background: transparent; }
  section { margin: 28px 0; }
  h2.sec { font-size: 19px; margin: 0 0 6px; }
  h3.sub { font-size: 15px; margin: 0 0 4px; }
  .cols { display: flex; flex-wrap: wrap; gap: 12px; }
  .col { flex: 1 1 220px; background: #171a21; border: 1px solid #242833;
         border-radius: 10px; padding: 14px 16px; }
  .n { display: inline-block; width: 22px; height: 22px; line-height: 22px;
       text-align: center; border-radius: 50%; background: #4f8cff;
       color: #0f1115; font-size: 12px; font-weight: 700; margin-bottom: 6px; }
  .plans { display: flex; flex-wrap: wrap; gap: 12px; align-items: stretch; }
  .plan { flex: 1 1 208px; display: flex; flex-direction: column;
          background: #171a21; border: 1px solid #242833; border-radius: 10px;
          padding: 16px; }
  .plan.featured { border-color: #4f8cff; }
  .tag { font-size: 12px; text-transform: uppercase; letter-spacing: .04em;
         color: #9aa4b2; }
  .price { font-size: 26px; font-weight: 700; margin: 6px 0 2px; }
  .per { font-size: 12px; color: #9aa4b2; }
  .plan ul { list-style: none; padding: 0; margin: 12px 0; font-size: 14px; }
  .plan li { padding: 3px 0 3px 20px; position: relative; }
  .plan li::before { content: "\\2713"; position: absolute; left: 0; color: #6ee7a8; }
  .plan .btn { margin-top: auto; text-align: center; }
  blockquote { margin: 0 0 10px; font-style: italic; }
  .who { font-size: 12px; color: #9aa4b2; }
  details.faq { border-bottom: 1px solid #242833; padding: 10px 0; }
  details.faq summary { cursor: pointer; font-weight: 600; }
  details.faq p { color: #9aa4b2; margin: 8px 0 0; }
  .note { background: #171a21; border: 1px solid #242833;
          border-left: 3px solid #4f8cff; border-radius: 8px;
          padding: 12px 14px; font-size: 13px; color: #9aa4b2; }
  .note strong { color: #e6e8eb; }
  .site-foot { margin-top: 30px; padding: 16px 0; border-top: 1px solid #242833;
               font-size: 12px; color: #9aa4b2; display: flex; gap: 14px;
               flex-wrap: wrap; }

  @media (max-width: 720px) {
    .hero h1 { font-size: 24px; }
    .site-head nav { margin-left: 0; }
  }
"""

#: Feuille servie par les pages publiques : le dessin du dashboard, étendu.
PUBLIC_CSS = DASHBOARD_CSS + SITE_CSS

#: En-têtes communs à toute réponse HTML publique (D43) : rien à renifler, aucune
#: fuite de référent vers un tiers.
BASE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


def public_headers(*, form: bool = False, max_age: int = 300) -> dict:
    """En-têtes d'une page publique.

    `form=True` n'ouvre que `form-action 'self'` : un formulaire de `/signup` ou
    de `/validate-form` ne peut pas poster ailleurs. Une page sans formulaire
    reste à `form-action 'none'` — le refus le plus large possible.
    """
    form_action = "'self'" if form else "'none'"
    return {
        **BASE_HEADERS,
        # Une page commerciale peut être mise en cache quelques minutes par le
        # proxy : elle ne contient ni donnée client, ni identifiant.
        "Cache-Control": f"public, max-age={max_age}",
        "Content-Security-Policy": (
            "default-src 'none'; style-src 'unsafe-inline'; img-src 'self'; "
            f"form-action {form_action}; "
            "frame-ancestors 'none'; base-uri 'none'"
        ),
    }


#: En-têtes d'une réponse **privée** : une page qui contient, ou vient de
#: contenir, un secret ou une donnée client. Jamais de cache — un proxy qui
#: garderait la réponse d'un `POST /validate-form` garderait l'adresse du
#: visiteur —, jamais d'indexation, et le refus le plus large possible.
#: La définition vit ici, une seule fois : `/signup` (clé API, D44) et
#: `/validate-form` (adresse validée, D46) doivent dire la même chose.
PRIVATE_HEADERS = {
    **BASE_HEADERS,
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
        "frame-ancestors 'none'; base-uri 'none'"
    ),
}


def site_head() -> str:
    """En-tête commun des pages publiques : **une** navigation, pas trois copies.

    Recopier la navigation dans chaque page l'aurait fait diverger à la première
    route ajoutée (`/compte` est arrivé après `/pricing`).
    """
    return (
        '<header class="site-head">'
        '<div class="brand">Address <span>Validator</span></div>'
        "<nav>"
        '<a href="/pricing">Tarifs</a>'
        '<a href="/guide">Guide</a>'
        '<a href="/validate-form">Tester une adresse</a>'
        '<a href="/compte">Espace client</a>'
        '<a href="/signup">Créer un compte</a>'
        "</nav>"
        "</header>"
    )


def document(
    title: str,
    body: str,
    *,
    description: str = "",
    robots: str = "index, follow",
    css: str = PUBLIC_CSS,
) -> str:
    """Assemble une page HTML complète, autonome et sans ressource externe."""
    meta = ""
    if description:
        meta += f'<meta name="description" content="{html.escape(description, quote=True)}">\n'
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="{html.escape(robots, quote=True)}">
{meta}<title>{html.escape(title)}</title>
<style>
{css}</style>
</head>
<body>
{body}
</body>
</html>
"""

