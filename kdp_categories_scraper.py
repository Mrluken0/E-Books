import sys
import json
import os
import time
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# =======================================================
# CONFIGURATION
# =======================================================
HEADLESS = False  # Passe sur True pour aller plus vite sans afficher la fenêtre graphique

# On réutilise le MÊME profil persistant authentifié que book_publisher.py :
# la session KDP y est déjà valide, donc le login ci-dessous n'est qu'un fallback rare.
PROFILE_PATH = r"C:\Users\luken\AppData\Local\ms-playwright\kdp-profile"

KDP_URL = "https://kdp.amazon.com/fr_FR/title-setup/kindle/new/details"
OUTPUT_FILE = OUTPUT_FILE = r"C:\Users\luken\.n8n-files\kdp_categories_tree.json"
# Référence de complétude (chemins/feuilles par rubrique L0), figée depuis le 1er tree
# connu-bon. Sert au QC à détecter une branche PARTIELLEMENT incomplète, pas juste absente.
BASELINE_FILE = "kdp_categories_baseline.json"

# Identifiants lus depuis l'environnement (jamais en dur dans le code).
# Utilisés UNIQUEMENT si le profil persistant a perdu sa session.
EMAIL = os.environ.get("KDP_EMAIL_SCRAPER")
MDP = os.environ.get("KDP_PASSWORD_SCRAPER")

# Délai max (ms) d'attente qu'un nœud finisse d'injecter son contenu (select ou checkboxes)
NODE_CONTENT_TIMEOUT = 5000

# Rerun ciblé : si des noms de rubriques niveau 0 sont passés en arguments,
# on n'explore QUE celles-ci (et on les ajoute au JSON existant, sans écraser le reste).
# Ex : python kdp_categories_scraper.py "Santé et Bien-être" "Tourisme et Voyages"
CIBLES_NIVEAU0 = [a.strip() for a in sys.argv[1:] if a.strip()]


def _norm(s: str) -> str:
    """Normalise un libellé : trim + collapse des espaces internes (comme cleanStr côté JS)."""
    return " ".join((s or "").split())


def log(message: str):
    print(f"[SCRAPER] {message}", file=sys.stderr)


