# -*- coding: utf-8 -*-
"""
Harness de test DEDIE — etape 3 : set_pricing() sur la page /pricing UNIQUEMENT.

But : executer bp.set_pricing(page, config) en live sur un brouillon existant,
puis CONFIRMER (DOM + screenshot) les valeurs remplies :
  - radio royalty_rate coche = 70_PERCENT / 35_PERCENT selon config["royalty"]
  - home_marketplace = FR
  - prix FR (price_vat_inclusive) = config["prix"]
  - estimation de redevance nette affichee par KDP (ligne Amazon.fr), si presente.

Le harness NE soumet RIEN : il s'arrete juste apres avoir lu les valeurs.

GARDE-FOUS (meme esprit que harness_save_continue.py) :
  - Neutralise DUR submit_and_get_asin (raise si appelee).
  - N'appelle AUCUN clic Publier / Enregistrer-et-continuer APRES set_pricing.
  - Check login NON bloquant (jamais de page.pause()).
  - save_content_and_continue n'est utilisee QUE pour amener /content -> /pricing
    (jamais apres set_pricing).

Usage :
  python harness_set_pricing.py --config "C:/Users/luken/.n8n-files/book_config.json" \
         --shot "<...>/pricing_filled.png"
  python harness_set_pricing.py --config "<...>" --draft-url "<URL .../content ou .../pricing>"
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import book_publisher as bp
from playwright.sync_api import sync_playwright, TimeoutError


def _blocked(name):
    def _guard(*a, **k):
        raise RuntimeError(
            f"GARDE-FOU: {name} ne doit JAMAIS etre appelee par le harness de test."
        )
    return _guard


# set_pricing est CE qu'on teste -> on NE le bloque pas.
# submit_and_get_asin ne doit jamais tourner ici.
bp.submit_and_get_asin = _blocked("submit_and_get_asin")

BOOKSHELF_URL = "https://kdp.amazon.com/fr_FR/bookshelf"


def _err(m): print(f"[HARNESS][ERREUR] {m}", file=sys.stderr)
def _info(m): print(f"[HARNESS] {m}", file=sys.stderr)
def _is_signin(page): return "/ap/signin" in page.url


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
                out.push({ id, url: a.href, title });
            }
            return out;
        }"""
    )


def setup_url(book_id, step):
    return f"https://kdp.amazon.com/fr_FR/title-setup/kindle/{book_id}/{step}"


def id_from(url_or_id):
    m = re.search(r"/title-setup/kindle/([^/]+)/", url_or_id)
    return m.group(1) if m else url_or_id.strip()


