"""
prompt_sender.py
────────────────
Envoie automatiquement un prompt dans une zone de texte web,
attend la réponse, gère les échecs, supprime la conversation.

Usage :
    python prompt_sender.py --site Claude --prompt "Mon prompt"
    python prompt_sender.py --site Claude --file prompt.txt
    python prompt_sender.py --list

Retourne sur stdout :
    {"status": "success", "response": "...texte réponse..."}
    {"status": "error", "message": "..."}
"""

import sys
import json
import time
import argparse
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print(json.dumps({"status": "error", "message": "Playwright non installé."}))
    sys.exit(1)

DEFAULT_CONFIG_PATH = Path(__file__).parent / "Config.json"
HTML_DUMP_PATH      = Path(__file__).parent / "debug_empty_response.html"


def load_config(config_path):
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def list_sites(config):
    for site, entry in config.items():
        kind = "API" if entry.get("api") else "Browser"
        print(f"  - {site} ({kind}) → {entry.get('url', '')}")


def insert_text_prosemirror(page, selector, text):
    """Insère du texte dans un éditeur ProseMirror/Tiptap."""
    success = page.evaluate(f"""
        (() => {{
            const el = document.querySelector({json.dumps(selector)});
            if (!el) return false;
            el.focus();
            return document.execCommand('insertText', false, {json.dumps(text)});
        }})()
    """)
    if not success:
        page.evaluate(f"""
            (() => {{
                const el = document.querySelector({json.dumps(selector)});
                if (!el) return;
                el.focus();
                const dt = new DataTransfer();
                dt.setData('text/plain', {json.dumps(text)});
                el.dispatchEvent(new ClipboardEvent('paste', {{
                    clipboardData: dt, bubbles: true, cancelable: true
                }}));
            }})()
        """)


def wait_for_response(page, selectors, timeout=120):
    print("[INFO] Attente du démarrage de la réponse...", flush=True)
    
    error_keywords = [
        "limite atteinte", "reached your usage limit", "try again after", 
        "out of messages", "prochaine disponibilité", "erreur de connexion"
    ]
    
    assistant_sel = selectors.get("assistant_message", ".font-claude-response-body")
    
    start_time = time.time()
    started = False
    
    # 1. Attendre le chargement initial de la conversation
    while time.time() - start_time < 20:
        page_text = page.inner_text("body").lower()
        for keyword in error_keywords:
            if keyword in page_text:
                print(f"\n[BLOQUAGE CRITIQUE] Limite atteinte : '{keyword}'", flush=True)
                sys.exit(1)
        
        has_response = page.locator('[data-testid="assistant-message"]').count() > 0 or page.locator(assistant_sel).count() > 0
        if "/chat/" in page.url or has_response:
            started = True
            break
            
        time.sleep(0.5)
        
    if not started:
        print("[WARN] Claude n'a pas démarré (aucun élément de réponse trouvé).", flush=True)
        return None

    print("[INFO] Réponse détectée. Capture du texte en cours...", flush=True)
    
    # 2. Capture basée sur l'activité de la page (Animation / Bouton Stop)
    last_text = ""
    stable_count = 0
    
    while time.time() - start_time < timeout:
        # Sécurité : vérifier si Claude a fini de générer en regardant les boutons de l'interface
        # Tant que le bouton de stop [aria-label="Stop generating"] ou [aria-label="Interrompre"] existe, l'animation tourne
        is_generating = page.evaluate("""
            (() => {
                // On cherche le bouton d'arrêt de génération de Claude
                const stopBtn = document.querySelector('button[aria-label*="Stop" i], button[aria-label*="Interrompre" i], button[aria-label*="pause" i]');
                if (stopBtn) return true;
                
                // Sécurité secondaire : Est-ce qu'on voit une icône de chargement/spinning ?
                const spinner = document.querySelector('.animate-spin, [class*="loading" i]');
                if (spinner) return true;
                
                return false;
            })()
        """)
        
        current_text = page.evaluate(f"""
            (() => {{
                let containers = document.querySelectorAll('[data-testid="assistant-message"], .font-claude-response');
                if (containers.length > 0 && containers[containers.length - 1].innerText.trim() !== "") {{
                    return containers[containers.length - 1].innerText;
                }}
                return "";
            }})()
        """)
        
        # Si le site dit qu'il génère encore, on refuse de s'arrêter, peu importe si le texte est figé
        if is_generating:
            stable_count = 0
            if current_text:
                last_text = current_text
        else:
            # Si le bouton stop a disparu, on applique une mini-sécurité de stabilité (0.5 seconde)
            if current_text and current_text == last_text:
                stable_count += 1
                if stable_count >= 2: 
                    break
            else:
                stable_count = 0
                if current_text:
                    last_text = current_text
                    
        time.sleep(0.25)

    final_text = last_text.strip()
    
    # Nettoyage rapide des artefacts sur les premières lignes
    if final_text:
        lines = final_text.split("\n")
        cleaned_lines = []
        for line in lines:
            if any(k in line.strip().lower() for k in ["web recherché", "web searched", "recherche en cours"]):
                continue
            cleaned_lines.append(line)
        final_text = "\n".join(cleaned_lines).strip()
    
    return final_text if final_text else None