def sauvegarder_incrementiel(chemin_noms: list, chemin_node_ids: list, feuilles: list):
    data = {"chemins": []}
    if os.path.exists(OUTPUT_FILE):
        try:
            with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass
    if "chemins" not in data or not isinstance(data.get("chemins"), list):
        data["chemins"] = []
    # Déduplication par chemin de noms (clé stable du nœud)
    data["chemins"] = [c for c in data["chemins"] if c.get("chemin_noms") != chemin_noms]
    data["chemins"].append({
        "chemin_noms": chemin_noms,
        "chemin_node_ids": chemin_node_ids,
        "feuilles": feuilles,
    })
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def controle_qualite(rubriques_niveau0: list):
    """Résumé post-extraction sur stderr : nombre de chemins, de feuilles,
    et rubriques de niveau 0 qui n'apparaissent dans AUCUN chemin (= ratées)."""
    log("=" * 55)
    log("CONTRÔLE QUALITÉ POST-EXTRACTION")
    if not os.path.exists(OUTPUT_FILE):
        log(f"❌ Fichier {OUTPUT_FILE} introuvable : extraction vide ?")
        return

    try:
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        log(f"❌ Impossible de relire {OUTPUT_FILE} : {e}")
        return

    chemins = data.get("chemins", [])
    total_chemins = len(chemins)
    total_feuilles = sum(len(c.get("feuilles", [])) for c in chemins)

    # Rubriques niveau 0 réellement présentes (position 0 de chaque chemin)
    presents = {c["chemin_noms"][0] for c in chemins if c.get("chemin_noms")}
    manquantes = [r for r in rubriques_niveau0 if r not in presents]

    log(f"Chemins (feuilles-listes) extraits : {total_chemins}")
    log(f"Feuilles (catégories finales) au total : {total_feuilles}")

    if not rubriques_niveau0:
        log("⚠️ Liste des rubriques niveau 0 inconnue (le parcours n'a rien capturé "
            "au niveau 0) : impossible de vérifier l'exhaustivité.")
    elif manquantes:
        log(f"⚠️ {len(manquantes)} rubrique(s) niveau 0 ABSENTE(S) de tout chemin "
            f"(à relancer/vérifier) :")
        for r in manquantes:
            log(f"   - {r}")
    else:
        log(f"✅ Les {len(rubriques_niveau0)} rubriques de niveau 0 apparaissent "
            f"toutes dans au moins un chemin.")

    # --- Détection d'incomplétude PARTIELLE : comparaison par rubrique vs baseline ---
    if os.path.exists(BASELINE_FILE):
        try:
            with open(BASELINE_FILE, "r", encoding="utf-8") as f:
                base = json.load(f).get("par_rubrique", {})
        except Exception:
            base = {}
        courant = _compter_par_l0(chemins)
        deficits = []
        for name, ref in base.items():
            cur = courant.get(name, {"chemins": 0, "feuilles": 0})
            if cur["chemins"] < ref["chemins"] or cur["feuilles"] < ref["feuilles"]:
                deficits.append((name, cur, ref))
        if deficits:
            log(f"⚠️ {len(deficits)} rubrique(s) EN DÉFICIT vs baseline "
                f"(capture partielle probable — à relancer/inspecter) :")
            for name, cur, ref in deficits:
                log(f"   - {name}: {cur['chemins']}/{ref['chemins']} chemins, "
                    f"{cur['feuilles']}/{ref['feuilles']} feuilles")
        else:
            log("✅ Complétude vs baseline OK : aucune rubrique en déficit de "
                "chemins/feuilles.")
    else:
        log("ℹ️ Pas de baseline de référence : détection d'incomplétude partielle "
            "indisponible (seule l'absence totale est détectée).")
    log("=" * 55)


def _compter_par_l0(chemins: list) -> dict:
    """Retourne {nom_L0: {'chemins': n, 'feuilles': n}} — tolère l'ancien ('chemin')
    et le nouveau ('chemin_noms') schéma."""
    stats = {}
    for c in chemins:
        chemin = c.get("chemin_noms") or c.get("chemin")
        if not chemin:
            continue
        s = stats.setdefault(chemin[0], {"chemins": 0, "feuilles": 0})
        s["chemins"] += 1
        s["feuilles"] += len(c.get("feuilles", []))
    return stats


def _generer_baseline_si_absente():
    """Fige, depuis le tree existant connu-bon, une référence de complétude par
    rubrique L0. Générée UNE seule fois : les runs suivants comparent contre elle."""
    if os.path.exists(BASELINE_FILE) or not os.path.exists(OUTPUT_FILE):
        return
    try:
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            chemins = json.load(f).get("chemins", [])
    except Exception:
        return
    stats = _compter_par_l0(chemins)
    if not stats:
        return
    with open(BASELINE_FILE, "w", encoding="utf-8") as f:
        json.dump({"par_rubrique": stats}, f, ensure_ascii=False, indent=2)
    log(f"Baseline de complétude générée depuis le tree existant → {BASELINE_FILE} "
        f"({len(stats)} rubriques).")


def _est_ancien_format() -> bool:
    """True si le JSON de sortie existant utilise l'ancien schéma (clé 'chemin'
    au lieu de 'chemin_node_ids'). Incompatible : on ne peut pas le migrer."""
    try:
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            ch = json.load(f).get("chemins", [])
        return bool(ch) and "chemin_node_ids" not in ch[0]
    except Exception:
        return False


