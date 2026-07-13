# -*- coding: utf-8 -*-
"""
Harness de test DEDIE — etape 4 (FINALE) : submit_and_get_asin() en DRY-RUN.

But : derouler la VRAIE fonction bp.submit_and_get_asin(page, config) sur un
brouillon KDP reel, JUSQU'AU point d'appel du clic de soumission finale
(#save-and-publish-announce) — MAIS SANS JAMAIS CLIQUER CE BOUTON.

>>> CONTRAINTE ABSOLUE <<<
Sous AUCUN pretexte on ne clique le bouton de soumission finale (ni tout bouton
qui declencherait une vraie publication). Le clic precis est NEUTRALISE DUR
(pas seulement "on s'arrete par convention") :

  - page.click est monkeypatche. Tout appel visant #save-and-publish-announce
    (ou tout selecteur contenant "publish"/"save-and-publish") NE clique PAS :
    il logge "PRET A SOUMETTRE, ARRET VOLONTAIRE ICI" et leve _SubmitReached.
  - _SubmitReached herite de BaseException volontairement : ainsi il ECHAPPE au
    `except Exception` interne de submit_and_get_asin (qui, sinon, l'aurait
    re-emballe en "Erreur etape finale") et remonte tel quel au harness. On
    distingue donc proprement 3 cas :
        * _SubmitReached  -> point de clic atteint SANS cliquer (succes du dry-run)
        * Exception       -> garde-fou pre-clic a bloque (compte/livre incomplet)
        * retour normal    -> NE DOIT JAMAIS ARRIVER (voudrait dire clic passe)

Avant d'appeler submit_and_get_asin, le harness fait aussi son PROPRE releve
independant (pour le rapport, meme si la fonction leve) :
  - bp._publish_is_blocked(page)  -> blocage detecte ou non
  - etat DOM du bouton Publier (classe, aria-disabled, .disabled, texte)
  - banniere "compte incomplet" / fiscal / bancaire eventuelle
  - recap des champs requis (royalty coche, home_marketplace, prix FR)
  - screenshot de la page avant soumission

Le harness NE soumet RIEN. Il ne re-remplit PAS le pricing (deja valide en live) ;
il ne fait que naviguer /pricing et lire.

Usage :
  python harness_submit_dryrun.py --config "C:/Users/luken/.n8n-files/book_config.json" \
         --shot "<...>/submit_ready.png"
  python harness_submit_dryrun.py --config "<...>" --draft-url "<URL .../pricing>"
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import book_publisher as bp
from playwright.sync_api import sync_playwright, TimeoutError


BOOKSHELF_URL = "https://kdp.amazon.com/fr_FR/bookshelf"
PUBLISH_BTN = "#save-and-publish-announce"


class _SubmitReached(BaseException):
    """Sentinelle : le code a atteint le clic de soumission. On NE clique PAS.

    Herite de BaseException (pas Exception) pour echapper au `except Exception`
    interne de submit_and_get_asin et remonter intact jusqu'au harness.
    """


def _err(m):  print(f"[HARNESS][ERREUR] {m}", file=sys.stderr)
def _info(m): print(f"[HARNESS] {m}", file=sys.stderr)
def _is_signin(page): return "/ap/signin" in page.url


def _install_click_guard(page):
    """Neutralise DUR le clic de soumission finale sur CE page object.

    N'importe quel selecteur visant le bouton Publier est intercepte : au lieu
    de cliquer, on logge et on leve _SubmitReached. Tous les AUTRES clics
    (navigation interne eventuelle) restent normaux.
    """
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


# --- Garde-fou global : rien d'autre que submit_and_get_asin ne doit soumettre.
# (set_pricing/save_content_and_continue ne sont PAS appelees ici, mais on blinde.)
def _blocked(name):
    def _guard(*a, **k):
        raise RuntimeError(f"GARDE-FOU: {name} ne doit JAMAIS etre appelee par ce harness.")
    return _guard


bp.save_content_and_continue = _blocked("save_content_and_continue")
bp.set_pricing = _blocked("set_pricing")


def discover_drafts(page):
    page.goto(BOOKSHELF_URL)
    try:
        page.wait_for_load_state("networkidle", timeout=bp.TIMEOUT)
    except TimeoutError:
        pass
    if _is_signin(page):
        return []
    return page.evaluate(
        r"""() => {
            const out = []; const seen = new Set();
            for (const a of document.querySelectorAll('a[href*="/title-setup/kindle/"]')) {
                const m = a.href.match(/\/title-setup\/kindle\/([^\/]+)\//);
                if (!m) continue;
                const id = m[1];
                if (seen.has(id)) continue;
                seen.add(id);
                let title = ''; let n = a;
                for (let i = 0; i < 6 && n; i++, n = n.parentElement) {
                    const t = (n.getAttribute && n.getAttribute('title')) || '';
                    if (t) { title = t; break; }
                }
                // ASIN eventuel sur un ancetre (livre deja publie)
                let asin = ''; n = a;
                for (let i = 0; i < 8 && n; i++, n = n.parentElement) {
                    const da = n.getAttribute && n.getAttribute('data-asin');
                    if (da) { asin = da; break; }
                }
                out.push({ id, url: a.href, title, asin });
            }
            return out;
        }"""
    )


def setup_url(book_id, step):
    return f"https://kdp.amazon.com/fr_FR/title-setup/kindle/{book_id}/{step}"


def id_from(url_or_id):
    m = re.search(r"/title-setup/kindle/([^/]+)/", url_or_id)
    return m.group(1) if m else url_or_id.strip()


def verify_publish_page(page):
    """Releve DOM complet de l'etat de la page AVANT soumission (pour le rapport)."""
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

            // Champs requis (miroir du /pricing)
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

                // Bannieres de blocage compte possibles
                acctIncomplete: rx(/informations de compte incompl/i)
                                || rx(/compte.{0,20}incomplet/i),
                taxMissing: rx(/renseignements fiscaux/i) || rx(/informations fiscales/i)
                            || rx(/questionnaire fiscal/i),
                bankMissing: rx(/informations bancaires/i) || rx(/mode de paiement/i)
                             || rx(/coordonnees bancaires/i),
                identityMissing: rx(/verifier votre identite/i) || rx(/identite.{0,20}incompl/i),

                // Messages d'erreur/validation visibles (best-effort)
                hasErrorAlert: !!q('[role="alert"], .a-alert-error, .error-message'),
            };
        }"""
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--draft-url", default=None)
    ap.add_argument("--shot", default=None)
    ap.add_argument("--pause", action="store_true")
    args = ap.parse_args()

    config = bp.read_config(args.config)
    _info(f"config: titre={config.get('titre_livre')!r}  royalty={config.get('royalty')!r}  "
          f"prix={config.get('prix')!r}")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=bp.PROFILE_PATH, headless=bp.HEADLESS,
            args=["--start-maximized"],
        )
        page = context.pages[0] if context.pages else context.new_page()

        # --- 0) ASIN courant dans le bookshelf AVANT de commencer ---
        _info("Inventaire du bookshelf (ASIN courant avant test)...")
        drafts = discover_drafts(page)
        if _is_signin(page):
            _err("Session KDP expiree (/ap/signin) — connecte-toi puis relance.")
            context.close(); raise SystemExit(2)
        if not drafts and not args.draft_url:
            _err("Aucun brouillon trouve. Passe --draft-url explicitement.")
            context.close(); raise SystemExit(2)
        for i, d in enumerate(drafts):
            _info(f"   [{i}] id={d['id']}  titre={d['title'] or '(inconnu)'}  "
                  f"asin={d['asin'] or '(aucun)'}")

        # --- Cible du brouillon ---
        if args.draft_url:
            book_id = id_from(args.draft_url)
            _info(f"Brouillon cible (fourni) : id={book_id}")
        else:
            book_id = drafts[0]["id"]
            _info(f"Brouillon courant retenu : id={book_id}  "
                  f"asin_actuel={drafts[0]['asin'] or '(aucun)'}")

        # --- Atteindre /pricing (SANS re-remplir : deja valide en live) ---
        page.goto(setup_url(book_id, "pricing"))
        try:
            page.wait_for_load_state("networkidle", timeout=bp.TIMEOUT)
        except TimeoutError:
            pass
        if _is_signin(page):
            _err("Session KDP expiree sur /pricing — connecte-toi puis relance.")
            context.close(); raise SystemExit(2)
        _info(f"Sur la page : {page.url}")

        # Attendre que le bouton Publier soit dans le DOM
        try:
            page.wait_for_selector(PUBLISH_BTN, timeout=bp.TIMEOUT)
        except TimeoutError:
            _err(f"Bouton {PUBLISH_BTN} introuvable — pas au bon stade ?")
            if args.shot:
                page.screenshot(path=args.shot, full_page=True)
            context.close(); raise SystemExit(1)

        # --- Releve independant AVANT de derouler la fonction ---
        page.wait_for_timeout(1200)  # laisser KDP calculer/valider
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

        # --- LE dry-run : derouler la VRAIE submit_and_get_asin jusqu'au clic ---
        _install_click_guard(page)
        _info("Appel de bp.submit_and_get_asin(page, config) en DRY-RUN...")
        try:
            asin = bp.submit_and_get_asin(page, config)
            # Ne doit JAMAIS arriver : le clic est neutralise en amont.
            _err("!!! ANORMAL : submit_and_get_asin a RETOURNE sans etre interceptee. "
                 f"asin={asin!r}. Le clic a-t-il eu lieu ??")
            context.close(); raise SystemExit(3)
        except _SubmitReached:
            _info("OK : point de clic de soumission ATTEINT sans cliquer. "
                  "Garde-fou pre-clic (_publish_is_blocked) PASSE -> aucun blocage.")
            result = "READY_NO_BLOCK"
        except SystemExit:
            raise
        except Exception as e:
            # Garde-fou pre-clic de submit_and_get_asin a leve AVANT le clic.
            _info(f"submit_and_get_asin s'est arretee AVANT le clic : {e}")
            result = "BLOCKED_OR_ERROR"

        _info(f"---- RESULTAT DRY-RUN : {result} ----")
        _info("Fin du test. AUCUNE soumission, AUCUN clic Publier effectue.")

        if args.pause:
            input("[HARNESS] Inspecte la page. Entree pour fermer... ")
        context.close()

        # Succes = point de clic atteint proprement ET aucun blocage detecte.
        ok = (result == "READY_NO_BLOCK") and (not blocked_flag) \
            and not (v["acctIncomplete"] or v["taxMissing"]
                     or v["bankMissing"] or v["identityMissing"])
        raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
