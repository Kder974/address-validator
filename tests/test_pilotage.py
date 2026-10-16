"""Tests de la page de connexion et de la session du pilotage (décision D69).

Ce qui est mesuré, et pourquoi :

  * un navigateur sans session arrive sur la vraie page de connexion (plus la
    fenêtre grise) ; un script garde le défi Basic (D32) ;
  * le bon couple pose un cookie `__Host-` HttpOnly, Secure, SameSite=Strict, qui
    ouvre le tableau de bord ; un cookie retouché, échu ou d'avant un changement de
    mot de passe ne l'ouvre pas ;
  * cinq mots de passe faux bloquent l'adresse IP, même au bon mot de passe ;
  * un formulaire d'un autre site est refusé ; la déconnexion efface le cookie.
"""

from html import unescape

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.routes import dashboard

client = TestClient(app)
NAVIGATEUR = {"Accept": "text/html,application/xhtml+xml"}


@pytest.fixture
def reglages(monkeypatch):
    r = get_settings()
    monkeypatch.setattr(r, "dashboard_user", "kder")
    monkeypatch.setattr(r, "dashboard_password", "mot-de-passe-du-pilotage")
    return r


def _ip(n: int) -> dict:
    return {"X-Forwarded-For": f"198.18.0.{n}"}


def _connexion(ip: dict, user="kder", password="mot-de-passe-du-pilotage", **entetes):
    return client.post("/dashboard/connexion", data={"user": user, "password": password},
                       headers={**ip, **entetes}, follow_redirects=False)


def test_un_navigateur_arrive_sur_la_page_de_connexion(reglages):
    reponse = client.get("/dashboard", headers={**NAVIGATEUR, **_ip(1)}, follow_redirects=False)
    assert reponse.status_code == 303 and reponse.headers["location"] == "/dashboard/connexion"
    page = client.get("/dashboard/connexion")
    assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
    assert 'name="user"' in page.text and 'type="password"' in page.text and "noindex" in page.text
    assert "<script" not in page.text.lower() and "5 essais, puis 15 minutes" in page.text
    # Un script garde le défi Basic.
    assert client.get("/dashboard", headers=_ip(1)).status_code == 401


def test_le_bon_couple_pose_une_session_qui_ouvre_le_tableau_de_bord(reglages):
    reponse = _connexion(_ip(2))
    assert reponse.status_code == 303 and reponse.headers["location"] == "/dashboard"
    cookie = reponse.headers["set-cookie"]
    for attribut in ("__Host-av_pilotage=", "HttpOnly", "Secure", "SameSite=strict", "Path=/"):
        assert attribut.lower() in cookie.lower()
    jeton = cookie.split("=", 1)[1].split(";", 1)[0]
    page = client.get("/dashboard", headers={**NAVIGATEUR, **_ip(2), "Cookie": f"{dashboard.COOKIE}={jeton}"})
    assert page.status_code == 200 and "Pilotage" in page.text and "<script" not in page.text.lower()
    assert "form-action 'self'" in page.headers["content-security-policy"]


def test_un_cookie_retouche_echu_ou_d_avant_le_changement_de_mot_de_passe_ne_vaut_rien(reglages, monkeypatch):
    jeton = dashboard.jeton_session(reglages)
    assert dashboard.session_valide(jeton, reglages)
    fin, signature = jeton.split(".")
    assert not dashboard.session_valide(f"{int(fin) + 3600}.{signature}", reglages)
    assert not dashboard.session_valide(jeton, reglages, maintenant=int(fin) + 1)
    assert not dashboard.session_valide("", reglages) and not dashboard.session_valide("abc.def", reglages)
    monkeypatch.setattr(reglages, "dashboard_password", "nouveau-mot-de-passe")
    assert not dashboard.session_valide(jeton, reglages)
    retouche = client.get("/dashboard", headers={**NAVIGATEUR, **_ip(3), "Cookie": f"{dashboard.COOKIE}={fin}.{'0' * 64}"}, follow_redirects=False)
    assert retouche.status_code == 303


