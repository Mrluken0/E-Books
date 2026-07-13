# -*- coding: utf-8 -*-
"""
Harness DEDIE — recreation d'un brouillon de test PUIS dry-run de submit.

Contexte : le Bookshelf du profil kdp-profile est vide (plus aucun brouillon).
Pour tester l'etape finale submit_and_get_asin, on regenere d'abord un vrai
brouillon en deroulant le VRAI pipeline (etapes toutes validees en live) :

    fill_book_details -> upload_content -> use_cover_creator
    -> save_content_and_continue (/content -> /pricing) -> set_pricing

...puis on enchaine le DRY-RUN de submit_and_get_asin, EXACTEMENT comme
harness_submit_dryrun.py : la vraie fonction est deroulee jusqu'au point de
clic de soumission, mais le clic est NEUTRALISE DUR.

>>> CONTRAINTE ABSOLUE (identique au harness precedent) <<<
On ne clique JAMAIS le bouton de soumission finale. Le clic sur
#save-and-publish-announce (ou tout selecteur contenant "publish") est
intercepte par un monkeypatch de page.click qui logge et leve _SubmitReached
(herite de BaseException pour echapper au except Exception interne de
submit_and_get_asin). Le garde-fou est installe DES le depart : meme si une
etape amont tentait de publier, elle serait bloquee.

Les AUTRES clics du pipeline (#save-and-continue, #continue, Cover Creator...)
ne contiennent pas "publish" -> ils passent normalement. Cover Creator opere
sur cc_page (autre page object) : non affecte par le garde-fou de `page`.

Ce harness ECRIT un vrai brouillon sur KDP (c'est le but : recreer une cible).
Il ne PUBLIE rien.

Usage :
  python harness_full_recreate_dryrun.py \
      --config "C:/Users/luken/.n8n-files/book_config.json" \
      --shot "<...>/submit_ready.png"
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import book_publisher as bp
from playwright.sync_api import sync_playwright, TimeoutError

PUBLISH_BTN = "#save-and-publish-announce"


class _SubmitReached(BaseException):
    """Sentinelle : le code a atteint le clic de soumission. On NE clique PAS."""


def _err(m):  print(f"[HARNESS][ERREUR] {m}", file=sys.stderr)
def _info(m): print(f"[HARNESS] {m}", file=sys.stderr)
def _is_signin(page): return "/ap/signin" in page.url


def _install_click_guard(page):
    orig_click = page.click

    def guarded_click(selector, *a, **k):
        s = str(selector).lower()
        if "save-and-publish" in s or "publish" in s:
            _info("=" * 68)
            _info(f"PRET A SOUMETTRE — clic sur {selector!r} INTERCEPTE.")
            _info("ARRET VOLONTAIRE ICI : aucune publication declenchee.")
            _info("=" * 68)
            raise _SubmitReached()
        return orig_click(selector, *a, **k)

    page.click = guarded_click
    _info(f"Garde-fou clic installe (neutralise tout clic visant {PUBLISH_BTN!r}).")


def verify_publish_page(page):
    """Releve DOM de l'etat de la page AVANT soumission (pour le rapport)."""
    return page.evaluate(
        r"""() => {
            const q = (s) => document.querySelector(s);
            const span = q('#save-and-publish');
            const btn  = q('#save-and-publish-announce');
            const cls = ((span && span.className) || '') + ' ' + ((btn && btn.className) || '');
            const ariaSpan = span && span.getAttribute('aria-disabled');
            const ariaBtn  = btn && btn.getAttribute('aria-disabled');
            const prop = !!(btn && btn.disabled === true);
            const disabled = /disabled/i.test(cls) || ariaSpan === 'true'
                             || ariaBtn === 'true' || prop;
            const checked = q('input[name="data[digital][royalty_rate]-radio"]:checked');
            const home = q('select[name="data[digital][home_marketplace]"]');
            const priceFR = q('input[name="data[digital][channels][amazon][FR][price_vat_inclusive]"]');
            const bodyTxt = (document.body.innerText || '');
            const rx = (re) => re.test(bodyTxt);
            return {
                url: location.href,
                btnPresent: !!btn,
                btnText: (span && (span.innerText || '').trim())
                         || (btn && (btn.innerText || '').trim()) || null,
                btnDisabled: disabled,
                btnClass: cls.trim().slice(0, 300),
                ariaSpan, ariaBtn, prop,
                royaltyChecked: checked ? checked.value : null,
                homeMarketplace: home ? home.value : null,
                priceFR: priceFR ? priceFR.value : null,
                acctIncomplete: rx(/informations de compte incompl/i) || rx(/compte.{0,20}incomplet/i),
                taxMissing: rx(/renseignements fiscaux/i) || rx(/informations fiscales/i) || rx(/questionnaire fiscal/i),
                bankMissing: rx(/informations bancaires/i) || rx(/mode de paiement/i) || rx(/coordonnees bancaires/i),
                identityMissing: rx(/verifier votre identite/i) || rx(/identite.{0,20}incompl/i),
                hasErrorAlert: !!q('[role="alert"], .a-alert-error, .error-message'),
            };
        }"""
    )


