# -*- coding: utf-8 -*-
"""
Harness de test DEDIE — fin de l'etape 2 UNIQUEMENT : le clic
« Enregistrer et continuer » (#save-and-continue) qui doit faire passer de
/content a /pricing, malgre le bandeau non bloquant « Table des matieres
manquante ».

Ce harness ne teste QUE bp.save_content_and_continue. Il ne remplit ni ne
soumet quoi que ce soit sur /pricing : on confirme seulement qu'on y atterrit
et que la page se charge.

GARDE-FOUS (identiques a harness_cover_upload.py) :
  - N'appelle JAMAIS set_pricing ni submit_and_get_asin ; les neutralise dans
    le module importe (raise si jamais appelees).
  - Ne remplit rien sur /pricing, ne clique aucun submit/publish.
  - Check login NON bloquant : si la session KDP a expire (/ap/signin), on
    s'arrete proprement au lieu de tomber sur un page.pause() interactif.

Modes de decouverte du brouillon :
  --draft-url <URL>   URL directe (/details ou /content) d'un brouillon existant.
  (defaut)            Sans --draft-url : liste les brouillons du bookshelf,
                      confirme l'id/ASIN courant, et cible le 1er brouillon.

Usage typique (non interactif) :
  python harness_save_continue.py --config "C:/Users/luken/.n8n-files/book_config.json"
  python harness_save_continue.py --config "<...>" \
         --draft-url "https://kdp.amazon.com/fr_FR/title-setup/kindle/<ID>/content"
"""
import argparse
import os
import re
import sys

# Cote a cote de book_publisher.py ; on l'importe tel quel.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import book_publisher as bp
from playwright.sync_api import sync_playwright, TimeoutError


def _blocked(name):
    def _guard(*a, **k):
        raise RuntimeError(
            f"GARDE-FOU: {name} ne doit JAMAIS etre appelee par le harness de test."
        )
    return _guard


# Neutralisation dure : meme si un chemin de code y touchait, ca leve au lieu d'agir.
bp.set_pricing = _blocked("set_pricing")
bp.submit_and_get_asin = _blocked("submit_and_get_asin")

BOOKSHELF_URL = "https://kdp.amazon.com/fr_FR/bookshelf"


def _err(msg):
    print(f"[HARNESS][ERREUR] {msg}", file=sys.stderr)


def _info(msg):
    print(f"[HARNESS] {msg}", file=sys.stderr)


def _is_signin(page):
    return "/ap/signin" in page.url


def discover_drafts(page):
    """Best-effort : liste (titre, id, url_edit) des livres du bookshelf via les
    ancres /title-setup/kindle/<ID>/... presentes dans le DOM. KDP n'expose pas
    d'ASIN pour un brouillon ; l'ID de setup sert de reference courante."""
    page.goto(BOOKSHELF_URL)
    try:
        page.wait_for_load_state("networkidle", timeout=bp.TIMEOUT)
    except TimeoutError:
        pass
    if _is_signin(page):
        return []
    rows = page.evaluate(
        r"""() => {
            const out = [];
            const seen = new Set();
            for (const a of document.querySelectorAll('a[href*="/title-setup/kindle/"]')) {
                const m = a.href.match(/\/title-setup\/kindle\/([^\/]+)\//);
                if (!m) continue;
                const id = m[1];
                if (seen.has(id)) continue;
                seen.add(id);
                // titre : remonter au conteneur de ligne et prendre un texte parlant
                let title = '';
                let n = a;
                for (let i = 0; i < 6 && n; i++, n = n.parentElement) {
                    const t = (n.getAttribute && n.getAttribute('title')) || '';
                    if (t) { title = t; break; }
                }
                out.push({ id, url: a.href, title });
            }
            return out;
        }"""
    )
    return rows


