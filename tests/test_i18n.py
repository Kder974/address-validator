"""Tests de `app/i18n.py` (décision D57).

Ce que ces tests mesurent, et pourquoi :

  * le français reste à la racine et les autres langues sont préfixées : une adresse
    déjà publiée (`/pricing`) ne change pas ;
  * les prix de chaque langue sont **exactement** la table de `PRIX-PAR-LANGUE.md`
    (décision du 14/10/2026), écrits comme la langue les écrit ;
  * l'euro reste la vérité de facturation : il vient de `pricing.OFFRES`, et la
    table convertie doit suivre ces euros. Changer un prix dans le code sans
    refaire la conversion fait échouer ces tests ;
  * le rendu d'un gabarit échappe les valeurs et refuse de publier un champ vide.
"""

from decimal import Decimal

import pytest

from app import accounts, i18n
from app.routes.pricing import OFFRES, PROBLEME

OFFRES_PAR_NOM = {offre["nom"]: offre for offre in OFFRES}

#: PRIX-PAR-LANGUE.md : essai, Starter, Pro, unitaire Starter, unitaire Pro, colis raté, fourchette.
TABLE = {
    "fr": ("0 €", "29 €", "79 €", "0,0145 €", "0,0132 €", "15 €", "15–26 €"),
    "es": ("0 €", "29 €", "79 €", "0,0145 €", "0,0132 €", "15 €", "15–26 €"),
    "en": ("$0", "$33", "$90", "$0.0165", "$0.0150", "$17", "$17–30"),
    "ar": ("$0", "$33", "$90", "$0.0165", "$0.0150", "$17", "$17–30"),
    "zh": ("¥0", "¥223", "¥608", "¥0.1115", "¥0.1013", "¥115", "¥115–200"),
    "hi": ("₹0", "₹3,160", "₹8,620", "₹1.58", "₹1.44", "₹1,600", "₹1,600–2,900"),
}


def test_le_francais_reste_a_la_racine():
    assert i18n.chemin("fr") == "/"
    assert i18n.chemin("fr", "pricing") == "/pricing"
    assert i18n.chemin("en") == "/en/"
    assert i18n.chemin("en", "pricing") == "/en/pricing"
    assert i18n.chemin("ar", "validate-form") == "/ar/validate-form"
    assert i18n.PREFIXEES == ("en", "zh", "hi", "es", "ar")
    assert i18n.RTL == {"ar"}


@pytest.mark.parametrize("lang, page", [("de", ""), ("en", "tarifs"), ("fr", "/pricing")])
def test_une_page_inconnue_est_refusee(lang, page):
    with pytest.raises(ValueError):
        i18n.chemin(lang, page)


def test_les_liens_donnent_la_meme_page_dans_chaque_langue():
    champs = i18n.liens("en", "pricing")

    assert champs["url_accueil"] == "/en/"
    assert champs["url_pricing"] == "/en/pricing"
    assert champs["url_validate_form"] == "/en/validate-form"
    assert [champs[f"lien_{lang}"] for lang in i18n.LANGUES] == [
        "/pricing", "/en/pricing", "/zh/pricing", "/hi/pricing", "/es/pricing", "/ar/pricing",
    ]


def test_les_nombres_suivent_la_langue():
    assert [i18n.nombre(lang, 2000) for lang in ("fr", "es", "en")] == ["2 000", "2.000", "2,000"]
    assert [i18n.nombre(lang, 0.02) for lang in ("fr", "es", "hi")] == ["0,02", "0,02", "0.02"]


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_les_prix_sont_ceux_de_la_table(lang):
    assert tuple(i18n.prix(lang, cle) for cle in i18n.CLES_PRIX) == TABLE[lang]


def test_l_euro_vient_du_code():
    for cle, nom in (("essai", "Essai gratuit"), ("starter", "Starter"), ("pro", "Pro")):
        assert i18n.prix("fr", cle) == OFFRES_PAR_NOM[nom]["prix"]


@pytest.mark.parametrize("devise", sorted(i18n.TAUX))
def test_la_conversion_suit_les_euros_du_code(devise):
    """Le jour où `pricing.OFFRES` change, la table convertie est périmée : ce test le dit."""
    for cle, nom in (("starter", "Starter"), ("pro", "Pro")):
        attendu = Decimal(str(OFFRES_PAR_NOM[nom]["prix_eur"])) * i18n.TAUX[devise]
        assert abs(i18n.MONTANTS[devise][cle] - attendu) <= attendu / 100, (devise, nom)


@pytest.mark.parametrize("devise", sorted(i18n.MONTANTS))
def test_le_prix_unitaire_est_le_prix_divise_par_le_quota(devise):
    montants = i18n.MONTANTS[devise]
    # Arrondi au dernier chiffre affiché (D94 : 79 € ÷ 6 000 ne tombe pas juste).
    for plan in ("starter", "pro"):
        unitaire = montants[f"unitaire_{plan}"]
        assert unitaire == (montants[plan] / accounts.PLANS[plan]).quantize(unitaire), (devise, plan)


def test_le_colis_rate_en_dollars_est_celui_du_code():
    assert i18n.MONTANTS["USD"]["colis_rate"] == Decimal(str(accounts.COUT_ECHEC_USD))
    assert PROBLEME[1][0] == "17 à 30 $" and i18n.MONTANTS["USD"]["colis_rate_max"] == 30


def test_la_mention_facture_en_euros():
    assert i18n.mention_euros("en") == (
        "Approximate prices. Billed in euros: €29 and €79 per month, excl. VAT "
        "(exchange rate of September 2026)."
    )
    assert i18n.mention_euros("fr") == i18n.mention_euros("es") == ""
    for lang in ("zh", "hi", "ar"):
        assert "29" in i18n.mention_euros(lang) and "2026" in i18n.mention_euros(lang)


def test_rendre_remplit_et_echappe(tmp_path):
    gabarit = tmp_path / "page.html"
    gabarit.write_text("<p>{{a}} — {{b}}</p>", encoding="utf-8")

    assert i18n.rendre(gabarit, {"a": "<b>&", "b": 'x"y', "inutile": "z"}) == (
        "<p>&lt;b&gt;&amp; — x&quot;y</p>"
    )


def test_rendre_refuse_un_champ_vide(tmp_path):
    manquant = tmp_path / "manquant.html"
    manquant.write_text("<p>{{a}} {{b}}</p>", encoding="utf-8")
    mal_ecrit = tmp_path / "mal_ecrit.html"
    mal_ecrit.write_text("<p>{{ a }}</p>", encoding="utf-8")

    with pytest.raises(RuntimeError):
        i18n.rendre(manquant, {"a": "1"})
    with pytest.raises(RuntimeError):
        i18n.rendre(mal_ecrit, {"a": "1"})


def test_une_saisie_avec_des_accolades_s_affiche_telle_quelle(tmp_path):
    """« {{x}} » tapé par un visiteur n'est pas un champ oublié : pas de `500`."""
    gabarit = tmp_path / "page.html"
    gabarit.write_text("<p>{{a}}</p>", encoding="utf-8")

    assert i18n.rendre(gabarit, {"a": "{{a}} {{b"}) == "<p>{{a}} {{b</p>"