def save_html_debug(page):
    """Sauvegarde le HTML de la page pour analyse."""
    try:
        html = page.content()
        HTML_DUMP_PATH.write_text(html, encoding="utf-8")
        print(f"[DEBUG] HTML sauvegardé : {HTML_DUMP_PATH}", flush=True)
    except Exception as e:
        print(f"[DEBUG] Erreur sauvegarde HTML : {e}", flush=True)


def delete_conversation(page, selectors):
    """Supprime la conversation en utilisant des listes de repli (fallback)."""
    try:
        print("[INFO] Suppression de la conversation via fallback...", flush=True)
        
        # 1. On prépare les listes de secours pour chaque bouton
        # Si le premier sélecteur du JSON échoue, click_with_fallback tentera les suivants
        menu_options_list = [
            selectors.get("menu_options", "button[aria-label*='options' i]"),
            "button[aria-label*='options' i]",
            "button[aria-haspopup='menu']"
        ]
        
        delete_button_list = [
            selectors.get("delete_button", "[data-testid='delete-chat-trigger']"),
            "[data-testid='delete-chat-trigger']",
            ".text-danger",
            "div:has-text('Supprimer')"
        ]
        
        confirm_delete_list = [
            selectors.get("confirm_delete", "button:has-text('Supprimer')"),
            "button:has-text('Supprimer')",
            "button:has-text('Delete')",
            ".bg-fill-danger"
        ]
        time.sleep(2.0)  # ← ajouter ici
        # 2. On exécute les clics sécurisés
        print("[DEBUG] Clic Bouton 1 (Options)...", flush=True)
        click_with_fallback(page, menu_options_list, timeout=4000)
        time.sleep(0.5)
        
        print("[DEBUG] Clic Bouton 2 (Supprimer)...", flush=True)
        click_with_fallback(page, delete_button_list, timeout=3000)
        time.sleep(0.5)
        
        print("[DEBUG] Clic Bouton 3 (Confirmation)...", flush=True)
        click_with_fallback(page, confirm_delete_list, timeout=3000)
        
        time.sleep(2.0)
        print("[OK] Conversation supprimée avec succès (via fallback).", flush=True)

    except Exception as e:
        print(f"[WARN] Échec de la suppression même avec les fallbacks : {e}", flush=True)


def send_prompt_once(page, selector, submit_key, type_delay, prompt_text):
    """Insère et soumet le prompt sur la page courante."""
    # 1. Attendre que la zone de texte soit visible
    page.wait_for_selector(selector, timeout=15000)
    page.click(selector)
    time.sleep(0.5)

    # 2. Remplissage du texte (fill est le plus robuste pour le nouveau champ Claude)
    if type_delay > 0:
        page.type(selector, prompt_text, delay=type_delay)
    else:
        page.fill(selector, prompt_text)

    time.sleep(0.5)

    # 3. Envoi via la touche clavier configurée (Enter)
    if submit_key:
        page.keyboard.press(submit_key)

    print("[INFO] Prompt soumis", flush=True)


def click_with_fallback(page, selectors_list, timeout=3000):
    """Tente de cliquer sur le premier sélecteur qui fonctionne dans une liste."""
    for selector in selectors_list:
        try:
            element = page.locator(selector).last
            element.wait_for(timeout=timeout)
            element.click()
            return True # Succès !
        except Exception:
            continue # On passe au sélecteur de secours
    raise Exception("Aucun des sélecteurs fournis n'a fonctionné.")


