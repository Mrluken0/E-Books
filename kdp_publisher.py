import sys
import json
import argparse
import os
import re
from playwright.sync_api import sync_playwright, TimeoutError

# Configurer stdout en UTF-8 pour éviter les erreurs d'encodage avec n8n
sys.stdout.reconfigure(encoding='utf-8')

# --- CONFIGURATION INTERNE ---
HEADLESS = False  # Passer à True une fois le script stabilisé en prod
PROFILE_PATH = r"C:\Users\luken\AppData\Local\ms-playwright\kdp-profile"
TIMEOUT = 30000  # 30 secondes


def log(message):
    """Écrit les logs intermédiaires sur stderr pour ne pas polluer stdout."""
    print(f"[LOG] {message}", file=sys.stderr)


def read_config(path):
    log(f"Lecture de la configuration : {path}")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Le fichier de configuration {path} n'existe pas.")
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# ÉTAPE 1 — DÉTAILS DU LIVRE
# ---------------------------------------------------------------------------
def fill_book_details(page, config):
    """Remplit langue, titre, auteur, description, droits, mots-clés, catégories."""
    log("Étape 1 : Remplissage des détails du livre...")
    try:
        # --- Décomposition de l'auteur (Prénom / Nom) ---
        nom_complet = config["auteur_nom"].split(" ", 1)
        prenom = nom_complet[0]
        nom = nom_complet[1] if len(nom_complet) > 1 else ""

        # --- Mots-clés : split sur VIRGULE (corrigé), 7 max, trim des espaces ---
        mots_cles = [m.strip() for m in config["mots_cles"].split(",") if m.strip()][:7]
        log(f"Mots-clés préparés ({len(mots_cles)}) : {mots_cles}")

        # =====================================================================
        # >>> PAUSE 1 : DÉTAILS DU LIVRE
        # À inspecter en live dans le DevTools (clic droit > Inspecter) :
        #   - Sélecteur LANGUE          (ex: #data-print-book-language-native ?)
        #   - Sélecteur TITRE           (ex: #data-print-book-title ?)
        #   - Sélecteur SOUS-TITRE      (ex: #data-print-book-subtitle ?)
        #   - Sélecteur AUTEUR PRÉNOM   (ex: #data-print-book-primary-author-first-name ?)
        #   - Sélecteur AUTEUR NOM      (ex: #data-print-book-primary-author-last-name ?)
        #   - Sélecteur DROITS "je possède les droits"
        #   - Sélecteurs 7 champs MOTS-CLÉS
        #   - Bouton + modal CATÉGORIES
        #   - Radio CONTENU EXPLICITE = Non
        #   - Bouton "Enregistrer et continuer"
        # Variables dispo pour tester dans la console pause :
        #   config["langue"], config["titre_livre"], config.get("sous_titre"),
        #   prenom, nom, config["description"], mots_cles, config["categories"]
        # =====================================================================
        log(">>> PAUSE 1 : identifier les sélecteurs de la page DÉTAILS du livre")
        page.pause()

        # -------------------------------------------------------------------
        # TEMPLATE à dé-commenter et compléter avec les VRAIS sélecteurs :
        #
        # page.wait_for_selector("SELECTOR_LANGUE", timeout=TIMEOUT)
        # page.select_option("SELECTOR_LANGUE", value=config.get("langue", "fr"))
        #
        # page.fill("SELECTOR_TITRE", config["titre_livre"])
        # if config.get("sous_titre"):
        #     page.fill("SELECTOR_SOUS_TITRE", config["sous_titre"])
        #
        # page.fill("SELECTOR_AUTEUR_PRENOM", prenom)
        # page.fill("SELECTOR_AUTEUR_NOM", nom)
        #
        # _fill_description(page, config["description"])   # voir helper dédié
        #
        # page.check("SELECTOR_DROITS_OWN")
        #
        # for i, mot in enumerate(mots_cles):
        #     page.fill(f"SELECTOR_KEYWORD_{i}", mot)
        #
        # page.click("SELECTOR_CATEGORIES_BUTTON")
        # page.wait_for_selector("SELECTOR_CATEGORY_DIALOG", timeout=TIMEOUT)
        # ... navigation dans l'arbre des catégories ...
        # page.click("SELECTOR_CATEGORY_SAVE")
        #
        # page.check("SELECTOR_ADULT_CONTENT_NO")
        # page.click("SELECTOR_SAVE_AND_CONTINUE")
        # -------------------------------------------------------------------

    except TimeoutError as e:
        raise Exception(f"Timeout étape 1 (détails livre) — sélecteur introuvable : {str(e)}")
    except KeyError as e:
        raise Exception(f"Erreur étape 1 (détails livre) — clé de config manquante : {str(e)}")
    except Exception as e:
        raise Exception(f"Erreur étape 1 (détails livre) : {str(e)}")


