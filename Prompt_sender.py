"""
prompt_sender.py
────────────────
Envoie automatiquement un prompt dans une zone de texte web.
Usage :
    python prompt_sender.py --url "https://example.com" --selector "textarea" --prompt "Mon prompt"
    python prompt_sender.py --url "https://example.com" --selector "textarea" --file prompt.txt
    python prompt_sender.py --config config.json

Installation :
    pip install playwright
    playwright install chromium
"""

import sys
import json
import time
import argparse
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("Playwright non installé. Lance : pip install playwright && playwright install chromium")
    sys.exit(1)


# ── Configuration par défaut (exemple Claude.ai) ─────────────────────────────
DEFAULT_CONFIG = {
    "url": "https://claude.ai",
    "selector": "div[contenteditable='true'].ProseMirror",  # zone de texte Claude.ai
    "submit_selector": None,      # sélecteur du bouton submit (None = pas de submit auto)
    "submit_key": None,           # touche clavier pour soumettre ex: "Enter" (None = pas de submit)
    "wait_before_type": 2.0,      # secondes d'attente avant de taper
    "type_delay": 20,             # délai entre chaque caractère en ms (0 = instantané)
    "headless": False,            # False = tu vois le browser
}


def load_config(config_path):
    """Charge une config JSON externe."""
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def send_prompt(config, prompt_text):
    """Ouvre le browser, navigue, colle le prompt."""
    
    url           = config.get("url")
    selector      = config.get("selector")
    submit_sel    = config.get("submit_selector")
    submit_key    = config.get("submit_key")
    wait_before   = config.get("wait_before_type", 2.0)
    type_delay    = config.get("type_delay", 20)
    headless      = config.get("headless", False)

    print(f"[INFO] Ouverture de : {url}")
    print(f"[INFO] Sélecteur cible : {selector}")
    print(f"[INFO] Longueur prompt : {len(prompt_text)} caractères")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()

        # Navigation
        page.goto(url, wait_until="domcontentloaded")
        print(f"[INFO] Page chargée")

        # Attente avant interaction
        time.sleep(wait_before)

        # Attendre que le sélecteur soit disponible
        try:
            page.wait_for_selector(selector, timeout=15000)
            print(f"[INFO] Zone de texte trouvée")
        except Exception:
            print(f"[ERREUR] Zone de texte introuvable avec le sélecteur : {selector}")
            print("[INFO] Browser laissé ouvert pour inspection. Ferme manuellement.")
            input("Appuie sur Entrée pour fermer...")
            browser.close()
            return

        # Clic sur la zone de texte
        page.click(selector)
        time.sleep(0.3)

        # Saisie du prompt
        if type_delay > 0:
            print(f"[INFO] Saisie en cours (délai {type_delay}ms/car)...")
            page.type(selector, prompt_text, delay=type_delay)
        else:
            print(f"[INFO] Saisie instantanée...")
            page.keyboard.press("Control+a")
            # Pour les zones contenteditable, utiliser clipboard
            page.evaluate(f"""
                const el = document.querySelector('{selector}');
                if (el) {{
                    el.focus();
                    document.execCommand('insertText', false, {json.dumps(prompt_text)});
                }}
            """)

        print(f"[OK] Prompt inséré")

        # Submit automatique si configuré
        if submit_sel:
            try:
                page.wait_for_selector(submit_sel, timeout=5000)
                page.click(submit_sel)
                print(f"[OK] Bouton submit cliqué : {submit_sel}")
            except Exception:
                print(f"[WARN] Bouton submit non trouvé : {submit_sel}")

        elif submit_key:
            page.keyboard.press(submit_key)
            print(f"[OK] Touche '{submit_key}' pressée")

        # Maintenir le browser ouvert pour que tu puisses voir/copier la réponse
        print("\n[ACTION] Le prompt a été inséré dans la page.")
        print("[ACTION] Copie la réponse puis appuie sur Entrée pour fermer le browser.")
        input()

        browser.close()
        print("[INFO] Browser fermé.")


def main():
    parser = argparse.ArgumentParser(description="Envoie un prompt dans une zone de texte web")
    parser.add_argument("--url",      help="URL de la page cible")
    parser.add_argument("--selector", help="Sélecteur CSS de la zone de texte")
    parser.add_argument("--prompt",   help="Texte du prompt à envoyer")
    parser.add_argument("--file",     help="Fichier .txt contenant le prompt")
    parser.add_argument("--config",   help="Fichier JSON de configuration")
    parser.add_argument("--headless", action="store_true", help="Mode headless (sans UI)")
    args = parser.parse_args()

    # Charger la config
    if args.config:
        config = load_config(args.config)
    else:
        config = DEFAULT_CONFIG.copy()

    # Surcharger avec les arguments CLI
    if args.url:
        config["url"] = args.url
    if args.selector:
        config["selector"] = args.selector
    if args.headless:
        config["headless"] = True

    # Récupérer le prompt
    if args.file:
        prompt_text = Path(args.file).read_text(encoding="utf-8").strip()
    elif args.prompt:
        prompt_text = args.prompt
    else:
        # Lire depuis stdin (pipe depuis n8n ou autre)
        print("[INFO] Pas de prompt fourni. Lecture depuis stdin (Ctrl+D pour terminer)...")
        prompt_text = sys.stdin.read().strip()

    if not prompt_text:
        print("[ERREUR] Prompt vide.")
        sys.exit(1)

    if not config.get("url") or not config.get("selector"):
        print("[ERREUR] URL et selector sont obligatoires.")
        sys.exit(1)

    send_prompt(config, prompt_text)


if __name__ == "__main__":
    main()
