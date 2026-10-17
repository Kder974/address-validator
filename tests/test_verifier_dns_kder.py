"""Tests du controle DNS de `kder-ai.com` (`scripts/verifier_dns_kder.py`).

Objectif : la suite mesure ce que le script **declare**, sans toucher au reseau.
Les reponses DNS utilisees ici sont des sorties `nslookup` reelles, relevees le
13/10/2026 puis le 15/10/2026 (etat avant migration, etat attendu apres
migration), passees au script par une table `(nom, type) -> sortie brute` :
c'est donc l'analyseur qui est mesure, pas une reponse inventee enregistrement
par enregistrement. Le datacenter controle par defaut est **celui du domaine**
(`kder-ai.com` est sur Zoho monde, `mx.zoho.com`) ; la variante `zoho.eu` est
mesuree sur la meme table, car un defaut fige dans le mauvais datacenter fait
signaler trois MX manquants sur un domaine correct.

Ce qui est verifie :

  * une sortie `nslookup` (MX, TXT, valeur DKIM decoupee en fragments) ;
  * un domaine **non migre** est signale manquant/ecart, jamais « pret » ;
  * un domaine correctement configure sort en `0` et « DNS prets », dans le
    datacenter monde (defaut) comme dans la variante `--dc eu` ;
  * un DKIM non fourni est « non verifie », jamais « different » ;
  * le DMARC pose par Brevo (`rua=mailto:rua@dmarc.brevo.com`) est **accepte** ;
  * les codes de retour `1` / `2` / `3` et la sortie `--json` ;
  * une panne de resolution DNS n'est pas lue comme un enregistrement manquant.
"""

import json
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

DOMAINE = "kder-ai.com"

#: Cle DKIM factice de longueur realiste (> 255 caracteres) : un vrai resolveur
#: la renvoie en plusieurs fragments de 255, ce que le script doit recoller.
CLE_DKIM = "v=DKIM1; k=rsa; p=" + "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8A" * 8

#: Etat releve le 13/10/2026, avant migration : MX de redirection Namecheap,
#: SPF eForward, preuve de domaine Zoho, DMARC de Brevo, aucun DKIM.
MX_AVANT = (
    ("eforward1.registrar-servers.com", 10),
    ("eforward2.registrar-servers.com", 10),
    ("eforward3.registrar-servers.com", 10),
    ("eforward4.registrar-servers.com", 15),
    ("eforward5.registrar-servers.com", 20),
)
TXT_AVANT = (
    "brevo-code:b9cabf23d325d0beaa396b99cf018d1c",
    "zoho-verification=zb82668052.zmverify.zoho.com",
    "v=spf1 include:spf.efwd.registrar-servers.com ~all",
)
DMARC_BREVO = "v=DMARC1; p=none; rua=mailto:rua@dmarc.brevo.com"


def _module():
    """`scripts/verifier_dns_kder.py` importe comme module (le dossier n'est pas paquet).

    Le module est enregistre dans `sys.modules` avant `exec_module` : il declare
    un `@dataclass`, et `dataclasses` resout ses annotations via
    `sys.modules[cls.__module__]`.
    """
    nom = "verifier_dns_kder_sous_test"
    specification = spec_from_file_location(
        nom, ROOT / "scripts" / "verifier_dns_kder.py"
    )
    module = module_from_spec(specification)
    sys.modules[nom] = module
    try:
        specification.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(nom, None)
        raise
    return module


def _sortie_mx(entrees):
    """Sortie `nslookup -type=MX` factice, une ligne par enregistrement."""
    return "\n".join(
        f"{DOMAINE}\tMX preference = {priorite}, mail exchanger = {hote}"
        for hote, priorite in entrees
    )


