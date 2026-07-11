# -*- coding: utf-8 -*-
"""
Harness de test DEDIE — upload de couverture (Mode 1) UNIQUEMENT.

But : confirmer en live que le fichier reellement transmis a l'input file KDP
(#data-assets-cover-file-upload-AjaxInput) est bien config["cover_path"] (le
_composed.jpg avec texte incruste), et que l'apercu KDP l'affiche.

GARDE-FOUS (suite a l'incident de lancement accidentel) :
  - N'importe PAS et n'appelle JAMAIS set_pricing ni submit_and_get_asin.
  - Les neutralise malgre tout dans le module importe (raise si jamais appelees).
  - Ne clique jamais #save-and-continue vers /pricing, ni aucun submit.
  - S'arrete immediatement apres use_cover_creator.

Deux modes :
  --mode cover-only --draft-url <URL/content>
        Navigue directement sur le /content d'un brouillon EXISTANT, puis lance
        seulement use_cover_creator. Aucun brouillon cree. (recommande)
  --mode full
        Reproduit le flux amont (details -> content -> cover) et cree donc un
        brouillon reel. Requiert que le config contienne 'contenu_ia'.

Usage :
  python harness_cover_upload.py --config "C:/Users/luken/.n8n-files/book_config.json" \
         --mode cover-only --draft-url "https://kdp.amazon.com/fr_FR/title-setup/kindle/<ID>/content"
"""
import argparse
import os
import sys

# Ce harness est cote a cote de book_publisher.py ; on l'importe tel quel.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import book_publisher as bp
from playwright.sync_api import sync_playwright


def _blocked(name):
    def _guard(*a, **k):
        raise RuntimeError(
            f"GARDE-FOU: {name} ne doit JAMAIS etre appelee par le harness de test."
        )
    return _guard


# Neutralisation dure : meme si un chemin de code y touchait, ca leve au lieu d'agir.
bp.set_pricing = _blocked("set_pricing")
bp.submit_and_get_asin = _blocked("submit_and_get_asin")


def _trace_upload(page):
    """Enveloppe page.set_input_files pour journaliser le chemin EXACT transmis
    a l'input couverture, sans rien changer au comportement."""
    orig = page.set_input_files

    def wrapped(selector, files, *a, **k):
        if "cover-file-upload" in str(selector):
            print(f"[TRACE] set_input_files -> selecteur={selector}", file=sys.stderr)
            print(f"[TRACE] set_input_files -> FICHIER REEL = {files}", file=sys.stderr)
            variant = "_composed" if "_composed" in str(files).lower() else "ARTWORK-NU (?)"
            print(f"[TRACE] variante detectee = {variant}", file=sys.stderr)
        return orig(selector, files, *a, **k)

    page.set_input_files = wrapped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--mode", choices=["cover-only", "full"], default="cover-only")
    ap.add_argument("--draft-url", default=None,
                    help="URL /content d'un brouillon existant (mode cover-only)")
    ap.add_argument("--shot", default=None, help="Chemin screenshot apercu apres upload")
    args = ap.parse_args()

    config = bp.read_config(args.config)
    print(f"[HARNESS] cover_path lu dans le config = {config.get('cover_path')}", file=sys.stderr)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=bp.PROFILE_PATH, headless=bp.HEADLESS,
            args=["--start-maximized"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        _trace_upload(page)

        if args.mode == "cover-only":
            if not args.draft_url:
                raise SystemExit("--draft-url requis en mode cover-only")
            print(f"[HARNESS] Navigation directe vers {args.draft_url}", file=sys.stderr)
            page.goto(args.draft_url)
            bp.ensure_logged_in(page, config)
            input("[HARNESS] Connecte-toi si besoin, place-toi sur l'onglet /content, "
                  "puis appuie sur Entree ici pour lancer l'upload couverture... ")
            bp.use_cover_creator(context, page, config)
        else:
            page.goto(bp.KDP_NEW_EBOOK_URL)
            bp.ensure_logged_in(page, config)
            bp.fill_book_details(page, config)
            bp.upload_content(page, config)
            bp.use_cover_creator(context, page, config)

        print("[HARNESS] use_cover_creator termine. AUCUNE etape prix/soumission lancee.",
              file=sys.stderr)
        if args.shot:
            page.screenshot(path=args.shot, full_page=True)
            print(f"[HARNESS] Screenshot apercu -> {args.shot}", file=sys.stderr)

        input("[HARNESS] Inspecte l'apercu KDP. Entree pour fermer le navigateur... ")
        context.close()


if __name__ == "__main__":
    main()
