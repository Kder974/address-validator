"""`/checklist` : la Checklist Anti-Colis Perdu, page gratuite en français (D98).

Ce qui est mesuré, et pourquoi :

  * la page est publique, cachable, sans script, sans ressource externe ni formulaire ;
  * elle porte les 27 points de `docs/commercial/produits/01_checklist.md`, chacun avec une
    vraie case à cocher et son étiquette (accessibilité) ;
  * chaque chiffre vient du code ou de la configuration (D48) : changer un prix, un quota ou
    le plafond d'essais change la page ;
  * elle mène au test gratuit, aux offres et aux agents, et l'accueil, les tarifs et le guide
    français la citent.
"""

import re

from fastapi.testclient import TestClient

from app import accounts, i18n
from app.config import get_settings
from app.main import app
from app.routes import checklist

client = TestClient(app)


def test_la_checklist_est_publique_et_sans_script():
    page = client.get("/checklist")
    assert page.status_code == 200
    assert page.headers["cache-control"] == "public, max-age=300"
    csp = page.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "form-action 'none'" in csp and "script-src" not in csp
    for interdit in ("<script", "<img", "src=", "@import", "url(", "<form", "{{"):
        assert interdit not in page.text, interdit
    assert '<html lang="fr">' in page.text and 'name="robots" content="index, follow"' in page.text


def test_les_27_points_viennent_de_la_source_et_se_cochent():
    texte = client.get("/checklist").text
    source = open("docs/commercial/produits/01_checklist.md", encoding="utf-8").read()
    points = re.findall(r"^- \[ \] \*\*(\d+)\.\*\*", source, re.M)
    assert len(points) == 27
    for n in range(1, 28):
        assert f'<input type="checkbox" id="point-{n}"><label for="point-{n}">' in texte, n
    assert texte.count('type="checkbox"') == 27


def test_les_chiffres_sont_ceux_du_code(monkeypatch):
    reglages = get_settings().model_copy(update={"validate_form_rate_limit_per_day": 7, "x402_price": 0.05,
                                                 "trial_days": 21})
    monkeypatch.setitem(accounts.PLANS, "starter", 2345)
    texte = checklist.page(reglages)
    assert "Tester 7 adresses gratuitement" in texte and "0,05 USDC" in texte and "21 jours" in texte
    assert "2 345 validations" in texte and i18n.prix("fr", "starter") in texte
    assert i18n.prix("fr", "colis_rate_fourchette") in texte
    # L'exemple : 2 % de 1 000 commandes, à 15 et 26 € l'échec.
    assert "300 à 520 €" in texte and "1 500 à 2 600 €" in texte


def test_les_appels_a_l_action_et_le_maillage():
    texte = client.get("/checklist").text
    corps = texte[texte.index("<main"):texte.index("</main>")]  # pas le pied de page commun
    for page in ("validate-form", "pricing", "agents"):
        assert f'href="{i18n.chemin("fr", page)}"' in corps, page
    for page in ("", "pricing", "guide"):
        assert 'href="/checklist"' in client.get(i18n.chemin("fr", page), headers={"Accept": "text/html"}).text, page
