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


def wait_for_response(page, response_selector="[data-is-streaming]", timeout=120):
    """
    Attend le début puis la fin du streaming.
    Retourne le texte de la réponse ou None si vide/timeout.
    """
    print("[INFO] Attente du début de la réponse...", flush=True)

    # Attendre que le streaming commence (max 15s)
    for _ in range(30):
        if page.query_selector(response_selector):
            break
        time.sleep(0.5)
    else:
        return None

    print("[INFO] Streaming en cours...", flush=True)

    # Attendre la fin du streaming
    elapsed = 0
    last_text = ""
    while elapsed < timeout:
        el = page.query_selector(response_selector)
        if el:
            last_text = el.inner_text()
        else:
            # Streaming terminé — récupérer le texte final
            break
        time.sleep(0.5)
        elapsed += 0.5

    # Récupérer le texte final
    final_text = page.evaluate("""
        (() => {
            const streaming = document.querySelector('[data-is-streaming]');
            if (streaming) return streaming.innerText;
            const sels = [
                '[data-testid="assistant-message"]',
                '.font-claude-message',
                'div[data-role="assistant"]'
            ];
            for (const sel of sels) {
                const els = document.querySelectorAll(sel);
                if (els.length > 0) return els[els.length - 1].innerText;
            }
            return null;
        })()
    """)

    return (final_text or last_text or "").strip() or None


def save_html_debug(page):
    """Sauvegarde le HTML de la page pour analyse."""
    try:
        html = page.content()
        HTML_DUMP_PATH.write_text(html, encoding="utf-8")
        print(f"[DEBUG] HTML sauvegardé : {HTML_DUMP_PATH}", flush=True)
    except Exception as e:
        print(f"[DEBUG] Erreur sauvegarde HTML : {e}", flush=True)


def delete_conversation(page):
    """Supprime la conversation courante et navigue vers une nouvelle page."""
    try:
        # Ouvrir le menu "Plus d'options"
        menu_btn = page.query_selector("button[aria-label*=\"Plus d'options\"], button[aria-label*='More options']")
        if not menu_btn:
            # Chercher par aria-label partiel
            menu_btn = page.query_selector("button[aria-label*='options' i]")
        if not menu_btn:
            print("[WARN] Bouton options non trouvé — suppression ignorée", flush=True)
            return

        menu_btn.click()
        time.sleep(0.5)

        # Cliquer Delete
        delete_trigger = page.query_selector("[data-testid='delete-chat-trigger']")
        if not delete_trigger:
            print("[WARN] Bouton delete non trouvé — suppression ignorée", flush=True)
            return

        delete_trigger.click()
        time.sleep(0.5)

        # Confirmer la suppression
        confirm_btn = page.query_selector("button[class*='danger']")
        if not confirm_btn:
            print("[WARN] Bouton confirmation non trouvé — suppression ignorée", flush=True)
            return

        confirm_btn.click()
        time.sleep(1.0)
        print("[OK] Conversation supprimée", flush=True)

    except Exception as e:
        print(f"[WARN] Erreur suppression conversation : {e}", flush=True)


def send_prompt_once(page, selector, submit_sel, submit_key, type_delay, prompt_text):
    """Insère et soumet le prompt sur la page courante."""
    page.wait_for_selector(selector, timeout=15000)
    page.click(selector)
    time.sleep(0.3)

    if type_delay > 0:
        page.type(selector, prompt_text, delay=type_delay)
    else:
        insert_text_prosemirror(page, selector, prompt_text)

    time.sleep(0.5)

    submitted = False
    if submit_sel:
        try:
            page.wait_for_selector(submit_sel, timeout=3000)
            page.click(submit_sel)
            submitted = True
        except Exception:
            pass
    if not submitted and submit_key:
        page.keyboard.press(submit_key)

    print("[INFO] Prompt soumis", flush=True)


def send_via_browser(site_config, prompt_text):
    """Workflow complet : envoie, attend, retry si vide, supprime, retourne JSON."""

    url              = site_config.get("url")
    selector         = site_config.get("selector")
    submit_sel       = site_config.get("submit_selector")
    submit_key       = site_config.get("submit_key")
    response_sel     = site_config.get("response_selector", "[data-is-streaming]")
    wait_before      = site_config.get("wait_before_type", 2.0)
    type_delay       = site_config.get("type_delay", 0)
    headless         = site_config.get("headless", False)
    response_timeout = site_config.get("response_timeout", 120)
    max_retries      = site_config.get("max_retries", 3)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()

        response_text = None

        for attempt in range(1, max_retries + 1):
            print(f"[INFO] Tentative {attempt}/{max_retries}", flush=True)

            # Naviguer vers une page vide à chaque tentative
            page.goto(url, wait_until="domcontentloaded")
            print("[INFO] Page chargée", flush=True)
            time.sleep(wait_before)

            try:
                send_prompt_once(page, selector, submit_sel, submit_key, type_delay, prompt_text)
            except Exception as e:
                print(f"[ERREUR] Envoi prompt : {e}", flush=True)
                continue

            # Attendre la réponse
            response_text = wait_for_response(page, response_sel, response_timeout)

            if response_text:
                print(f"[OK] Réponse obtenue ({len(response_text)} chars)", flush=True)
                # Supprimer la conversation
                delete_conversation(page)
                break
            else:
                print(f"[WARN] Réponse vide — sauvegarde HTML debug...", flush=True)
                save_html_debug(page)
                # Supprimer la conversation vide avant retry
                delete_conversation(page)
                time.sleep(2.0)

        browser.close()

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
