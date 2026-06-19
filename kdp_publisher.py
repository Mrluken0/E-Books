import sys
import json
import argparse
import os
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

def fill_book_details(page, config):
    log("Étape 1 : Remplissage des détails du livre...")
    
    # Langue
    page.wait_for_selector("#kdp-title-language", timeout=TIMEOUT)
    page.select_option("#kdp-title-language", value=config.get("langue", "fr"))
    
    # Titre et Sous-titre
    page.fill("#data-title", config["titre_livre"])
    if config.get("sous_titre"):
        page.fill("#data-subtitle", config["sous_titre"])
        
    # Auteur (Hypothèse d'un nom simple "Prénom Nom")
    nom_complet = config["auteur_nom"].split(" ", 1)
    prenom = nom_complet[0]
    nom = nom_complet[1] if len(nom_complet) > 1 else ""
    page.fill("#data-author-firstname", prenom)
    page.fill("#data-author-lastname", nom)
    
    # Description (on passe par l'iframe de l'éditeur de texte KDP si présent, ou le textarea)
    if page.locator("#data-description-iframe").count() > 0:
        frame = page.frame(name="data-description-iframe")
        frame.fill("body", config["description"])
    else:
        page.fill("#data-description", config["description"])
        
    # Droits de publication (Je possède les droits)
    page.check("#data-rights-own")
    
    # Mots-clés (KDP propose 7 champs text de id keywords-0 à keywords-6)
    mots_cles = config["mots_cles"].split(" ")[:7]
    for i, mot in enumerate(mots_cles):
        page.fill(f"#data-keywords-{i}", mot)
        
    # Catégories (Simulation de la sélection - KDP utilise souvent un modal complexe)
    # /!\ Cette section est indicative car l'arbre des catégories KDP requiert souvent des clics successifs.
    page.click("#data-categories-button")
    page.wait_for_selector("#category-dialog", timeout=TIMEOUT)
    # Logique de sélection simplifiée à adapter selon le DOM exact d'Amazon :
    # page.check(f"//label[contains(text(), '{config['categories']}')]")
    page.click("#category-save-button")
    
    # Contenu explicite (Non par défaut)
    page.check("#data-adult-content-no")
    
    # Sauvegarder et continuer
    page.click("#save-and-continue-button")

def upload_content(page, config):
    log("Étape 2 : Upload du manuscrit...")
    
    # Attente du chargement de la page 2
    page.wait_for_selector("#data-manuscript-file", timeout=TIMEOUT)
    
    # Upload du fichier .docx
    docx_path = os.path.abspath(config["docx_path"])
    if not os.path.exists(docx_path):
        raise FileNotFoundError(f"Fichier manuscrit introuvable : {docx_path}")
        
    page.set_input_files("#data-manuscript-file", docx_path)
    log("Téléchargement du manuscrit en cours (attente de la confirmation)...")
    page.wait_for_selector(".upload-success-message", timeout=60000) # Timeout étendu pour l'upload

def use_cover_creator(page, config):
    log("Étape 2.2 : Création de la couverture via Cover Creator...")
    
    # Lancement du Cover Creator
    page.click("#launch-cover-creator-button")
    page.wait_for_selector("#cover-creator-iframe", timeout=TIMEOUT)
    
    # Passage dans l'iframe du Cover Creator
    cc_frame = page.frame(name="cover-creator-iframe")
    
    # Sélection d'un template simple (ex: premier template de la liste)
    cc_frame.wait_for_selector(".design-template-thumbnail", timeout=TIMEOUT)
    cc_frame.click(".design-template-thumbnail:nth-child(1)")
    
    # Validation et soumission du design
    cc_frame.click("#submit-cover-button")
    
    # Retour à la page principale et attente du traitement du fichier par KDP
    page.wait_for_selector("#cover-upload-success-container", timeout=90000)
    
    # Sauvegarder et continuer vers l'étape Prix
    page.click("#save-and-continue-button")

def set_pricing(page, config):
    log("Étape 3 : Configuration des prix...")
    page.wait_for_selector("#data-pricing-base-marketplace", timeout=TIMEOUT)
    
    # Territoires : Tous les territoires (distribution mondiale)
    page.check("#data-territories-worldwide")
    
    # Marketplace principale
    page.select_option("#data-pricing-base-marketplace", value="amazon.fr")
    
    # Prix en EUR (KDP auto-convertit souvent pour les autres marketplaces)
    page.fill("#data-pricing-base-price", str(config["prix"]))
    
    # Attendre que les calculs de redevances soient faits pour éviter de soumettre trop vite
    page.wait_for_timeout(2000)

def submit_and_get_asin(page):
    log("Soumission du livre pour publication...")
    
    page.click("#publish-button")
    
    # Après soumission, KDP redirige vers la page d'accueil (Bibliothèque) 
    # et affiche un modal de confirmation contenant l'ASIN.
    page.wait_for_selector(".publish-confirmation-modal", timeout=60000)
    
    # Extraction de l'ASIN depuis le texte du modal ou l'URL
    asin_element = page.locator(".publish-confirmation-asin")
    if asin_element.count() > 0:
        asin = asin_element.text_content().strip()
    else:
        # Fallback : tentative d'extraction depuis l'URL courante si redirection
        current_url = page.url
        # Exemple URL: https://kdp.amazon.com/.../bookshelf?asin=B0XXXXXX
        import re
        match = re.search(r"asin=([A-Z0-9]{10})", current_url)
        asin = match.group(1) if match else "UNKNOWN_ASIN"
        
    return asin

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
            use_cover_creator(page, config)
            set_pricing(page, config)
            asin = submit_and_get_asin(page)
            
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