def to_content_url(url_or_id):
    """Normalise n'importe quelle URL de setup (ou un ID nu) vers l'etape /content."""
    m = re.search(r"/title-setup/kindle/([^/]+)/", url_or_id)
    book_id = m.group(1) if m else url_or_id.strip()
    return f"https://kdp.amazon.com/fr_FR/title-setup/kindle/{book_id}/content", book_id


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--draft-url", default=None,
                    help="URL /content (ou /details) d'un brouillon existant.")
    ap.add_argument("--shot", default=None,
                    help="Chemin screenshot de /pricing apres le passage.")
    ap.add_argument("--pause", action="store_true",
                    help="Pause interactive (Entree) avant de fermer le navigateur.")
    args = ap.parse_args()

    config = bp.read_config(args.config)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=bp.PROFILE_PATH, headless=bp.HEADLESS,
            args=["--start-maximized"],
        )
        page = context.pages[0] if context.pages else context.new_page()

        # --- Cible du brouillon ---
        if args.draft_url:
            content_url, book_id = to_content_url(args.draft_url)
            _info(f"Brouillon cible (fourni) : id={book_id}")
        else:
            _info("Aucun --draft-url : decouverte via le bookshelf...")
            drafts = discover_drafts(page)
            if _is_signin(page):
                _err("Session KDP expiree (redirige vers /ap/signin) — "
                     "connecte-toi manuellement dans ce profil puis relance. "
                     "Aucune action effectuee.")
                context.close()
                raise SystemExit(2)
            if not drafts:
                _err("Aucun brouillon trouve dans le bookshelf (DOM inattendu ?). "
                     "Passe --draft-url explicitement.")
                context.close()
                raise SystemExit(2)
            _info(f"{len(drafts)} livre(s) detecte(s) dans le bookshelf :")
            for i, d in enumerate(drafts):
                _info(f"   [{i}] id={d['id']}  titre={d['title'] or '(inconnu)'}")
            content_url, book_id = to_content_url(drafts[0]["url"])
            _info(f"Brouillon courant retenu : id={book_id}")

        # --- Navigation directe sur /content, sans passer par les etapes amont ---
        _info(f"Navigation vers {content_url}")
        page.goto(content_url)
        try:
            page.wait_for_load_state("networkidle", timeout=bp.TIMEOUT)
        except TimeoutError:
            pass

        if _is_signin(page):
            _err("Session KDP expiree sur /content (/ap/signin) — connecte-toi "
                 "manuellement dans ce profil puis relance. Aucune action effectuee.")
            context.close()
            raise SystemExit(2)

        _info(f"URL avant le clic : {page.url}")
        toc_warning = page.evaluate(
            r"""() => document.body.innerText.toLowerCase()
                    .includes('table des mati') """
        )
        _info(f"Bandeau 'Table des matieres manquante' present : {toc_warning}")

        # --- LE test : le seul appel du harness ---
        try:
            bp.save_content_and_continue(page)
        except Exception as e:
            _err(f"save_content_and_continue a echoue : {e}")
            _err(f"URL au moment de l'echec : {page.url}")
            if args.shot:
                page.screenshot(path=args.shot, full_page=True)
                _info(f"Screenshot d'echec -> {args.shot}")
            context.close()
            raise SystemExit(1)

        # --- Verification post-clic ---
        landed = "/pricing" in page.url
        _info(f"URL apres le clic : {page.url}")
        if landed:
            _info("SUCCES : /pricing atteint. Le bandeau TOC n'a PAS bloque le clic.")
        else:
            _err(f"ECHEC : /pricing NON atteint (URL={page.url}).")

        # Confirme juste que la page de prix se charge (sans rien remplir/soumettre).
        try:
            page.wait_for_selector(
                'select[name="data[digital][home_marketplace]"]', timeout=bp.TIMEOUT
            )
            _info("Page /pricing chargee (select marketplace present). "
                  "AUCUN champ rempli, AUCUNE soumission.")
        except TimeoutError:
            _err("Sur /pricing mais le select marketplace n'apparait pas "
                 "(page prix pas totalement chargee ?).")

        if args.shot:
            page.screenshot(path=args.shot, full_page=True)
            _info(f"Screenshot /pricing -> {args.shot}")

        if args.pause:
            input("[HARNESS] Inspecte /pricing. Entree pour fermer... ")
        context.close()

        raise SystemExit(0 if landed else 1)


if __name__ == "__main__":
    main()