def send_via_browser(site_config, prompt_text):
    """Workflow complet : envoie, attend, retry si vide, supprime, retourne JSON."""

    url              = site_config.get("url")
    
    # On récupère le sous-dictionnaire des sélecteurs
    selectors        = site_config.get("selectors", {})
    selector         = selectors.get("chat_input")
    response_sel     = selectors.get("assistant_message") # Optionnel pour plus tard
    
    # Correction ici : comme il n'y a pas de submit_selector dans ton JSON,
    # on le force à None pour que la fonction comprenne qu'il faut utiliser le clavier (Enter)
    submit_sel       = None
    submit_key       = site_config.get("submit_key", "Enter")


    wait_before      = site_config.get("wait_before_type", 3.0)
    type_delay       = site_config.get("type_delay", 0)
    headless         = site_config.get("headless", False)
    response_timeout = site_config.get("response_timeout", 120)
    max_retries      = site_config.get("max_retries", 3)

    with sync_playwright() as p:
        profile = site_config.get("chrome_profile")
        if profile:
            context = p.chromium.launch_persistent_context(
                profile,
                headless=headless,
                channel="chrome",
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-restore-last-session",
                    "--no-first-run"
                ],
                ignore_default_args=["--enable-automation"],
            )
            # Masquer navigator.webdriver
            context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            # CORRECTION 1 : Évite le double onglet en récupérant celui déjà ouvert
            page = context.pages[0] if context.pages else context.new_page()
        else:
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context()
            page = context.new_page()

        response_text = None

        for attempt in range(1, max_retries + 1):
            print(f"[INFO] Tentative {attempt}/{max_retries}", flush=True)

            try:
                # Navigation vers Claude
                page.goto(url, wait_until="domcontentloaded")
                print("[INFO] Page chargée", flush=True)
                time.sleep(wait_before)

                # Envoi du prompt
                # On passe les variables dans l'ordre classique, sans nommer les arguments
                send_prompt_once(page, selector, submit_key, type_delay, prompt_text)

                # Attente et récupération de la réponse
                # CORRECTION 2 : Utilisation de la nouvelle fonction de stabilisation
                response_text = wait_for_response(page, selectors, response_timeout)

                if response_text:
                    print(f"[OK] Réponse obtenue ({len(response_text)} caractères)", flush=True)
                    # CORRECTION 3 : On supprime la conversation réussie avant de quitter
                    delete_conversation(page, selectors)
                    break
                else:
                    print(f"[WARN] Réponse vide ou non récupérée.", flush=True)
                    save_html_debug(page)
                    # CORRECTION 3 : Supprime même si la réponse a échoué/est vide
                    delete_conversation(page, selectors)

            except Exception as e:
                print(f"[ERREUR] Problème lors de la tentative {attempt} : {e}", flush=True)
                save_html_debug(page)
                # CORRECTION 3 : Forcer la suppression de la conversation cassée avant la tentative suivante
                delete_conversation(page, selectors)
                time.sleep(2.0)

        context.close()

        if response_text:
            print(json.dumps({"status": "success", "response": response_text}, ensure_ascii=False))
        else:
            print(json.dumps({
                "status": "error",
                "message": f"Aucune réponse après {max_retries} tentatives. HTML debug : {HTML_DUMP_PATH}"
            }))
            sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Envoie un prompt et récupère la réponse automatiquement")
    parser.add_argument("--site",     help="Nom du site dans Config.json")
    parser.add_argument("--prompt",   help="Texte du prompt")
    parser.add_argument("--file",     help="Fichier .txt contenant le prompt")
    parser.add_argument("--config",   help="Chemin vers Config.json")
    parser.add_argument("--list",     action="store_true", help="Lister les sites disponibles")
    parser.add_argument("--headless", action="store_true", help="Mode headless")
    args = parser.parse_args()

    config_path = args.config if args.config else DEFAULT_CONFIG_PATH
    try:
        config = load_config(config_path)
    except FileNotFoundError:
        print(json.dumps({"status": "error", "message": f"Config non trouvée : {config_path}"}))
        sys.exit(1)

    if args.list:
        list_sites(config)
        sys.exit(0)

    if not args.site:
        if len(config) == 1:
            args.site = list(config.keys())[0]
        else:
            print(json.dumps({"status": "error", "message": "Spécifie --site"}))
            sys.exit(1)

    if args.site not in config:
        print(json.dumps({"status": "error", "message": f"Site '{args.site}' non trouvé"}))
        sys.exit(1)

    site_config = config[args.site]
    if args.headless:
        site_config["headless"] = True

    if args.file:
        prompt_text = Path(args.file).read_text(encoding="utf-8").strip()
    elif args.prompt:
        prompt_text = args.prompt
    else:
        prompt_text = sys.stdin.read().strip()

    if not prompt_text:
        print(json.dumps({"status": "error", "message": "Prompt vide"}))
        sys.exit(1)

    send_via_browser(site_config, prompt_text)


if __name__ == "__main__":
    main()