def verify_pricing(page):
    """Lecture DOM des valeurs remplies + estimation redevance ligne Amazon.fr."""
    return page.evaluate(
        r"""() => {
            const q = (s) => document.querySelector(s);
            const checked = q('input[name="data[digital][royalty_rate]-radio"]:checked');
            const home = q('select[name="data[digital][home_marketplace]"]');
            const priceFR = q('input[name="data[digital][channels][amazon][FR][price_vat_inclusive]"]');

            // Ligne Amazon.fr : remonter depuis l'input prix jusqu'a la ligne de
            // tableau (tr / [role=row]) sinon un ancetre contenant "Amazon.fr".
            let frRow = '';
            if (priceFR) {
                let n = priceFR.closest('tr, [role="row"]');
                if (n) frRow = (n.innerText || '').trim();
                if (!frRow) {
                    n = priceFR;
                    for (let i = 0; i < 12 && n; i++, n = n.parentElement) {
                        const t = (n.innerText || '').trim();
                        // La ligne FR calculee contient le prix, un % (Taux) et un € (Redevance).
                        if (t.includes('Amazon.fr') && /%/.test(t) && /[€$£¥₹]/.test(t)
                            && t.length < 4000) { frRow = t; break; }
                    }
                }
            }
            // Erreur de format prix (locale) affichee sous le champ FR ?
            const bodyTxt = (document.body.innerText || '');
            const priceFormatError = /format tarifaire/i.test(bodyTxt);
            const acctIncomplete = /informations de compte incompl/i.test(bodyTxt);

            return {
                checkedRoyalty: checked ? checked.value : null,
                homeMarketplace: home ? home.value : null,
                priceFR: priceFR ? priceFR.value : null,
                priceReadonly: priceFR ? (priceFR.readOnly || priceFR.disabled) : null,
                frRow: frRow.replace(/\s+/g, ' ').trim(),
                priceFormatError,
                acctIncomplete,
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
    _info(f"config: royalty={config.get('royalty')!r}  prix={config.get('prix')!r}  "
          f"kdp_select={config.get('kdp_select')!r}")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=bp.PROFILE_PATH, headless=bp.HEADLESS,
            args=["--start-maximized"],
        )
        page = context.pages[0] if context.pages else context.new_page()

        # --- Cible du brouillon ---
        if args.draft_url:
            book_id = id_from(args.draft_url)
            _info(f"Brouillon cible (fourni) : id={book_id}")
        else:
            _info("Aucun --draft-url : decouverte via le bookshelf...")
            drafts = discover_drafts(page)
            if _is_signin(page):
                _err("Session KDP expiree (/ap/signin) — connecte-toi puis relance.")
                context.close(); raise SystemExit(2)
            if not drafts:
                _err("Aucun brouillon trouve. Passe --draft-url explicitement.")
                context.close(); raise SystemExit(2)
            for i, d in enumerate(drafts):
                _info(f"   [{i}] id={d['id']}  titre={d['title'] or '(inconnu)'}")
            book_id = drafts[0]["id"]
            _info(f"Brouillon courant retenu : id={book_id}")

        # --- Atteindre /pricing ---
        page.goto(setup_url(book_id, "pricing"))
        try:
            page.wait_for_load_state("networkidle", timeout=bp.TIMEOUT)
        except TimeoutError:
            pass
        if _is_signin(page):
            _err("Session KDP expiree sur /pricing — connecte-toi puis relance.")
            context.close(); raise SystemExit(2)

        on_pricing = "/pricing" in page.url and page.query_selector(
            'select[name="data[digital][home_marketplace]"]') is not None
        if not on_pricing:
            # Le brouillon est peut-etre reste a /content : avancer proprement.
            _info(f"Pas encore sur /pricing (url={page.url}) — passage via /content.")
            page.goto(setup_url(book_id, "content"))
            try:
                page.wait_for_load_state("networkidle", timeout=bp.TIMEOUT)
            except TimeoutError:
                pass
            bp.save_content_and_continue(page)  # /content -> /pricing (jamais apres set_pricing)

        _info(f"Sur la page prix : {page.url}")

        # --- LE test ---
        try:
            bp.set_pricing(page, config)
        except Exception as e:
            _err(f"set_pricing a echoue : {e}")
            _err(f"URL au moment de l'echec : {page.url}")
            if args.shot:
                page.screenshot(path=args.shot, full_page=True)
                _info(f"Screenshot d'echec -> {args.shot}")
            context.close(); raise SystemExit(1)

        # Laisser KDP recalculer la redevance affichee.
        page.wait_for_timeout(1500)

        # --- Verification DOM ---
        v = verify_pricing(page)
        exp_royalty = "35_PERCENT" if str(config.get("royalty", "70")) == "35" else "70_PERCENT"
        exp_price = str(config["prix"])

        ok_royalty = v["checkedRoyalty"] == exp_royalty
        ok_market = v["homeMarketplace"] == "FR"
        ok_price = (v["priceFR"] or "").replace(",", ".") == exp_price.replace(",", ".")

        _info("---- VERIFICATION /pricing ----")
        _info(f"  royalty coche : {v['checkedRoyalty']}  (attendu {exp_royalty})  -> {'OK' if ok_royalty else 'KO'}")
        _info(f"  home_marketplace : {v['homeMarketplace']}  (attendu FR)  -> {'OK' if ok_market else 'KO'}")
        _info(f"  prix FR saisi : {v['priceFR']!r}  (attendu {exp_price})  -> {'OK' if ok_price else 'KO'}  "
              f"[readonly={v['priceReadonly']}]")
        _info(f"  ligne Amazon.fr (Taux/Redevance) : {v['frRow']!r}")
        _info(f"  erreur format prix (locale) : {v['priceFormatError']}")
        _info(f"  encart 'compte incomplet' present : {v['acctIncomplete']}")

        if args.shot:
            page.screenshot(path=args.shot, full_page=True)
            _info(f"Screenshot /pricing -> {args.shot}")

        _info("Fin du test. AUCUNE soumission, AUCUN clic Publier/Continuer effectue.")

        if args.pause:
            input("[HARNESS] Inspecte /pricing. Entree pour fermer... ")
        context.close()

        all_ok = ok_royalty and ok_market and ok_price
        raise SystemExit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