def test_cinq_echecs_bloquent_meme_au_bon_mot_de_passe(reglages):
    for _ in range(reglages.login_max_failures):
        echec = _connexion(_ip(4), password="faux")
        assert echec.status_code == 401 and "Identifiant ou mot de passe incorrect" in echec.text
        assert "set-cookie" not in echec.headers
    bloque = _connexion(_ip(4))
    assert bloque.status_code == 429 and "Trop d'essais" in unescape(bloque.text) and int(bloque.headers["retry-after"]) > 0


def test_formulaire_d_un_autre_site_et_deconnexion(reglages):
    assert _connexion(_ip(5), Origin="https://site-pirate.example").status_code == 403
    sortie = client.post("/dashboard/deconnexion", headers=_ip(5), follow_redirects=False)
    assert sortie.status_code == 303 and sortie.headers["location"] == "/dashboard/connexion"
    assert 'Max-Age=0' in sortie.headers["set-cookie"] or "expires=" in sortie.headers["set-cookie"].lower()


def test_ferme_sans_mot_de_passe(monkeypatch):
    monkeypatch.setattr(get_settings(), "dashboard_password", "")
    assert client.get("/dashboard/connexion").status_code == 503
    assert client.post("/dashboard/connexion", data={"user": "kder", "password": ""}).status_code == 503


def test_un_vrai_navigateur_n_est_pas_pris_pour_un_autre_site(reglages):
    # Chrome, sous `Referrer-Policy: no-referrer`, envoie `Origin: null` et `Sec-Fetch-Site: same-origin`.
    chrome = {"Origin": "null", "Sec-Fetch-Site": "same-origin"}
    assert _connexion(_ip(6), **chrome).status_code == 303
    # Un code collé avec des espaces autour passe aussi.
    assert _connexion(_ip(7), user=" kder ", password=" mot-de-passe-du-pilotage ", **chrome).status_code == 303
    # Le navigateur dit « autre site » : refusé, même avec une bonne Origin et le bon mot de passe.
    assert _connexion(_ip(8), Origin="https://kder-ai.com", **{"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert _connexion(_ip(8), **{"Sec-Fetch-Site": "same-site"}).status_code == 403
    # Vieux navigateur sans Sec-Fetch-Site : Origin null ne prouve rien, la page vérifie le mot de passe.
    assert _connexion(_ip(9), Origin="null").status_code == 303


def test_la_protection_commune_aux_formulaires_espace_client_compris():
    from starlette.requests import Request

    from app.routes.connexion import origine_etrangere

    def requete(**entetes):
        return Request({"type": "http", "headers": [(k.lower().replace("_", "-").encode(), v.encode()) for k, v in {"host": "kder-ai.com", **entetes}.items()]})

    assert not origine_etrangere(requete(origin="null", sec_fetch_site="same-origin"))
    assert not origine_etrangere(requete(sec_fetch_site="none"))
    assert origine_etrangere(requete(origin="https://kder-ai.com", sec_fetch_site="cross-site"))
    assert not origine_etrangere(requete(origin="https://kder-ai.com"))
    assert origine_etrangere(requete(origin="https://pirate.example"))
    assert not origine_etrangere(requete(origin="null")) and not origine_etrangere(requete())
    # L'espace client (D62) passe par la même fonction : un vrai navigateur y est accepté.
    reponse = client.post("/compte/connexion", data={"email": "x@example.re", "password": "faux-mot-de-passe"},
                          headers={"Origin": "null", "Sec-Fetch-Site": "same-origin", "X-Forwarded-For": "198.18.0.40"})
    assert reponse.status_code != 403


def test_une_page_par_section(reglages):
    jeton = dashboard.jeton_session(reglages)
    for chemin, libelle in (("/dashboard/argent", "Argent"), ("/dashboard/site", "Site et SEO"), ("/dashboard/registre", "Registre")):
        page = client.get(f"{chemin}?p=7j", headers={**NAVIGATEUR, **_ip(30), "Cookie": f"{dashboard.COOKIE}={jeton}"})
        assert page.status_code == 200 and f'<a href="{chemin}?p=7j" aria-current=page>' in page.text
    # Sans session, chaque page renvoie à la connexion.
    assert client.get("/dashboard/api", headers={**NAVIGATEUR, **_ip(31)}, follow_redirects=False).status_code == 303
