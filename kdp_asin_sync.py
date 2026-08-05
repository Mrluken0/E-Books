#!/usr/bin/env python3
"""
kdp_asin_sync.py — Récupère l'ASIN réel d'UN livre publié (statut PENDING) en
scrappant le Bookshelf KDP. Appelé une fois par livre (Loop Over Items1 côté
n8n). Conçu pour un run planifié SANS supervision (cron n8n).

Usage :
    python kdp_asin_sync.py --auteur_b64 <base64> --titre_b64 <base64> --sous_titre_b64 <base64>

Toutes les valeurs texte sont en base64 (apostrophes/accents/guillemets dans
les titres français cassent sinon l'échappement shell) — pas de fichier JSON
intermédiaire, conforme à la demande.

Sortie stdout (JSON, un seul objet) :
    {"status": "success", "asin": "B0XXXXXXXX"}
    {"status": "not_found", "message": "..."}
    {"status": "login_required", "message": "..."}
    {"status": "error", "message": "..."}
"""
import argparse
import base64
import json
import re
import sys

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

PROFILE_PATH = r"C:\Users\luken\AppData\Local\ms-playwright\kdp-profile"
KDP_BOOKSHELF_URL = "https://kdp.amazon.com/fr_FR/bookshelf"
HEADLESS = True
TIMEOUT = 20000


def log(message):
    print(f"[LOG] {message}", file=sys.stderr)


def from_b64(s):
    return base64.b64decode(s).decode("utf-8") if s else ""


def _extract_asin_from_text(text):
    match = re.search(r"\b(B0[A-Z0-9]{8})\b", text)
    return match.group(1) if match else None


def scrape_asin(page, titre, auteur):
    """
    Cherche la ligne du livre sur le Bookshelf par titre. KDP concatène parfois
    titre + sous-titre sur une seule ligne affichée (cf. capture partagée :
    "Le Calme à Portée de Main : 7 Routines Simples..."), donc on cherche sur
    le titre principal seul, et on désambiguïse par auteur si plusieurs lignes
    matchent (titre partiel commun à plusieurs livres).
    """
    try:
        rows = page.locator("[data-asin]", has_text=titre)
        count = rows.count()
        if count == 0:
            return None, "not_found"
        if count == 1:
            row = rows.first
        else:
            row = rows.filter(has_text=auteur).first
            if row.count() == 0:
                return None, "ambiguous"
        row.wait_for(timeout=10000)
        asin = row.get_attribute("data-asin")
        if asin and re.fullmatch(r"[A-Z0-9]{10}", asin):
            return asin, "success"
        found = _extract_asin_from_text(row.inner_text())
        return found, ("success" if found else "not_found")
    except PWTimeout:
        return None, "not_found"
    except Exception as e:
        log(f"Erreur scraping : {e}")
        return None, "error"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--auteur_b64", required=True)
    parser.add_argument("--titre_b64", required=True)
    parser.add_argument("--sous_titre_b64", default="")
    args = parser.parse_args()

    auteur = from_b64(args.auteur_b64)
    titre = from_b64(args.titre_b64)
    sous_titre = from_b64(args.sous_titre_b64)  # décodé pour log/debug, pas utilisé dans la recherche
    log(f"Recherche : titre='{titre}' | sous-titre='{sous_titre}' | auteur='{auteur}'")

    try:
        with sync_playwright() as p:
            log("Lancement de Chromium (headless, run non supervisé)...")
            context = p.chromium.launch_persistent_context(
                user_data_dir=PROFILE_PATH,
                headless=HEADLESS,
            )
            page = context.pages[0] if context.pages else context.new_page()

            log("Navigation vers le Bookshelf KDP...")
            page.goto(KDP_BOOKSHELF_URL, timeout=TIMEOUT)

            if "/ap/signin" in page.url:
                context.close()
                print(json.dumps({
                    "status": "login_required",
                    "message": "Session KDP expirée — reconnexion manuelle requise "
                                "(aucune tentative de login automatique en run non supervisé)."
                }))
                sys.exit(0)

            page.wait_for_load_state("networkidle", timeout=TIMEOUT)
            asin, status = scrape_asin(page, titre, auteur)
            context.close()

        if status == "success":
            print(json.dumps({"status": "success", "asin": asin}))
        elif status == "ambiguous":
            print(json.dumps({
                "status": "error",
                "message": f"Plusieurs livres correspondent au titre '{titre}' "
                            f"sans correspondance claire sur l'auteur '{auteur}'."
            }))
        else:
            print(json.dumps({
                "status": "not_found",
                "message": f"Livre '{titre}' (auteur: {auteur}) introuvable sur le Bookshelf "
                            "— encore en cours de traitement, ou titre affiché différent."
            }))

    except Exception as e:
        print(json.dumps({"status": "error", "message": str(e)}))
        sys.exit(1)


if __name__ == "__main__":
    main()