def _sortie_txt(valeurs, nom=DOMAINE, fragments=1):
    """Sortie `nslookup -type=TXT` factice, valeur entre guillemets.

    `fragments` > 1 decoupe la valeur en morceaux de 255 caracteres : c'est ce
    que fait le resolveur pour une cle DKIM longue.
    """
    lignes = []
    for valeur in valeurs:
        if fragments > 1:
            morceaux = [
                valeur[i : i + 255] for i in range(0, len(valeur), 255)
            ] or [valeur]
            lignes.append(f"{nom}\ttext =")
            lignes.extend(f'"{morceau}"' for morceau in morceaux)
        else:
            lignes.append(f'{nom}\ttext =\n"{valeur}"')
    return "Serveur :  dns.google\nAddress:  8.8.8.8\n\n" + "\n".join(lignes)


def _simuler(monkeypatch, module, table):
    """Remplace l'interrogation DNS par une table `(nom, type) -> sortie brute`."""

    def _interroger(nom, type_, serveur, outil):
        return table.get((nom, type_), f"*** can't find {nom}: Non-existent domain")

    monkeypatch.setattr(module, "_interroger", _interroger)
    monkeypatch.setattr(module, "outil_dns", lambda: "nslookup")


def _etat_avant_migration():
    """Table DNS de l'etat releve le 13/10/2026 (aucun enregistrement Zoho)."""
    return {
        (DOMAINE, "MX"): _sortie_mx(MX_AVANT),
        (DOMAINE, "TXT"): _sortie_txt(TXT_AVANT),
        (f"_dmarc.{DOMAINE}", "TXT"): _sortie_txt([DMARC_BREVO], f"_dmarc.{DOMAINE}"),
    }


def _etat_configure(dc="com"):
    """Table DNS de l'etat attendu apres migration (docs/commercial, section 3).

    Les hotes et le `include` SPF sont donnes **en dur** : lire les constantes du
    script ne prouverait rien, c'est le script qui est mesure. Datacenter monde
    (`.com`) par defaut, comme `kder-ai.com` ; `dc="eu"` sert la variante d'un
    domaine heberge par `zoho.eu`.
    """
    suffixe = "zoho.com" if dc == "com" else "zoho.eu"
    return {
        (DOMAINE, "MX"): _sortie_mx(
            (
                (f"mx.{suffixe}", 10),
                (f"mx2.{suffixe}", 20),
                (f"mx3.{suffixe}", 50),
            )
        ),
        (DOMAINE, "TXT"): _sortie_txt(
            (
                f"v=spf1 include:{suffixe} include:spf.brevo.com ~all",
                "zoho-verification=zb82668052.zmverify.zoho.com",
            )
        ),
        (f"zmail._domainkey.{DOMAINE}", "TXT"): _sortie_txt(
            [CLE_DKIM], f"zmail._domainkey.{DOMAINE}", fragments=2
        ),
        (f"mail._domainkey.{DOMAINE}", "CNAME"): (
            f"mail._domainkey.{DOMAINE}\tcanonical name = mail._domainkey.brevo.com"
        ),
        (f"_dmarc.{DOMAINE}", "TXT"): _sortie_txt(
            [f"v=DMARC1; p=none; rua=mailto:address-validator@{DOMAINE}; fo=1"],
            f"_dmarc.{DOMAINE}",
        ),
    }


def _gravites(constats):
    """`{libelle: gravite}`, pour lire un constat sans dependre de son detail."""
    return {constat.libelle: constat.gravite for constat in constats}


# --- Analyse des reponses DNS ----------------------------------------------


def test_mx_lus_dans_une_sortie_nslookup(monkeypatch):
    """Les cinq MX de registrar de l'etat initial sont lus avec leur priorite."""
    module = _module()
    _simuler(monkeypatch, module, _etat_avant_migration())
    assert module.mx(DOMAINE) == dict(MX_AVANT)


def test_txt_decoupes_sont_recolles(monkeypatch):
    """Une cle DKIM en deux fragments est reassemblee, sinon rien ne comparerait."""
    module = _module()
    _simuler(monkeypatch, module, _etat_configure())
    assert len(CLE_DKIM) > 255  # le cas mesure est bien un cas decoupe
    assert module.txt(f"zmail._domainkey.{DOMAINE}") == [CLE_DKIM]