def _fill_description(page, description):
    """
    Remplit la description KDP en gérant les deux cas :
      1. Éditeur rich text TinyMCE (cas le plus fréquent sur KDP)
      2. Fallback : textarea classique

    À FINALISER après inspection : adapter le nom/sélecteur de l'iframe TinyMCE
    et le sélecteur du textarea de secours.
    """
    log(">>> PAUSE (description) : identifier l'éditeur (TinyMCE iframe vs textarea)")
    page.pause()

    # -------------------------------------------------------------------
    # TEMPLATE — cas 1 : TinyMCE présent
    #
    # Échapper la description pour l'injection JS via json.dumps
    # safe_desc = json.dumps(description)
    # try:
    #     # L'API TinyMCE est globale sur la page parente, pas dans l'iframe.
    #     page.evaluate(f"tinymce.activeEditor.setContent({safe_desc})")
    #     return
    # except Exception:
    #     pass
    #
    # # TEMPLATE — cas 1bis : remplir directement le <body> de l'iframe TinyMCE
    # iframe_loc = page.locator("SELECTOR_TINYMCE_IFRAME")
    # if iframe_loc.count() > 0:
    #     frame = iframe_loc.content_frame
    #     frame.fill("body", description)
    #     return
    #
    # # TEMPLATE — cas 2 : fallback textarea classique
    # page.fill("SELECTOR_DESCRIPTION_TEXTAREA", description)
    # -------------------------------------------------------------------


# ---------------------------------------------------------------------------
# ÉTAPE 2 — UPLOAD DU MANUSCRIT
# ---------------------------------------------------------------------------
def upload_content(page, config):
    """Upload du fichier .docx et attente de la confirmation."""
    log("Étape 2 : Upload du manuscrit...")
    try:
        docx_path = os.path.abspath(config["docx_path"])
        if not os.path.exists(docx_path):
            raise FileNotFoundError(f"Fichier manuscrit introuvable : {docx_path}")
        log(f"Manuscrit à uploader : {docx_path}")

        # =====================================================================
        # >>> PAUSE 2 : UPLOAD MANUSCRIT
        # À inspecter :
        #   - <input type="file"> du manuscrit (souvent caché, viser l'input
        #     et non le bouton stylisé)
        #   - Élément/texte de confirmation de fin d'upload
        # Variable dispo : docx_path
        # =====================================================================
        log(">>> PAUSE 2 : identifier l'input file manuscrit + message de succès")
        page.pause()

        # -------------------------------------------------------------------
        # TEMPLATE :
        # page.set_input_files("SELECTOR_MANUSCRIPT_INPUT", docx_path)
        # log("Téléchargement du manuscrit en cours...")
        # page.wait_for_selector("SELECTOR_UPLOAD_SUCCESS", timeout=120000)
        # -------------------------------------------------------------------

    except FileNotFoundError:
        raise
    except TimeoutError as e:
        raise Exception(f"Timeout étape 2 (upload manuscrit) — confirmation non reçue : {str(e)}")
    except Exception as e:
        raise Exception(f"Erreur étape 2 (upload manuscrit) : {str(e)}")