def id_from(url_or_id):
    import re
    m = re.search(r"/title-setup/kindle/([^/]+)/", url_or_id or "")
    return m.group(1) if m else (url_or_id or "").strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--draft-url", default=None,
                    help="Reprendre un brouillon existant (.../<id>/content) au lieu d'en creer un.")
    ap.add_argument("--shot", default=None)
    ap.add_argument("--pause", action="store_true")
    args = ap.parse_args()

    config = bp.read_config(args.config)

    # Injection EN MEMOIRE des outils IA (ajout KDP 2026-07). Le config n8n ne
    # porte pas encore ces cles ; valeurs dictees par l'auteur (une IA par zone).
    # N'ecrase jamais une valeur deja presente dans le config.
    ia = config.get("contenu_ia") or {}
    ia.setdefault("outils_texte", ["Claude", "Gemini"])
    ia.setdefault("outils_images", ["Claude", "Gemini"])
    config["contenu_ia"] = ia
    _info(f"config: titre={config.get('titre_livre')!r}  royalty={config.get('royalty')!r}  "
          f"prix={config.get('prix')!r}")
    _info(f"contenu_ia (avec outils injectes): texte={ia.get('texte')} outils_texte={ia.get('outils_texte')} "
          f"| images={ia.get('images')} outils_images={ia.get('outils_images')} "
          f"| traductions={ia.get('traductions')}")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=bp.PROFILE_PATH, headless=bp.HEADLESS,
            args=["--start-maximized"],
        )
        page = context.pages[0] if context.pages else context.new_page()

        # Garde-fou clic installe AVANT toute etape (filet de securite global).
        _install_click_guard(page)

        resume = bool(args.draft_url)
        if resume:
            book_id = id_from(args.draft_url)
            target = f"https://kdp.amazon.com/fr_FR/title-setup/kindle/{book_id}/content"
            _info(f"REPRISE du brouillon existant id={book_id} -> {target}")
            page.goto(target)
        else:
            _info(f"Navigation vers {bp.KDP_NEW_EBOOK_URL}")
            page.goto(bp.KDP_NEW_EBOOK_URL)
        try:
            page.wait_for_load_state("networkidle", timeout=bp.TIMEOUT)
        except TimeoutError:
            pass
        if _is_signin(page):
            _err("Session KDP expiree (/ap/signin) — connecte-toi puis relance.")
            context.close(); raise SystemExit(2)

        # --- Pipeline reel jusqu'a set_pricing (etapes validees en live) ---
        try:
            if resume:
                _info("[1/5] fill_book_details SAUTE (brouillon repris, details deja remplis).")
            else:
                _info("[1/5] fill_book_details...")
                bp.fill_book_details(page, config)
            _info("[2/5] upload_content...")
            bp.upload_content(page, config)
            _info("[3/5] use_cover_creator...")
            bp.use_cover_creator(context, page, config)
            _info("[4/5] save_content_and_continue (/content -> /pricing)...")
            bp.save_content_and_continue(page)
            _info("[5/5] set_pricing...")
            bp.set_pricing(page, config)
        except _SubmitReached:
            _err("!!! Un clic 'publish' a ete intercepte PENDANT le pipeline (anormal). "
                 "Aucune publication. Arret.")
            if args.shot:
                page.screenshot(path=args.shot, full_page=True)
            context.close(); raise SystemExit(3)
        except Exception as e:
            _err(f"Echec pipeline avant soumission : {e}")
            _err(f"URL au moment de l'echec : {page.url}")
            if args.shot:
                page.screenshot(path=args.shot, full_page=True)
            context.close(); raise SystemExit(1)

        _info(f"Brouillon cree, sur la page : {page.url}")

        # Attendre le bouton Publier
        try:
            page.wait_for_selector(PUBLISH_BTN, timeout=bp.TIMEOUT)
        except TimeoutError:
            _err(f"Bouton {PUBLISH_BTN} introuvable apres set_pricing.")
            if args.shot:
                page.screenshot(path=args.shot, full_page=True)
            context.close(); raise SystemExit(1)

        # --- Releve independant AVANT dry-run ---
        page.wait_for_timeout(1500)
        v = verify_publish_page(page)
        blocked_flag = bp._publish_is_blocked(page)

        _info("---- ETAT PAGE AVANT SOUMISSION ----")
        _info(f"  URL                        : {v['url']}")
        _info(f"  Bouton present             : {v['btnPresent']}")
        _info(f"  Bouton texte               : {v['btnText']!r}")
        _info(f"  Bouton DESACTIVE ?         : {v['btnDisabled']}  "
              f"(aria span={v['ariaSpan']} btn={v['ariaBtn']} prop={v['prop']})")
        _info(f"  bp._publish_is_blocked()   : {blocked_flag}")
        _info(f"  Bouton class               : {v['btnClass']!r}")
        _info("  --- Champs requis (recap) ---")
        _info(f"  royalty coche              : {v['royaltyChecked']}")
        _info(f"  home_marketplace           : {v['homeMarketplace']}")
        _info(f"  prix FR                    : {v['priceFR']!r}")
        _info("  --- Bannieres blocage compte ---")
        _info(f"  compte incomplet           : {v['acctIncomplete']}")
        _info(f"  fiscal manquant            : {v['taxMissing']}")
        _info(f"  bancaire manquant          : {v['bankMissing']}")
        _info(f"  identite manquante         : {v['identityMissing']}")
        _info(f"  alerte erreur visible      : {v['hasErrorAlert']}")

        if args.shot:
            page.screenshot(path=args.shot, full_page=True)
            _info(f"Screenshot avant soumission -> {args.shot}")

        # --- DRY-RUN : derouler la VRAIE submit_and_get_asin jusqu'au clic ---
        _info("Appel de bp.submit_and_get_asin(page, config) en DRY-RUN...")
        try:
            asin = bp.submit_and_get_asin(page, config)
            _err("!!! ANORMAL : submit_and_get_asin a RETOURNE sans interception. "
                 f"asin={asin!r}. Le clic a-t-il eu lieu ??")
            context.close(); raise SystemExit(3)
        except _SubmitReached:
            _info("OK : point de clic de soumission ATTEINT sans cliquer. "
                  "Garde-fou pre-clic (_publish_is_blocked) PASSE -> aucun blocage.")
            result = "READY_NO_BLOCK"
        except SystemExit:
            raise
        except Exception as e:
            _info(f"submit_and_get_asin s'est arretee AVANT le clic : {e}")
            result = "BLOCKED_OR_ERROR"

        _info(f"---- RESULTAT DRY-RUN : {result} ----")
        _info("Fin du test. AUCUNE soumission, AUCUN clic Publier effectue.")

        if args.pause:
            input("[HARNESS] Inspecte la page. Entree pour fermer... ")
        context.close()

        ok = (result == "READY_NO_BLOCK") and (not blocked_flag) \
            and not (v["acctIncomplete"] or v["taxMissing"]
                     or v["bankMissing"] or v["identityMissing"])
        raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
