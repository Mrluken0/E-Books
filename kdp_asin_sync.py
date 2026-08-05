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
import time
import unicodedata

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

PROFILE_PATH = r"C:\Users\luken\AppData\Local\ms-playwright\kdp-profile"
KDP_BOOKSHELF_URL = "https://kdp.amazon.com/fr_FR/bookshelf"
HEADLESS = True
TIMEOUT = 20000


def log(message):
    print(f"[LOG] {message}", file=sys.stderr)


def from_b64(s):
    return base64.b64decode(s).decode("utf-8") if s else ""


# Apostrophes typographiques / accents graves détournés -> apostrophe droite ASCII.
_APOS_TABLE = dict.fromkeys(map(ord, "’‘ʼ´`"), ord("'"))


def _normalize(s):
    """Normalise un titre pour comparaison robuste.

    Le DOM Bookshelf réserve deux pièges au matching par titre :
      - une apostrophe droite (U+0027) que la source pourrait fournir en
        typographique (U+2019), ou l'inverse -> on unifie tout en U+0027 ;
      - un ESPACE INSÉCABLE (U+00A0) inséré avant les « : » ("L'Effet
        Micro-Calme : ...") là où la chaîne de recherche a un espace
        normal -> on remplace les espaces insécables/fins par un espace normal
        et on réduit les runs d'espaces.
    Les accents sont conservés (ils sont significatifs et présents des deux
    côtés) ; on applique juste NFC pour aligner accents composés/précomposés.
    """
    if not s:
        return ""
    s = unicodedata.normalize("NFC", s).translate(_APOS_TABLE)
    for ws in (" ", " ", " "):
        s = s.replace(ws, " ")
    return re.sub(r"\s+", " ", s).strip().casefold()


# Extraction DOM : le Bookshelf « refreshed » est une table Amazon « mt ».
# Chaque livre = un groupe de cellules partageant data-row=<titleId interne KDP>
# (ex. 262DWNTJW92) — ce n'est PAS l'ASIN. L'ASIN (B0XXXXXXXX) n'apparaît nulle
# part en attribut de ligne : il vit uniquement dans les href des liens
# marketplace/X-Ray d'une cellule action de la même ligne
# (/amazon-dp-action/fr/dualbookshelf.marketplacelink/B0XXXXXXXX). Le titre
# COMPLET (titre + sous-titre concaténés) est dans un span.title-link-label de
# la cellule metadata (non tronqué ; la troncature visible est purement CSS).
# On itère les titres, on remonte au data-row, puis on récupère l'ASIN parmi
# toutes les cellules de ce data-row.
_EXTRACT_JS = r"""
() => {
    const asinRe = /B0[A-Z0-9]{8}/;
    const esc = (s) => (window.CSS && CSS.escape) ? CSS.escape(s) : s;
    const seen = new Set();
    const out = [];
    document.querySelectorAll('.title-link-label').forEach(span => {
        // .title-link-label matche aussi un span « série » parasite (surtout
        // des espaces) -> on l'exclut, et on ignore tout titre vide.
        if ((span.className || '').indexOf('manage-series-link') !== -1) return;
        const title = (span.textContent || '').trim();
        if (!title) return;
        let rid = null, cur = span;
        for (let i = 0; i < 25 && cur; i++) {
            if (cur.getAttribute && cur.getAttribute('data-row')) { rid = cur.getAttribute('data-row'); break; }
            cur = cur.parentElement;
        }
        if (!rid || seen.has(rid)) return;
        let asin = null;
        document.querySelectorAll('[data-row="' + esc(rid) + '"]').forEach(cell => {
            if (asin) return;
            const a = cell.querySelector('a[href*="marketplacelink/"], a[href*="xray/verify/"]');
            if (a) { const m = a.href.match(asinRe); if (m) asin = m[0]; }
        });
        // Cellule metadata complète -> contient aussi l'auteur (désambiguïsation).
        let metaEl = span;
        for (let i = 0; i < 25 && metaEl; i++) {
            if (metaEl.getAttribute && metaEl.getAttribute('data-row')) break;
            metaEl = metaEl.parentElement;
        }
        const metaText = ((metaEl && metaEl.innerText) || span.textContent || '').replace(/\s+/g, ' ').trim();
        out.push({ rid: rid, title: title, asin: asin, metaText: metaText });
        seen.add(rid);
    });
    return out;
}
"""


def _read_books(page):
    """Extrait la liste des livres visibles [{rid, title, asin, metaText}, ...].

    Le tableau « refreshed bookshelf » se (re)rend de façon asynchrone : on
    attend l'ATTACHEMENT d'un titre (surtout pas l'état « visible » par défaut :
    le premier .title-link-label du DOM est un span « série » parasite jamais
    visible, ce qui ferait expirer l'attente), puis on ré-extrait avec quelques
    scrolls si la première passe revient vide (rendu tardif des lignes).
    """
    try:
        page.wait_for_selector(".title-link-label", state="attached", timeout=15000)
    except PWTimeout:
        pass  # on tente quand même l'extraction ci-dessous
    for _ in range(5):
        books = page.evaluate(_EXTRACT_JS)
        if books:
            return books
        page.mouse.wheel(0, 3000)
        time.sleep(1.5)
    return []


def scrape_asin(page, titre, auteur):
    """
    Retrouve l'ASIN d'un livre par son titre sur le Bookshelf.

    Le titre affiché concatène titre principal + sous-titre ("L'Effet
    Micro-Calme : Le guide ..."), donc on matche le titre recherché comme
    SOUS-CHAÎNE normalisée du titre affiché (voir _normalize pour apostrophe /
    espace insécable / accents). En cas d'ambiguïté (plusieurs livres dont le
    titre affiché contient la chaîne cherchée), on désambiguïse par auteur
    (présent dans la cellule metadata).
    """
    try:
        books = _read_books(page)
        if not books:
            return None, "not_found"

        needle = _normalize(titre)
        if not needle:
            return None, "not_found"

        candidates = [b for b in books if b.get("asin") and needle in _normalize(b["title"])]
        if not candidates:
            return None, "not_found"
        if len(candidates) == 1:
            return candidates[0]["asin"], "success"

        # Plusieurs titres correspondent -> filtre par auteur.
        needle_auteur = _normalize(auteur)
        if needle_auteur:
            strict = [b for b in candidates if needle_auteur in _normalize(b["metaText"])]
            if len(strict) == 1:
                return strict[0]["asin"], "success"
        return None, "ambiguous"
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