# ---------------------------------------------------------------------------
# ÉTAPE 2.2 — COUVERTURE (Cover Creator)
# ---------------------------------------------------------------------------
def use_cover_creator(context, page, config):
    """
    Lance le Cover Creator. KDP peut l'ouvrir soit dans un NOUVEL ONGLET,
    soit dans une iframe : on gère les deux cas.
    """
    log("Étape 2.2 : Création de la couverture via Cover Creator...")
    try:
        # =====================================================================
        # >>> PAUSE 2.2a : identifier le bouton de lancement du Cover Creator
        # =====================================================================
        log(">>> PAUSE 2.2a : identifier le bouton 'Lancer le Cover Creator'")
        page.pause()

        cc_page = None
        # -------------------------------------------------------------------
        # TEMPLATE — cas A : Cover Creator dans un NOUVEL ONGLET
        # try:
        #     with context.expect_page(timeout=TIMEOUT) as new_page_info:
        #         page.click("SELECTOR_LAUNCH_COVER_CREATOR")
        #     cc_page = new_page_info.value
        #     cc_page.wait_for_load_state()
        #     log("Cover Creator ouvert dans un nouvel onglet.")
        # except TimeoutError:
        #     # Pas de nouvel onglet → cas B : iframe sur la page courante
        #     log("Pas de nouvel onglet, tentative via iframe...")
        #     cc_frame = page.locator("SELECTOR_COVER_CREATOR_IFRAME").content_frame
        #     cc_page = cc_frame  # le reste de l'API .click/.wait_for_selector est compatible
        # -------------------------------------------------------------------

        # =====================================================================
        # >>> PAUSE 2.2b : dans le Cover Creator, identifier :
        #   - vignette de template à sélectionner
        #   - bouton de validation / soumission du design
        # Utiliser cc_page (onglet) OU cc_frame (iframe) selon le cas détecté.
        # =====================================================================
        log(">>> PAUSE 2.2b : identifier template + bouton de validation du Cover Creator")
        page.pause()

        # -------------------------------------------------------------------
        # TEMPLATE :
        # cc_page.wait_for_selector("SELECTOR_TEMPLATE_THUMB", timeout=TIMEOUT)
        # cc_page.click("SELECTOR_TEMPLATE_THUMB")   # 1er template
        # cc_page.click("SELECTOR_SUBMIT_COVER")
        #
        # # Retour page principale : attente du traitement de la couverture
        # page.wait_for_selector("SELECTOR_COVER_UPLOAD_SUCCESS", timeout=120000)
        # page.click("SELECTOR_SAVE_AND_CONTINUE")
        # -------------------------------------------------------------------

    except TimeoutError as e:
        raise Exception(f"Timeout étape 2.2 (Cover Creator) — sélecteur introuvable : {str(e)}")
    except Exception as e:
        raise Exception(f"Erreur étape 2.2 (Cover Creator) : {str(e)}")


# ---------------------------------------------------------------------------
# ÉTAPE 3 — PRIX
# ---------------------------------------------------------------------------
def set_pricing(page, config):
    """
    Définit le prix sur amazon.fr en EUR.
    KDP calcule automatiquement les autres marketplaces : on n'y touche PAS.
    """
    log("Étape 3 : Configuration des prix...")
    try:
        prix = str(config["prix"])
        log(f"Prix cible (EUR / amazon.fr) : {prix}")

        # =====================================================================
        # >>> PAUSE 3 : PRIX
        # À inspecter :
        #   - Distribution / territoires (mondiale)
        #   - Le champ prix de base correspondant à amazon.fr (EUR).
        #     KDP affiche un tableau de marketplaces : ne remplir QUE la ligne
        #     amazon.fr, laisser les autres se calculer automatiquement.
        #   - Bouton de publication
        # Variable dispo : prix
        # =====================================================================
        log(">>> PAUSE 3 : identifier le champ prix amazon.fr (EUR) — NE PAS toucher aux autres")
        page.pause()

        # -------------------------------------------------------------------
        # TEMPLATE :
        # page.check("SELECTOR_TERRITORIES_WORLDWIDE")
        # # Cibler spécifiquement la ligne / l'onglet amazon.fr :
        # page.fill("SELECTOR_PRICE_AMAZON_FR_EUR", prix)
        # # Laisser KDP recalculer les autres marketplaces :
        # page.wait_for_timeout(2000)
        # -------------------------------------------------------------------

    except TimeoutError as e:
        raise Exception(f"Timeout étape 3 (prix) — sélecteur introuvable : {str(e)}")
    except KeyError as e:
        raise Exception(f"Erreur étape 3 (prix) — clé de config manquante : {str(e)}")
    except Exception as e:
        raise Exception(f"Erreur étape 3 (prix) : {str(e)}")