def test_nom_absent_donne_une_liste_vide(monkeypatch):
    """Un nom qui n'existe pas n'est pas une erreur d'outil : c'est une absence."""
    module = _module()
    _simuler(monkeypatch, module, {})
    assert module.txt(f"zmail._domainkey.{DOMAINE}") == []
    assert module.cname(f"mail._domainkey.{DOMAINE}") == []


def test_une_panne_de_resolution_nest_pas_une_absence(monkeypatch):
    """Un delai d'attente ne doit jamais se lire « enregistrement manquant »."""
    module = _module()
    monkeypatch.setattr(module, "outil_dns", lambda: "nslookup")
    monkeypatch.setattr(
        module,
        "_interroger",
        lambda nom, type_, serveur, outil: "*** Request to dns.google timed-out",
    )
    with pytest.raises(module.ErreurDns) as erreur:
        module.mx(DOMAINE)
    assert "MX" in str(erreur.value)


# --- Constats sur l'etat releve, puis sur l'etat configure ------------------


def test_domaine_non_migre_nest_jamais_pret(monkeypatch):
    """Etat du 13/10/2026 : rien de Zoho en place, donc jamais « DNS prets »."""
    module = _module()
    _simuler(monkeypatch, module, _etat_avant_migration())
    constats = module.controler(DOMAINE, "eu")
    gravites = _gravites(constats)
    details = {constat.libelle: constat.detail for constat in constats}

    assert gravites["MX mx.zoho.eu"] == "manquant"
    assert gravites["MX mx2.zoho.eu"] == "manquant"
    assert gravites["MX mx3.zoho.eu"] == "manquant"
    assert gravites["MX eforward1.registrar-servers.com"] == "different"
    assert gravites["SPF"] == "different"
    assert "efwd.registrar-servers.com" in details["SPF"]
    assert gravites["Preuve de domaine Zoho"] == "ok"
    assert gravites["DKIM Zoho"] == "manquant"
    assert gravites["DKIM Brevo"] == "different"
    # Le DMARC de Brevo est une surveillance valable : il n'est pas un ecart.
    assert gravites["DMARC"] == "ok"
    assert module.code_retour(constats) == 1  # un enregistrement manquant


def test_domaine_configure_sort_en_zero(monkeypatch, capsys):
    """Datacenter monde (le defaut) : tout est vert, code 0, « DNS prets »."""
    module = _module()
    _simuler(monkeypatch, module, _etat_configure())
    code = module.main(["--dkim", CLE_DKIM])
    sortie = capsys.readouterr().out

    assert code == 0
    assert "✅ DNS prets." in sortie
    assert "0 manquant(s)" in sortie
    assert "0 ecart(s)" in sortie
    assert "MANQUANT" not in sortie


def test_domaine_configure_eu_sort_aussi_en_zero(monkeypatch, capsys):
    """`--dc eu` pilote les hotes europeens : meme domaine, meme sortie verte."""
    module = _module()
    _simuler(monkeypatch, module, _etat_configure("eu"))
    code = module.main(["--dc", "eu", "--dkim", CLE_DKIM])
    sortie = capsys.readouterr().out

    assert code == 0
    assert "✅ DNS prets." in sortie
    assert "0 manquant(s)" in sortie
    assert "mx.zoho.eu" in sortie
    assert "mx.zoho.com" not in sortie


def test_dkim_non_fourni_est_non_verifie(monkeypatch, capsys):
    """Sans `--dkim`, le DKIM est « non verifie », et cela ne bloque pas le code."""
    module = _module()
    _simuler(monkeypatch, module, _etat_configure())
    code = module.main(["--sans-brevo"])
    sortie = capsys.readouterr().out

    assert code == 0
    assert "NON VERIFIE" in sortie
    assert "✅ DNS prets (DKIM non compare)." in sortie