def main():
    rubriques_niveau0 = []  # capturé au niveau 0 pour le contrôle qualité final

    # Le nouveau schéma (chemin_node_ids) est incompatible avec l'ancien tree.
    # Sur un run COMPLET, si l'ancien format est présent, on l'archive et on repart de zéro.
    # (Un rerun ciblé conserve le fichier au nouveau format pour permettre la reprise.)
    if not CIBLES_NIVEAU0:
        # Fige la baseline de complétude AVANT d'archiver l'ancien tree connu-bon.
        _generer_baseline_si_absente()
        if os.path.exists(OUTPUT_FILE) and _est_ancien_format():
            backup = OUTPUT_FILE.replace(".json", f".ancien-format-{int(time.time())}.json")
            os.replace(OUTPUT_FILE, backup)
            log(f"Ancien tree (sans nodeId intermédiaires) archivé sous : {backup}")
            log("Run complet depuis zéro avec le nouveau schéma chemin_node_ids.")

    if not EMAIL or not MDP:
        log("⚠️ Variables KDP_EMAIL_SCRAPER / KDP_PASSWORD_SCRAPER absentes : "
            "le login de secours sera IMPOSSIBLE si la session du profil a expiré.")

    with sync_playwright() as p:
        log("Lancement du navigateur (profil persistant)...")
        context = p.chromium.launch_persistent_context(
            user_data_dir=PROFILE_PATH,
            headless=HEADLESS,
            viewport={'width': 1280, 'height': 800}
        )
        page = context.pages[0] if context.pages else context.new_page()

        log(f"Navigation vers : {KDP_URL}")
        page.goto(KDP_URL, timeout=60000)

        # --- Login = fallback rare (le profil est normalement déjà connecté) ---
        try:
            page.locator("#ap_email").wait_for(state="visible", timeout=5000)
            log("Session expirée : connexion de secours requise...")
            if not EMAIL or not MDP:
                raise RuntimeError(
                    "Login KDP requis mais KDP_EMAIL_SCRAPER / KDP_PASSWORD_SCRAPER "
                    "ne sont pas définies. Définis ces variables d'environnement, "
                    "ou reconnecte manuellement le profil, puis relance."
                )
            page.fill("#ap_email", EMAIL)
            page.click("#continue")
            page.locator("#ap_password").wait_for(state="visible")
            page.fill("#ap_password", MDP)
            page.click("#signInSubmit")
            page.wait_for_url(lambda url: "/ap/signin" not in url, timeout=15000)
            log("Connexion de secours réussie.")
        except PlaywrightTimeoutError:
            log("Déjà connecté via le profil persistant.")

        log("Validation du contenu adulte (Non)...")
        adult_radio = page.locator('input[name="data[is_adult_content]-radio"][value="false"]')
        adult_radio.wait_for(state="attached")
        adult_radio.click(force=True)

        def verifier_et_ouvrir_modal():
            if not page.locator('.a-popover-modal').first.is_visible():
                log("🔄 [MODAL] Réouverture de la fenêtre des rubriques...")
                page.locator('#categories-modal-button').click()
                page.wait_for_selector('.a-popover-modal', state="visible")
                page.wait_for_timeout(300)

        def reset_modal():
            """Ferme puis rouvre la modale pour repartir d'un select[0] PROPRE.
            Reproduit l'état 'frais' qui fait réussir les runs ciblés, quand un run
            complet laisse la modale dans un état sale après de gros sous-arbres."""
            log("♻️ [MODAL] Reset complet (fermeture + réouverture)...")
            try:
                fermeture = page.locator(
                    '.a-popover-modal .a-button-close, '
                    '.a-popover-modal [data-action="a-popover-close"]'
                )
                if fermeture.count() and fermeture.first.is_visible():
                    fermeture.first.click()
                else:
                    page.keyboard.press("Escape")
            except Exception:
                try:
                    page.keyboard.press("Escape")
                except Exception:
                    pass
            # Attend que la modale disparaisse réellement avant de la rouvrir
            try:
                page.wait_for_selector('.a-popover-modal', state="hidden", timeout=3000)
            except PlaywrightTimeoutError:
                pass
            page.wait_for_timeout(300)
            verifier_et_ouvrir_modal()

        def recharger_page():
            """Recharge COMPLÈTEMENT la page pour restaurer l'état pristine du select[0].
            Certaines rubriques niveau 0 ne sont présentes dans la liste qu'au tout premier
            affichage : dès la 1re interaction, React réconcilie la liste et les retire.
            Seul un rechargement (comme un run ciblé) les fait réapparaître."""
            log("🔁 [PAGE] Rechargement complet (restauration de la liste niveau 0 pristine)...")
            page.goto(KDP_URL, timeout=60000)
            adult = page.locator('input[name="data[is_adult_content]-radio"][value="false"]')
            adult.wait_for(state="attached")
            adult.click(force=True)
            verifier_et_ouvrir_modal()

        verifier_et_ouvrir_modal()

        def attendre_contenu_noeud(niveau) -> bool:
            """Attend qu'un nœud fraîchement sélectionné ait fini de charger :
            soit un select plus profond (nœud intermédiaire), soit au moins une
            checkbox visible (feuille). Remplace le wait_for_timeout fixe.
            Retourne True si du contenu est apparu, False sur TIMEOUT."""
            try:
                page.wait_for_function(
                    """(niveau) => {
                        const selects = document.querySelectorAll('.a-popover-modal select');
                        if (selects.length > niveau + 1) return true;
                        const cbs = [...document.querySelectorAll('input[type=checkbox]')]
                            .filter(c => c.offsetParent !== null);
                        return cbs.length > 0;
                    }""",
                    arg=niveau,
                    timeout=NODE_CONTENT_TIMEOUT,
                )
                return True
            except PlaywrightTimeoutError:
                return False

        def attendre_option_dispo(niveau, val, nom) -> bool:
            """Attend que le select[niveau] contienne réellement l'option visée avant
            de tenter la sélection. Corrige les échecs transitoires au retour d'un
            sous-arbre DFS, quand le select est encore en cours de re-remplissage."""
            try:
                page.wait_for_function(
                    """([idx, val, nom]) => {
                        const cleanStr = (s) => s ? s.trim().replace(/\\s+/g, ' ') : '';
                        const selects = document.querySelectorAll('.a-popover-modal select');
                        const select = selects[idx];
                        if (!select) return false;
                        return Array.from(select.options).some(
                            o => o.value === val || cleanStr(o.innerText) === cleanStr(nom));
                    }""",
                    arg=[niveau, val, nom],
                    timeout=NODE_CONTENT_TIMEOUT,
                )
                return True
            except PlaywrightTimeoutError:
                return False

        def restaurer_chemin(chemin) -> bool:
            """Réaligne les selects de la modale sur `chemin`. Attend explicitement
            que chaque select existe avant de le régler (au lieu d'un no-op silencieux),
            et log un WARNING en cas d'échec. Retourne True si tout le chemin a été rejoué."""
            if not chemin:
                return True
            log(f"🔄 [AUTO-GUÉRISON] Réalignement sur : {' > '.join(chemin)}")
            verifier_et_ouvrir_modal()
            for i, nom_parent in enumerate(chemin):
                try:
                    page.wait_for_function(
                        "(idx) => document.querySelectorAll('.a-popover-modal select').length > idx",
                        arg=i,
                        timeout=NODE_CONTENT_TIMEOUT,
                    )
                except PlaywrightTimeoutError:
                    log(f"⚠️ [AUTO-GUÉRISON] Select niveau {i} jamais apparu "
                        f"pour restaurer '{nom_parent}'.")
                    return False

                ok = page.evaluate("""([idx, nom]) => {
                    const cleanStr = (s) => s ? s.trim().replace(/\\s+/g, ' ') : '';
                    const selects = Array.from(document.querySelectorAll('.a-popover-modal select'));
                    if (idx < selects.length) {
                        const select = selects[idx];
                        const opt = Array.from(select.options).find(o => cleanStr(o.innerText) === cleanStr(nom));
                        if (opt) {
                            select.value = opt.value;
                            select.dispatchEvent(new Event('change', { bubbles: true }));
                            return true;
                        }
                    }
                    return false;
                }""", [i, nom_parent])

                if not ok:
                    log(f"⚠️ [AUTO-GUÉRISON] Option '{nom_parent}' (niveau {i}) "
                        f"introuvable pendant la restauration.")
                    return False
                # Laisse le change se propager avant de viser le select suivant
                page.wait_for_timeout(200)
            return True

        def parcourir_noeud(chemin_actuel, chemin_ids_actuel, niveau):
            verifier_et_ouvrir_modal()
            selector = ".a-popover-modal select"
            select_locator = page.locator(selector)

            total_selects = select_locator.count()
            if total_selects <= niveau:
                return

            options = select_locator.nth(niveau).evaluate("""(selectEl) => {
                return Array.from(selectEl.querySelectorAll('option'))
                    .map(opt => {
                        let key = opt.innerText.trim();
                        let nodeId = null;
                        const valAttr = opt.getAttribute('value');
                        if (!valAttr || valAttr === "") return null;
                        try {
                            const data = JSON.parse(valAttr);
                            if (data) {
                                if (data.nodeId) nodeId = data.nodeId;
                                else if (data.id) nodeId = data.id;
                                else if (data.stringVal) {
                                    const internal = JSON.parse(data.stringVal);
                                    nodeId = internal.nodeId || internal.key;
                                }
                            }
                        } catch(e) {
                            nodeId = valAttr;
                        }
                        if (!nodeId) nodeId = valAttr;
                        return { value: valAttr, nom: key, nodeId: nodeId };
                    }).filter(o => o !== null && o.nom !== "Sélectionner une valeur");
            }""")

            # Mémorise la liste des rubriques niveau 0 pour le contrôle qualité,
            # et applique le filtre de rerun ciblé s'il y en a un.
            if niveau == 0:
                noms_dispo = [o['nom'] for o in options]
                if CIBLES_NIVEAU0:
                    cibles_norm = {_norm(c) for c in CIBLES_NIVEAU0}
                    options = [o for o in options if _norm(o['nom']) in cibles_norm]
                    rubriques_niveau0[:] = [o['nom'] for o in options]
                    manquant_du_select = [
                        c for c in CIBLES_NIVEAU0
                        if _norm(c) not in {_norm(n) for n in noms_dispo}
                    ]
                    if manquant_du_select:
                        log(f"⚠️ Cibles demandées mais ABSENTES du select niveau 0 "
                            f"(vérifie l'orthographe exacte) : {manquant_du_select}")
                    log(f"Rerun ciblé : {len(options)} rubrique(s) niveau 0 à explorer.")
                else:
                    rubriques_niveau0[:] = noms_dispo

            for opt in options:
                nouveau_chemin = chemin_actuel + [opt['nom']]
                nouveau_chemin_ids = chemin_ids_actuel + [opt['nodeId']]
                log(f"Exploration : {' > '.join(nouveau_chemin)}")

                def executer_selection():
                    verifier_et_ouvrir_modal()
                    return page.evaluate("""([idx, val, nom]) => {
                        const cleanStr = (s) => s ? s.trim().replace(/\\s+/g, ' ') : '';
                        const selects = Array.from(document.querySelectorAll('.a-popover-modal select'));
                        let select = selects[idx] || selects[selects.length - 1];
                        if (!select) return { success: false };

                        let targetOpt = Array.from(select.options).find(o => o.value === val || cleanStr(o.innerText) === cleanStr(nom));
                        if (!targetOpt) {
                            for (const s of selects) {
                                targetOpt = Array.from(s.options).find(o => o.value === val || cleanStr(o.innerText) === cleanStr(nom));
                                if (targetOpt) { select = s; break; }
                            }
                        }
                        if (select && targetOpt) {
                            select.value = targetOpt.value;
                            select.dispatchEvent(new Event('change', { bubbles: true }));
                            return { success: true };
                        }
                        return { success: false, dispo: selects.map(s => Array.from(s.options).map(o => o.innerText.trim())) };
                    }""", [niveau, opt['value'], opt['nom']])

                # Attend que l'option cible soit réellement présente dans le select
                # (le select peut être en cours de re-remplissage au retour d'un sous-arbre).
                verifier_et_ouvrir_modal()
                attendre_option_dispo(niveau, opt['value'], opt['nom'])

                selection = {"success": False}
                for tentative in range(3):
                    selection = executer_selection()
                    if selection["success"]:
                        break
                    page.wait_for_timeout(300)

                # Fallback niveau 1 : réaligne le chemin parent puis retente
                if not selection["success"]:
                    if niveau > 0:
                        restaurer_chemin(chemin_actuel)
                    else:
                        page.wait_for_timeout(400)
                    selection = executer_selection()

                # Fallback niveau 2 : la modale est peut-être dans un état sale.
                # On la RESET pour repartir propre, on réaligne le parent si besoin, on retente.
                if not selection["success"]:
                    reset_modal()
                    if niveau > 0:
                        restaurer_chemin(chemin_actuel)
                    attendre_option_dispo(niveau, opt['value'], opt['nom'])
                    selection = executer_selection()

                # Fallback niveau 3 (rubriques niveau 0 uniquement) : l'option a disparu
                # de select[0] après la réconciliation React. Seul un rechargement complet
                # de la page restaure la liste pristine. Sans risque ici : chemin parent vide.
                if not selection["success"] and niveau == 0:
                    recharger_page()
                    attendre_option_dispo(niveau, opt['value'], opt['nom'])
                    selection = executer_selection()

                if not selection["success"]:
                    # Diagnostic : que contenait réellement chaque select au moment de l'abandon ?
                    dispo = selection.get("dispo")
                    apercu = ""
                    if dispo:
                        apercu = " | selects vus: " + " || ".join(
                            f"[{i}] " + ", ".join(opts[:8]) + ("…" if len(opts) > 8 else "")
                            for i, opts in enumerate(dispo)
                        )
                    log(f"❌ [ÉCHEC] Option '{opt['nom']}' introuvable "
                        f"(chemin {' > '.join(nouveau_chemin)}) — nœud sauté.{apercu}")
                    continue

                # Attente ROBUSTE : soit sous-select, soit checkboxes visibles (plus de timeout aveugle)
                a_du_contenu = attendre_contenu_noeud(niveau)

                feuilles = page.evaluate("""() => {
                    return [...document.querySelectorAll('input[type=checkbox]')]
                        .filter(c => c.offsetParent !== null)
                        .map(c => {
                            const cls = [...c.classList].find(x => x.startsWith('checkbox-')) || '';
                            return {
                                "nom": (c.closest('label')?.innerText || '').trim(),
                                "nodeId": cls.replace('checkbox-', '')
                            };
                        });
                }""")

                a_sous_niveau = page.locator(selector).count() > (niveau + 1)

                if feuilles:
                    sauvegarder_incrementiel(nouveau_chemin, nouveau_chemin_ids, feuilles)
                    log(f" -> {len(feuilles)} feuilles enregistrées.")
                elif not a_sous_niveau:
                    # Ni checkbox ni sous-niveau : anomalie => perte de donnée potentielle, on le SIGNALE
                    detail = "" if a_du_contenu else " (TIMEOUT d'attente atteint — donnée probablement perdue)"
                    log(f"⚠️ [WARNING] Aucune feuille ni sous-niveau détecté pour : "
                        f"{' > '.join(nouveau_chemin)}{detail}")

                if a_sous_niveau:
                    parcourir_noeud(nouveau_chemin, nouveau_chemin_ids, niveau + 1)

        log("Début du parcours de l'arbre des catégories...")
        parcourir_noeud([], [], 0)
        log(f"Extraction terminée ! Fichier disponible : {OUTPUT_FILE}")

        controle_qualite(rubriques_niveau0)
        context.close()


if __name__ == "__main__":
    main()