# ---------------------------------------------------------------------------
# SOUMISSION + RÉCUPÉRATION ASIN
# ---------------------------------------------------------------------------
def submit_and_get_asin(page, config):
    """
    Soumet le livre puis récupère l'ASIN de façon robuste :
      1. Regex sur l'URL courante (asin=XXXXXXXXXX)
      2. Fallback : scrap du tableau de la bibliothèque KDP par titre
      3. Sinon "PENDING" (KDP peut mettre quelques minutes à l'attribuer)
    """
    log("Soumission du livre pour publication...")
    try:
        # =====================================================================
        # >>> PAUSE FINALE : identifier le bouton PUBLIER
        # Après clic, KDP redirige généralement vers la Bibliothèque (Bookshelf)
        # — il n'y a PAS forcément de modal avec l'ASIN.
        # =====================================================================
        log(">>> PAUSE FINALE : identifier le bouton 'Publier' (puis redirection Bookshelf)")
        page.pause()

        # -------------------------------------------------------------------
        # TEMPLATE :
        # page.click("SELECTOR_PUBLISH_BUTTON")
        # # Attendre la redirection vers la bibliothèque
        # page.wait_for_load_state("networkidle", timeout=60000)
        # -------------------------------------------------------------------

        # --- 1) Extraction via l'URL courante ---
        asin = _extract_asin_from_url(page.url)
        if asin:
            log(f"ASIN trouvé via URL : {asin}")
            return asin

        # --- 2) Fallback : scrap du tableau de la bibliothèque KDP par titre ---
        asin = _scrape_asin_from_bookshelf(page, config["titre_livre"])
        if asin:
            log(f"ASIN trouvé via bibliothèque KDP : {asin}")
            return asin

        # --- 3) Rien trouvé : KDP attribue parfois l'ASIN avec quelques minutes de délai ---
        log("ASIN non disponible immédiatement — retour 'PENDING'.")
        return "PENDING"

    except TimeoutError as e:
        raise Exception(f"Timeout étape finale (publication) — {str(e)}")
    except Exception as e:
        raise Exception(f"Erreur étape finale (publication / récupération ASIN) : {str(e)}")


def _extract_asin_from_url(url):
    """Cherche un ASIN (10 caractères A-Z0-9) dans l'URL courante."""
    match = re.search(r"asin=([A-Z0-9]{10})", url)
    if match:
        return match.group(1)
    # Variante : ASIN directement dans le chemin (ex: .../B0XXXXXXXX/...)
    match = re.search(r"\b(B0[A-Z0-9]{8})\b", url)
    return match.group(1) if match else None


def _scrape_asin_from_bookshelf(page, titre_livre):
    """
    Fallback : sur la page Bookshelf, retrouve la ligne du livre par son titre
    et en extrait l'ASIN.

    À FINALISER après inspection du DOM de la bibliothèque :
      - sélecteur des lignes de livres
      - emplacement du titre et de l'ASIN dans chaque ligne
    """
    log(">>> PAUSE (fallback ASIN) : inspecter le tableau Bookshelf pour localiser titre + ASIN")
    page.pause()

    # -------------------------------------------------------------------
    # TEMPLATE :
    # try:
    #     row = page.locator("SELECTOR_BOOK_ROW", has_text=titre_livre).first
    #     row.wait_for(timeout=TIMEOUT)
    #     # L'ASIN est souvent dans un attribut data-* ou un texte de la ligne :
    #     row_text = row.inner_text()
    #     return _extract_asin_from_url(row_text) or _extract_asin_from_text(row_text)
    # except Exception:
    #     return None
    # -------------------------------------------------------------------
    return None


def _extract_asin_from_text(text):
    """Cherche un ASIN brut dans un texte quelconque."""
    match = re.search(r"\b(B0[A-Z0-9]{8})\b", text)
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="KDP Publisher Script via Playwright")
    parser.add_argument("--config", required=True, help="Chemin vers le fichier kdp_config.json")
    args = parser.parse_args()

    output = {}

    try:
        config = read_config(args.config)

        with sync_playwright() as p:
            log("Lancement de Chromium avec le profil persistant...")
            context = p.chromium.launch_persistent_context(
                user_data_dir=PROFILE_PATH,
                headless=HEADLESS,
                args=["--start-maximized"]
            )

            page = context.new_page()

            log("Navigation vers KDP Setup...")
            page.goto("https://kdp.amazon.com/en_US/title-setup/kindle/new/details")

            # Déroulement du workflow
            fill_book_details(page, config)
            upload_content(page, config)
            use_cover_creator(context, page, config)
            set_pricing(page, config)
            asin = submit_and_get_asin(page, config)

            context.close()

        output = {"status": "success", "asin": asin}

    except Exception as e:
        log(f"ERREUR CRITIQUE : {str(e)}")
        output = {"status": "error", "message": str(e)}

    finally:
        # Le SEUL print qui doit aller sur stdout pour n8n
        print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
