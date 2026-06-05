"""
prompt_sender.py
────────────────
Envoie automatiquement un prompt dans une zone de texte web ou via API.

Usage :
    python prompt_sender.py --site Claude --prompt "Mon prompt"
    python prompt_sender.py --site Claude --file prompt.txt
    python prompt_sender.py --site MonSite --config config.json --prompt "..."

Config.json attendu :
{
  "Claude": {
    "url": "https://claude.ai/new",
    "selector": "div[data-testid='chat-input']",
    "submit_selector": "button[type='submit']",
    "submit_key": "Enter",
    "wait_before_type": 3.0,
    "type_delay": 0,
    "headless": false
  },
  "AutreSite": { ... }
}

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

# ── Chemin par défaut du fichier config ───────────────────────────────────────
DEFAULT_CONFIG_PATH = Path(__file__).parent / "Config.json"


def load_config(config_path):
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def list_sites(config):
    print("Sites disponibles dans la config :")
    for site in config.keys():
        entry = config[site]
        kind = "API" if entry.get("api") else "Browser"
        print(f"  - {site} ({kind}) → {entry.get('url', '')}")


def insert_text_prosemirror(page, selector, text):
    """
    Insère du texte dans un éditeur ProseMirror/Tiptap.
    Méthode 1 : execCommand insertText (fonctionne sur la plupart des éditeurs riches)
    Méthode 2 : clipboard paste (fallback)
    """
    # Méthode 1 — execCommand
    success = page.evaluate(f"""
        (() => {{
            const el = document.querySelector({json.dumps(selector)});
            if (!el) return false;
            el.focus();
            const result = document.execCommand('insertText', false, {json.dumps(text)});
            return result;
        }})()
    """)

    if not success:
        # Méthode 2 — clipboard (nécessite permissions)
        print("[WARN] execCommand échoué, tentative clipboard...")
        page.evaluate(f"""
            (() => {{
                const el = document.querySelector({json.dumps(selector)});
                if (!el) return;
                el.focus();
                const dt = new DataTransfer();
                dt.setData('text/plain', {json.dumps(text)});
                el.dispatchEvent(new ClipboardEvent('paste', {{
                    clipboardData: dt,
                    bubbles: true,
                    cancelable: true
                }}));
            }})()
        """)


def send_via_browser(site_config, prompt_text):
    """Envoie le prompt via Playwright (browser automation)."""

    url          = site_config.get("url")
    selector     = site_config.get("selector")
    submit_sel   = site_config.get("submit_selector")
    submit_key   = site_config.get("submit_key")
    wait_before  = site_config.get("wait_before_type", 2.0)
    type_delay   = site_config.get("type_delay", 0)
    headless     = site_config.get("headless", False)

    print(f"[INFO] URL         : {url}")
    print(f"[INFO] Sélecteur   : {selector}")
    print(f"[INFO] Prompt      : {len(prompt_text)} caractères")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()

        # Navigation
        page.goto(url, wait_until="domcontentloaded")
        print("[INFO] Page chargée")

        # Attente avant interaction
        time.sleep(wait_before)

        # Attendre la zone de texte
        try:
            page.wait_for_selector(selector, timeout=15000)
            print("[INFO] Zone de texte trouvée")
        except Exception:
            print(f"[ERREUR] Zone de texte introuvable : {selector}")
            input("Appuie sur Entrée pour fermer...")
            browser.close()
            return

        # Clic + insertion
        page.click(selector)
        time.sleep(0.3)

        if type_delay > 0:
            # Saisie caractère par caractère (sites simples)
            page.type(selector, prompt_text, delay=type_delay)
        else:
            # Insertion ProseMirror/Tiptap
            insert_text_prosemirror(page, selector, prompt_text)

        print("[OK] Prompt inséré")

        # Submit
        submitted = False

        if submit_sel:
            try:
                page.wait_for_selector(submit_sel, timeout=5000)
                page.click(submit_sel)
                print(f"[OK] Bouton submit cliqué : {submit_sel}")
                submitted = True
            except Exception:
                print(f"[WARN] Bouton submit non trouvé : {submit_sel}")

        if not submitted and submit_key:
            page.keyboard.press(submit_key)
            print(f"[OK] Touche '{submit_key}' pressée")

        print("\n[ACTION] Prompt envoyé. Copie la réponse puis appuie sur Entrée pour fermer.")
        input()

        browser.close()
        print("[INFO] Browser fermé.")


def send_via_api(site_config, prompt_text):
    """
    Placeholder pour les futures intégrations API directes.
    Structure attendue dans config :
    {
      "api": true,
      "api_type": "openai" | "anthropic" | "custom",
      "api_url": "https://...",
      "api_key_env": "OPENAI_API_KEY",
      "model": "gpt-4o"
    }
    """
    print(f"[API] Support API pas encore implémenté pour : {site_config.get('api_type', 'inconnu')}")
    print("[API] Utilise le mode browser pour le moment.")


def main():
    parser = argparse.ArgumentParser(description="Envoie un prompt dans une zone de texte web")
    parser.add_argument("--site",    help="Nom du site dans Config.json (ex: Claude)", required=False)
    parser.add_argument("--prompt",  help="Texte du prompt à envoyer")
    parser.add_argument("--file",    help="Fichier .txt contenant le prompt")
    parser.add_argument("--config",  help="Chemin vers Config.json (défaut: Config.json)")
    parser.add_argument("--list",    action="store_true", help="Lister les sites disponibles")
    parser.add_argument("--headless", action="store_true", help="Mode headless (sans UI)")
    args = parser.parse_args()

    # Charger la config
    config_path = args.config if args.config else DEFAULT_CONFIG_PATH
    try:
        config = load_config(config_path)
    except FileNotFoundError:
        print(f"[ERREUR] Config non trouvée : {config_path}")
        sys.exit(1)

    # Lister les sites
    if args.list:
        list_sites(config)
        sys.exit(0)

    # Sélection du site
    if not args.site:
        if len(config) == 1:
            args.site = list(config.keys())[0]
            print(f"[INFO] Site auto-sélectionné : {args.site}")
        else:
            print("[ERREUR] Plusieurs sites dans la config. Spécifie --site")
            list_sites(config)
            sys.exit(1)

    if args.site not in config:
        print(f"[ERREUR] Site '{args.site}' non trouvé dans la config.")
        list_sites(config)
        sys.exit(1)

    site_config = config[args.site]

    # Surcharge headless
    if args.headless:
        site_config["headless"] = True

    # Récupérer le prompt
    if args.file:
        prompt_text = Path(args.file).read_text(encoding="utf-8").strip()
    elif args.prompt:
        prompt_text = args.prompt
    else:
        print("[INFO] Lecture depuis stdin (Ctrl+Z sur Windows pour terminer)...")
        prompt_text = sys.stdin.read().strip()

    if not prompt_text:
        print("[ERREUR] Prompt vide.")
        sys.exit(1)

    # Routing browser vs API
    if site_config.get("api"):
        send_via_api(site_config, prompt_text)
    else:
        send_via_browser(site_config, prompt_text)


if __name__ == "__main__":
    main()