def test_dmarc_brevo_est_accepte(monkeypatch):
    """Brevo pose `rua=mailto:rua@dmarc.brevo.com` : c'est un DMARC valable."""
    module = _module()
    _simuler(
        monkeypatch,
        module,
        {(f"_dmarc.{DOMAINE}", "TXT"): _sortie_txt([DMARC_BREVO], f"_dmarc.{DOMAINE}")},
    )
    constats = module.constats_dmarc(DOMAINE)
    assert [constat.gravite for constat in constats] == ["ok"]


def test_dmarc_sans_rua_est_un_ecart(monkeypatch):
    """`p=none` sans rapport : la surveillance ne remonte rien, c'est un ecart."""
    module = _module()
    _simuler(
        monkeypatch,
        module,
        {
            (f"_dmarc.{DOMAINE}", "TXT"): _sortie_txt(
                ["v=DMARC1; p=none"], f"_dmarc.{DOMAINE}"
            )
        },
    )
    constats = module.constats_dmarc(DOMAINE)
    assert [constat.gravite for constat in constats] == ["different"]
    assert "rua" in constats[0].detail


def test_dmarc_absent_est_manquant(monkeypatch):
    """Sans `_dmarc`, le domaine envoie sans politique : c'est un manquant."""
    module = _module()
    _simuler(monkeypatch, module, {})
    constats = module.constats_dmarc(DOMAINE)
    assert [constat.gravite for constat in constats] == ["manquant"]


# --- Programme principal ----------------------------------------------------


def test_main_json_porte_les_memes_constats(monkeypatch, capsys):
    """`--json` sert de trace : code, constats et gravites y sont lisibles."""
    module = _module()
    _simuler(monkeypatch, module, _etat_avant_migration())
    code = module.main(["--json"])
    rapport = json.loads(capsys.readouterr().out)

    assert code == 1
    assert rapport["code"] == 1
    assert rapport["domaine"] == DOMAINE
    assert rapport["datacenter"] == module.DC_DEFAUT  # `.com`, celui du domaine
    assert any(
        constat["libelle"] == "DKIM Zoho" and constat["gravite"] == "manquant"
        for constat in rapport["constats"]
    )


def test_main_sort_en_3_quand_le_dns_est_inutilisable(monkeypatch, capsys):
    """Sans `dig` ni `nslookup`, le script le dit et sort en 3 (jamais « pret »)."""
    module = _module()

    def _aucun_outil():
        raise module.ErreurDns("ni `dig` ni `nslookup` sur cette machine")

    monkeypatch.setattr(module, "outil_dns", _aucun_outil)
    code = module.main([])
    capture = capsys.readouterr()

    assert code == 3
    assert "nslookup" in capture.out


def test_datacenter_par_defaut_est_le_monde(monkeypatch):
    """`kder-ai.com` est sur Zoho monde : sans option, le script attend `.com`."""
    module = _module()
    _simuler(monkeypatch, module, _etat_avant_migration())

    assert module.DC_DEFAUT == "com"
    assert module.parseur().parse_args([]).dc == module.DC_DEFAUT
    assert module.MX_ATTENDUS["com"][0] == ("mx.zoho.com", 10)
    assert module.INCLUDE_SPF["com"] == "include:zoho.com"

    libelles = {constat.libelle for constat in module.controler(DOMAINE)}
    assert "MX mx.zoho.com" in libelles
    assert "MX mx.zoho.eu" not in libelles  # le defaut ne bascule pas en Europe


def test_datacenter_eu_change_les_hotes_attendus(monkeypatch):
    """La variante `.eu` reste pilotable : un domaine europeen est controle juste."""
    module = _module()
    _simuler(monkeypatch, module, _etat_avant_migration())
    gravites = _gravites(module.controler(DOMAINE, "eu"))

    assert gravites["MX mx.zoho.eu"] == "manquant"
    assert "MX mx.zoho.com" not in gravites
    assert module.MX_ATTENDUS["eu"][0] == ("mx.zoho.eu", 10)
    assert module.INCLUDE_SPF["eu"] == "include:zoho.eu"
