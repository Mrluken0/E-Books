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

# URL du formulaire de création d'un ebook (site FR)
KDP_NEW_EBOOK_URL = "https://kdp.amazon.com/fr_FR/title-setup/kindle/new/details"

# Le <select> langue de KDP utilise des libellés natifs ("french", "english"...),
# PAS les codes ISO ("fr", "en"). Mapping des codes config -> valeurs KDP.
# (vérifié en live sur #data-language-native)
LANG_MAP = {
    "fr": "french",
    "en": "english",
    "de": "german",
    "es": "spanish",
    "it": "italian",
    "pt": "portuguese",
    "nl": "dutch",
    "ja": "japanese",
}

# Codes marketplace KDP = valeur des <option> du select
# name="data[digital][home_marketplace]", indexés par libellé affiché
# (= SITE_VENTE_PRINCIPAL_KDP). Vérifié en live sur la page /details (2026-07).
MARKETPLACE_MAP = {
    "Amazon.com": "US",
    "Amazon.in": "IN",
    "Amazon.co.uk": "UK",
    "Amazon.de": "DE",
    "Amazon.fr": "FR",
    "Amazon.es": "ES",
    "Amazon.it": "IT",
    "Amazon.nl": "NL",
    "Amazon.co.jp": "JP",
    "Amazon.com.br": "BR",
    "Amazon.ca": "CA",
    "Amazon.com.mx": "MX",
    "Amazon.com.au": "AU",
}

# Constantes étape 1 — communes à tous les livres de ce pipeline
LANGUE_KDP = "Français"
DROIT_PUBLICATION_KDP = "Je détiens les droits d'auteur et possède les droits de publication requis."
SITE_VENTE_PRINCIPAL_KDP = "Amazon.fr"
OPTION_PUBLICATION_KDP = "Paraître maintenant"

def log(message):
    """Écrit les logs intermédiaires sur stderr pour ne pas polluer stdout."""
    print(f"[LOG] {message}", file=sys.stderr)


def read_config(path):
    log(f"Lecture de la configuration : {path}")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Le fichier de configuration {path} n'existe pas.")
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def ensure_logged_in(page, config):
    """
    Vérifie qu'on est bien connecté à KDP. Avec le profil persistant kdp-profile,
    la session reste valide longtemps : aucun login dans le code en temps normal.

    Si la session a expiré, KDP redirige vers /ap/signin.

    ┌──────────────────────────────────────────────────────────────────────┐
    │ >>> ZONE LOGIN — À CODER PAR TOI <<<                                   │
    │                                                                        │
    │ Sélecteurs STANDARD Amazon (à confirmer en live, non vérifiés ici) :   │
    │   - Champ email/login ......... #ap_email   (ou #ap_email_login)        │
    │   - Bouton "Continuer" ........ #continue                               │
    │   - Champ mot de passe ........ #ap_password                           │
    │   - Bouton "Se connecter" ..... #signInSubmit                          │
    │   - (2FA éventuel OTP) ......... #auth-mfa-otpcode + #auth-signin-button│
    │                                                                        │
    │ Récupère email/mdp depuis une source SÉCURISÉE (variable d'env n8n,    │
    │ fichier hors repo, gestionnaire de secrets) — JAMAIS en clair dans     │
    │ kdp_config.json qui est poussé sur GitHub.                             │
    │                                                                        │
    │ Exemple de structure à compléter :                                     │
    │   page.fill("#ap_email", os.environ["KDP_EMAIL"])                      │
    │   page.click("#continue")                                              │
    │   page.fill("#ap_password", os.environ["KDP_PASSWORD"])                │
    │   page.click("#signInSubmit")                                          │
    │   # gérer ici un éventuel code 2FA...                                   │
    └──────────────────────────────────────────────────────────────────────┘
    """
    if "/ap/signin" not in page.url:
        return  # déjà connecté via le profil persistant

    log("Session KDP expirée — page de connexion détectée.")

    # >>> Insère ICI ton code de connexion (voir docstring ci-dessus). <<<
    email = config["email"]
    mdp = config["mot_de_passe"]
    
    page.pause()

    page.fill("#ap_email", email)
    page.click("#continue")
    page.fill("#ap_password", mdp)
    page.click("#signInSubmit")

    page.wait_for_url(lambda url: "/ap/signin" not in url, timeout=15000)
    log("Connexion réussie.")


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
        # SÉLECTEURS VÉRIFIÉS EN LIVE (KDP fr_FR, ebook, 2026-06) :
        #   Langue        : #data-language-native (select, valeurs natives via LANG_MAP)
        #   Titre         : #data-title
        #   Sous-titre    : #data-subtitle
        #   Auteur prénom : #data-primary-author-first-name
        #   Auteur nom    : #data-primary-author-last-name
        #   Description   : CKEditor instance "editor1" (voir _fill_description)
        #   Droits (perso): #non-public-domain (radio "Je détiens les droits...")
        #   Mots-clés     : #data-keywords-0 .. #data-keywords-6
        #   Contenu adulte: input[name="data[is_adult_content]-radio"][value="false"]
        #   Catégories    : bouton #categories-modal-button (modal à mapper, voir TODO)
        #   Continuer     : #save-and-continue   (brouillon : #save)
        # =====================================================================

        # --- Langue ---
        langue_kdp = LANG_MAP.get(config.get("langue", "fr").lower(), "french")

        # Force la valeur directement via JS sur le select natif
        page.evaluate(f"""
            var sel = document.querySelector('#data-language-native');
            sel.value = '{langue_kdp}';
            sel.dispatchEvent(new Event('change', {{bubbles: true}}));
        """)


        # --- Titre / sous-titre ---
        page.fill("#data-title", config["titre_livre"])
        if config.get("sous_titre"):
            page.fill("#data-subtitle", config["sous_titre"])

        # --- Série ---
        # A coder

        # --- Auteur principal ---
        page.fill("#data-primary-author-first-name", prenom)
        page.fill("#data-primary-author-last-name", nom)
        
        page.pause()  # Pause pour vérifier le remplissage avant de continuer
        
        # --- Description (CKEditor, voir helper dédié) ---
        _fill_description(page, config["description"])

        
        # --- Droits de publication : je détiens les droits ---
        page.check("#non-public-domain")

        
        # --- Contenu pour public adulte (connotation sexuelle) : oui / non ---
        # Piloté par le bool config["contenu_adulte"]. Rien d'autre à renseigner :
        # la tranche d'âge de lecture (data[reading_interest_age]) sert à distinguer
        # les livres pour ENFANTS, pas le contenu adulte -> volontairement ignorée.
        page.check('input[name="data[is_adult_content]-radio"][value="false"]')
        if config.get("contenu_adulte"):
            page.check('input[name="data[is_adult_content]-radio"][value="true"]')


        # --- Site de vente principal (Amazon.fr) ---
        # Vérifié en live : c'est le MÊME champ que l'étape 3
        # (select[name="data[digital][home_marketplace]"]), présent AUSSI sur la
        # page /details mais avec un défaut = US (Amazon.com). On le règle sur FR
        # ici par sécurité (en plus de l'étape 3), via le sélecteur éprouvé.
        page.select_option(
            'select[name="data[digital][home_marketplace]"]',
            value=MARKETPLACE_MAP.get(SITE_VENTE_PRINCIPAL_KDP, "FR"),
        )
        page.wait_for_timeout(1500)

        #page.pause()

        # --- Option de publication (Paraître maintenant / Précommande) ---
        _set_publish_option(page, config.get("option_publication", OPTION_PUBLICATION_KDP))

        
        # --- Rubriques + classement (modal) ---
        select_categories(page, config)


        # --- Mots-clés (7 champs) ---
        for i, mot in enumerate(mots_cles):
            page.fill(f"#data-keywords-{i}", mot)

        # --- Enregistrer et continuer vers l'étape Contenu ---
        page.click("#save-and-continue")

    except TimeoutError as e:
        raise Exception(f"Timeout étape 1 (détails livre) — sélecteur introuvable : {str(e)}")
    except KeyError as e:
        raise Exception(f"Erreur étape 1 (détails livre) — clé de config manquante : {str(e)}")
    except Exception as e:
        raise Exception(f"Erreur étape 1 (détails livre) : {str(e)}")


def _set_publish_option(page, option_pub):
    """
    Sélectionne l'option de publication sur la page /details.

    /!\\ Vérifié en live : ce N'EST PAS un radio <input> mais un ACCORDÉON Amazon
    (#data-preorder-enabled-accordion) à 2 lignes ; l'état réel est stocké dans
    l'input caché name="data[preorder][enabled]" :
      - "Paraître maintenant" -> ligne data-a-accordion-row-name="off" (enabled=false,
        « Je souhaite que mon livre paraisse maintenant » — active par défaut)
      - "Précommande"         -> ligne data-a-accordion-row-name="on"  (enabled=true,
        « Proposer mon ebook Kindle en précommande »)
    On clique le lien .a-accordion-row de la ligne voulue (sélecteurs stables).

    /!\\ PRÉCOMMANDE : exige EN PLUS une date de parution
    (#data-preorder-release-date-input, datepicker) et un compte éligible.
    Jamais utilisé dans ce pipeline -> volet date NON automatisé (page.pause()
    pour finalisation live le jour où un vrai livre partira en précommande).
    """
    lignes = {"Paraître maintenant": "off", "Précommande": "on"}
    row = lignes.get(option_pub)
    if row is None:
        raise Exception(
            f"option_publication invalide : {option_pub!r} "
            "(attendu 'Paraître maintenant' ou 'Précommande')."
        )

    log(f"Option de publication : {option_pub!r} (accordéon ligne '{row}').")
    page.click(
        f'#data-preorder-enabled-accordion '
        f'[data-a-accordion-row-name="{row}"] .a-accordion-row'
    )

    if row == "on":
        # Cas précommande : date de parution + éligibilité à finaliser en live.
        log(">>> PAUSE PRÉCOMMANDE : renseigner la date de parution "
            "(#data-preorder-release-date-input) et vérifier l'éligibilité du "
            "compte. Volet non encore automatisé — m'envoyer les infos si besoin.")
        page.pause()
        # TODO(précommande) : remplir la date de parution puis valider.


def _fill_description(page, description):
    """
    Remplit la description KDP.

    /!\\ Vérifié en live : KDP utilise CKEditor (et NON TinyMCE).
    Instance CKEditor = "editor1", iframe d'édition = iframe.cke_wysiwyg_frame,
    input caché synchronisé = [name="data[description]"].

    Stratégie :
      1. API CKEditor : CKEDITOR.instances.editor1.setData(...) — le plus fiable,
         ça met aussi à jour l'input caché du formulaire.
      2. Fallback : écrire directement dans le <body> de l'iframe CKEditor.
    """
    log("Remplissage de la description (CKEditor)...")
    safe_desc = json.dumps(description)  # échappe quotes / sauts de ligne pour le JS

    # --- 1) Voie privilégiée : API CKEditor ---
    ok = page.evaluate(
        """(html) => {
            try {
                if (window.CKEDITOR && CKEDITOR.instances && CKEDITOR.instances.editor1) {
                    CKEDITOR.instances.editor1.setData(html);
                    return true;
                }
            } catch (e) {}
            return false;
        }""",
        description,
    )
    if ok:
        return

    # --- 2) Fallback : <body> de l'iframe d'édition CKEditor ---
    log("API CKEditor indisponible — fallback iframe.cke_wysiwyg_frame")
    frame = page.frame_locator("iframe.cke_wysiwyg_frame")
    frame.locator("body").fill(description)
    _ = safe_desc  # conservé si besoin d'une injection JS manuelle ultérieure


def _normalize_categories(categories):
    """
    Normalise config["categories"] en liste d'entrées CASCADE :
      {chemin_node_ids: [<nodeId L0>, ..., <nodeId dernier select>],
       classement: <nodeId feuille>, libelle: <str>}

    Format attendu (cascade N niveaux par nodeId, aligné sur kdp_categories_tree.json) :
      "categories": [
        {"chemin_node_ids": ["156915011", "156968011"],
         "classement": "156969011",
         "libelle": "Droit > Code de la propriété intellectuelle > Communications"},
        ...   (3 rubriques maximum)
      ]
      - chemin_node_ids = liste ORDONNÉE des nodeId des <select> à poser, du niveau 0
                          jusqu'au dernier select qui révèle la feuille de classement.
                          (= champ "chemin_node_ids" d'une entrée du tree)
      - classement      = nodeId de la FEUILLE (case class="checkbox-<nodeId>", libellé EN)
                          (= "nodeId" d'une entrée de "feuilles" du tree)
      - libelle         = facultatif, purement informatif (logs)

    Les nodeId sont indépendants de la langue et de l'orthographe -> robustes.
    (Ne PAS mettre de préfixe "Kindle Store"/"Kindle eBooks" : contexte déjà fixe.)
    """
    if isinstance(categories, dict):
        categories = [categories]
    if not categories:
        return []
    rubriques = []
    for cat in categories:
        if not isinstance(cat, dict) or "chemin_node_ids" not in cat or "classement" not in cat:
            raise Exception(
                "Format categories invalide : chaque entrée doit être un objet "
                "{'chemin_node_ids': [<nodeId L0>, ...], 'classement': <nodeId feuille>}. "
                "Reçu : " + repr(cat)
            )
        chemin = cat["chemin_node_ids"]
        if not isinstance(chemin, list) or not chemin or not all(chemin):
            raise Exception(
                "chemin_node_ids doit être une liste NON VIDE de nodeId "
                "(L0 -> ... -> dernier select). Reçu : " + repr(chemin)
            )
        rubriques.append({
            "chemin_node_ids": [str(x) for x in chemin],
            "classement": str(cat["classement"]),
            "libelle": cat.get("libelle"),
        })
    return rubriques[:3]  # KDP : 3 classements maximum


def select_categories(page, config):
    """
    Sélectionne jusqu'à 3 rubriques + classements via la modal KDP, par nodeId.

    Mécanique VÉRIFIÉE EN LIVE (KDP fr_FR, ebook, 2026-07) :
      - Ouverture        : #categories-modal-button
      - Contexte fixe    : "Livres Kindle" (aucun préfixe Kindle Store/eBooks à saisir)
      - Cascade N niveaux: <select> natifs en cascade ; chaque <option> porte une value
                           JSON dont on extrait le nodeId -> on pose SUCCESSIVEMENT chaque
                           niveau de chemin_node_ids (logique reprise de restaurer_chemin
                           du scraper), en attendant l'apparition du niveau suivant.
      - Classement       : <input type=checkbox class="checkbox-<nodeId>"> (sans id/name,
                           libellé EN ANGLAIS) -> coché par la classe nodeId (fiable).
      - Ligne supplém.   : bouton "Ajouter une autre rubrique"
      - Validation       : bouton "Enregistrer les catégories"
    """
    cats = _normalize_categories(config.get("categories"))
    if not cats:
        log("Aucune rubrique fournie — étape catégories ignorée.")
        return

    log(f"Rubriques (cascade nodeId) à sélectionner : "
        f"{[c['chemin_node_ids'] + ['✓' + c['classement']] for c in cats]}")

    # Ouverture + attente CIBLÉE que la cascade niveau 0 soit réellement prête
    # (remplace l'ancien click + wait_for_timeout(1500) fixe, cause du bug : dans le
    # profil Playwright à cache FROID, l'arbre des rubriques est récupéré sur le réseau
    # à la 1re ouverture et peut dépasser 1500 ms).
    _open_categories_modal(page, cats[0]["chemin_node_ids"][0])

    for idx, cat in enumerate(cats):
        if idx > 0:
            _add_category_row(page)
        # Point d'inspection live à la demande (ne bloque pas les runs n8n) :
        #   PowerShell : $env:KDP_PAUSE_CASCADE=1 ; python book_publisher.py --config ...
        if os.environ.get("KDP_PAUSE_CASCADE"):
            log("KDP_PAUSE_CASCADE actif — pause juste avant la cascade rubrique.")
            page.pause()
        _remplir_rubrique_cascade(page, cat["chemin_node_ids"], cat.get("libelle"))
        _check_classement_by_node_id(page, cat["classement"], cat.get("libelle"))

    _confirm_categories_modal(page)


# JS partagé : sélectionne, parmi les <select> du modal, celui qui propose l'option
# portant `nodeId`, et pose sa value. L'extraction du nodeId depuis la value JSON de
# l'option est IDENTIQUE à celle du scraper (kdp_categories_scraper) -> cohérence garantie
# quelle que soit la clé utilisée par KDP (nodeId / id / stringVal).
_JS_SELECT_NODE = """(nodeId) => {
    const optNodeId = (opt) => {
        const valAttr = opt.getAttribute('value');
        if (!valAttr) return null;
        try {
            const data = JSON.parse(valAttr);
            if (data) {
                if (data.nodeId) return String(data.nodeId);
                if (data.id) return String(data.id);
                if (data.stringVal) {
                    const internal = JSON.parse(data.stringVal);
                    return String(internal.nodeId || internal.key);
                }
            }
        } catch (e) { return valAttr; }
        return valAttr;
    };
    const optOf = (s) => [...s.options].find(o => optNodeId(o) === nodeId);
    const selects = [...document.querySelectorAll('select')];
    // 1) priorité : un select encore sur son placeholder qui propose cette option
    //    (= le bon niveau du bloc en cours de remplissage, jamais un bloc déjà figé)
    let target = selects.find(s => s.selectedIndex === 0 && optOf(s));
    // 2) sinon : un select déjà positionné sur cette valeur (idempotence / retry)
    if (!target) target = selects.find(s => { const o = optOf(s); return o && s.value === o.value; });
    // 3) sinon : n'importe quel select proposant l'option
    if (!target) target = selects.find(s => optOf(s));
    if (!target) return { ok: false, reason: 'aucun select ne propose nodeId=' + nodeId };
    const opt = optOf(target);
    if (target.value !== opt.value) {
        target.value = opt.value;
        target.dispatchEvent(new Event('change', { bubbles: true }));
    }
    return { ok: true, label: (opt.text || '').trim() };
}"""

# JS : un select ENCORE SUR PLACEHOLDER propose-t-il déjà l'option du nodeId donné ?
# (sert à attendre que le niveau suivant de la cascade soit injecté par le XHR KDP)
_JS_NIVEAU_PRET = """(nodeId) => {
    const optNodeId = (opt) => {
        const valAttr = opt.getAttribute('value');
        if (!valAttr) return null;
        try {
            const data = JSON.parse(valAttr);
            if (data) {
                if (data.nodeId) return String(data.nodeId);
                if (data.id) return String(data.id);
                if (data.stringVal) {
                    const internal = JSON.parse(data.stringVal);
                    return String(internal.nodeId || internal.key);
                }
            }
        } catch (e) { return valAttr; }
        return valAttr;
    };
    return [...document.querySelectorAll('select')].some(
        s => s.selectedIndex === 0 && [...s.options].some(o => optNodeId(o) === nodeId));
}"""

# JS de DIAGNOSTIC : photographie l'état de la modal catégories à un instant T, pour
# départager sans humain « la modal ne s'est pas ouverte » vs « les <select> ne sont
# pas encore montés/peuplés » vs « le nodeId L0 n'existe pas dans l'arbre KDP ».
_JS_DIAG_MODAL = """(nodeId) => {
    const optNodeId = (opt) => {
        const valAttr = opt.getAttribute('value');
        if (!valAttr) return null;
        try {
            const data = JSON.parse(valAttr);
            if (data) {
                if (data.nodeId) return String(data.nodeId);
                if (data.id) return String(data.id);
                if (data.stringVal) {
                    const internal = JSON.parse(data.stringVal);
                    return String(internal.nodeId || internal.key);
                }
            }
        } catch (e) { return valAttr; }
        return valAttr;
    };
    const visible = (el) => !!(el && (el.offsetParent !== null ||
        (el.getClientRects && el.getClientRects().length)));
    const selects = [...document.querySelectorAll('select')];
    const placeholders = selects.filter(s => s.selectedIndex === 0);
    const modal = document.querySelector(
        '#categories-modal, [role="dialog"], .a-popover-modal, .a-modal-scroller');
    const btn = document.querySelector('#categories-modal-button');
    const adult = [...document.querySelectorAll('input[name="data[is_adult_content]-radio"]')];
    const lang = document.querySelector('#data-language-native');
    // Détail PAR select : révèle un select cascade monté mais VIDE (optionCount<=1)
    // vs peuplé avec d'autres nodeId. On ignore les selects natifs connus du form.
    const known = ['data-language-native', 'data-contributors', 'reading-interest',
                   'home_marketplace'];
    const allSelects = selects.map(s => {
        const idn = s.id || s.name || '(anon)';
        return {
            id: idn,
            n: s.options.length,
            sel: s.selectedIndex,
            vis: visible(s),
            // échantillon des options (texte court + nodeId) — sauf pour les selects connus
            opts: known.some(k => idn.includes(k)) ? '(select form connu)'
                : [...s.options].slice(0, 5).map(o => ({ t: (o.text || '').trim().slice(0, 22), nid: optNodeId(o) })),
        };
    });
    return {
        btnDisabled: btn ? (btn.disabled || btn.getAttribute('aria-disabled') === 'true') : null,
        adultQuestionAnswered: adult.some(r => r.checked),  // prérequis d'activation du bouton
        langValue: lang ? lang.value : null,               // la langue a-t-elle bien "pris" ?
        modalPresent: !!modal,
        modalVisible: visible(modal),
        modalText: modal ? (modal.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 500) : null,
        selectCount: selects.length,
        visibleSelectCount: selects.filter(visible).length,
        placeholderSelectCount: placeholders.length,
        nodeIdProposed: selects.some(s => [...s.options].some(o => optNodeId(o) === nodeId)),
        allSelects: allSelects,
    };
}"""


def _open_categories_modal(page, first_node_id):
    """
    Ouvre la modal des rubriques et ATTEND que la cascade soit réellement prête :
    qu'un <select> encore sur son placeholder propose déjà le nodeId du niveau 0.

    Remplace l'ancien `click + wait_for_timeout(1500)` fixe (cause du bug « aucun
    select ne propose nodeId=... » au niveau 0). Robuste aux échecs possibles :
      - bouton désactivé (question « contenu adulte » non répondue) -> exception ciblée ;
      - clic d'ouverture silencieusement perdu (bouton hors viewport / recouvert par
        un overlay) -> scroll + fallback clic JS ;
      - arbre des rubriques récupéré sur le réseau APRÈS le clic (cache froid du profil
        Playwright, 1500 ms trop court) -> on attend le nodeId L0 au lieu d'un délai en dur.

    En cas d'échec, journalise un DIAGNOSTIC qui tranche entre les hypothèses.
    """
    # 0) Le bouton reste DÉSACTIVÉ tant que la question « Images/contenu à connotation
    #    sexuelle » (data[is_adult_content]-radio) n'a pas de réponse — vérifié en live :
    #    KDP affiche « Répondez à la question concernant la catégorie réservée aux adultes
    #    avant de sélectionner… ». On attend qu'il soit RÉELLEMENT actionnable, avec un
    #    diagnostic ciblé sinon (au lieu d'un time-out Playwright opaque sur le clic).
    btn = page.wait_for_selector("#categories-modal-button", state="visible", timeout=TIMEOUT)
    try:
        page.wait_for_function(
            "() => { const b = document.querySelector('#categories-modal-button');"
            " return b && !b.disabled && b.getAttribute('aria-disabled') !== 'true'; }",
            timeout=TIMEOUT,
        )
    except TimeoutError:
        raise Exception(
            "Bouton 'Choisissez des rubriques' (#categories-modal-button) resté DÉSACTIVÉ : "
            "la question « contenu à connotation sexuelle » (data[is_adult_content]-radio) "
            "n'a pas été répondue en amont. Vérifier l'étape 'contenu adulte' "
            "(page.check value=false) avant select_categories."
        )

    # Capture réseau CIBLÉE : la modal peuple son select L0 via un POST
    # .../browse-nodes/get-root-nodes/ (vérifié en live 2026-07-10). Quand la cascade
    # reste vide, la vraie cause est presque toujours CE POST (statut != 200 ou corps
    # vide selon la session/marketplace du profil). On journalise son statut + un extrait.
    captured = []      # {url, status} — rempli SYNCHRONEMENT dans le handler
    _resp_objs = []    # (info, response) — corps lus APRÈS, hors du callback

    def _on_response(resp):
        # /!\ API sync Playwright : NE PAS appeler resp.text() ici (aller-retour
        # navigateur depuis un callback d'event -> DEADLOCK). On ne lit que url/status
        # (dispo sans round-trip) et on diffère la lecture du corps hors du handler.
        try:
            if "browse-nodes" in resp.url or "get-root-nodes" in resp.url:
                info = {"url": resp.url.split("/details/")[-1], "status": resp.status}
                captured.append(info)
                _resp_objs.append((info, resp))
        except Exception:
            pass

    page.on("response", _on_response)
    try:
        # 1) Clic d'ouverture fiabilisé
        btn.scroll_into_view_if_needed()
        try:
            btn.click()
        except Exception:
            # Un overlay intercepte le clic natif -> clic JS direct sur le bouton.
            page.evaluate("() => document.querySelector('#categories-modal-button')?.click()")

        # 2) Attente CIBLÉE : cascade niveau 0 prête (select placeholder proposant le nodeId)
        try:
            page.wait_for_function(_JS_NIVEAU_PRET, arg=first_node_id, timeout=TIMEOUT)
        except TimeoutError:
            diag = page.evaluate(_JS_DIAG_MODAL, first_node_id)
            # Lecture des corps MAINTENANT (hors callback -> pas de deadlock sync).
            for info, resp in _resp_objs:
                try:
                    body = resp.text()
                    info["len"] = len(body)
                    info["snippet"] = body[:300]
                except Exception as e:
                    info["bodyErr"] = str(e)
            log(f"DIAGNOSTIC modal catégories (nodeId L0={first_node_id}) : {diag}")
            log(f"DIAGNOSTIC réseau get-root-nodes : {captured or 'AUCUN POST browse-nodes capturé'}")
            raise Exception(
                f"Modal catégories : le niveau 0 (nodeId={first_node_id}) n'est jamais "
                f"devenu disponible après clic sur #categories-modal-button. "
                f"Diagnostic DOM : {diag}. Réseau get-root-nodes : {captured}. "
                "Lecture : POST get-root-nodes absent => clic n'a pas déclenché le fetch ; "
                "status != 200 => fetch refusé (session/marketplace du profil kdp-profile) ; "
                "status 200 mais corps court/vide => l'arbre revient vide pour cette session."
            )
    finally:
        page.remove_listener("response", _on_response)


def _remplir_rubrique_cascade(page, chemin_node_ids, libelle=None):
    """
    Pose SUCCESSIVEMENT chaque niveau de la cascade de rubriques par nodeId, du niveau 0
    jusqu'au dernier select, en attendant l'injection du niveau suivant avant de continuer.
    Reprend la logique validée de restaurer_chemin() du scraper (parcours séquentiel des
    <select> avec attente entre chaque niveau), au lieu de l'unique sélection L0 d'avant.

    Lève une exception CLAIRE dès qu'un nodeId de la cascade est introuvable à l'étape où
    on l'attend, ou si le niveau suivant n'apparaît jamais après une sélection.
    """
    chemin_str = " > ".join(chemin_node_ids) + (f"  ({libelle})" if libelle else "")
    for i, node_id in enumerate(chemin_node_ids):
        res = page.evaluate(_JS_SELECT_NODE, node_id)
        if not res or not res.get("ok"):
            raison = res.get("reason") if res else "aucun retour"
            raise Exception(
                f"Cascade rubrique : niveau {i} (nodeId={node_id}) NON sélectionné "
                f"({raison}). Chemin visé : [{chemin_str}]."
            )
        log(f"  Cascade niveau {i} : {res['label']} (nodeId={node_id}).")

        if i < len(chemin_node_ids) - 1:
            # Attend que le select du niveau suivant (avec SON option) soit injecté.
            prochain = chemin_node_ids[i + 1]
            try:
                page.wait_for_function(_JS_NIVEAU_PRET, arg=prochain, timeout=TIMEOUT)
            except TimeoutError:
                raise Exception(
                    f"Cascade rubrique : le niveau {i + 1} (nodeId={prochain}) n'est jamais "
                    f"apparu après la sélection du niveau {i} (nodeId={node_id}). "
                    f"chemin_node_ids invalide/incomplet ? Chemin visé : [{chemin_str}]."
                )
        else:
            # Dernier select posé : laisse le XHR KDP peupler les cases de classement.
            page.wait_for_timeout(2000)


def _check_classement_by_node_id(page, node_id, libelle=None):
    """Coche la feuille de classement identifiée par sa classe checkbox-<nodeId>."""
    res = page.evaluate(
        """(nodeId) => {
            const cb = document.querySelector('input.checkbox-' + nodeId);
            if (!cb) return { ok: false };
            if (!cb.checked) cb.click();
            return { ok: true, checked: cb.checked,
                     label: (cb.closest('label')?.innerText || '').trim() };
        }""",
        node_id,
    )
    if not res or not res.get("ok"):
        raise Exception(
            f"Classement nodeId={node_id} introuvable dans la modal "
            "(la rubrique parente sélectionnée est-elle la bonne ?)."
        )
    log(f"Classement coché : {res.get('label') or libelle or '?'} (nodeId={node_id}).")
    page.wait_for_timeout(600)


def _add_category_row(page):
    """Ajoute une ligne de rubrique supplémentaire (bouton 'Ajouter une autre rubrique')."""
    page.evaluate(
        """() => {
            const b = [...document.querySelectorAll('button')].find(
                x => x.textContent.trim() === 'Ajouter une autre rubrique'
                     && x.offsetParent !== null);
            if (b) b.click();
        }"""
    )
    page.wait_for_timeout(1200)


def _confirm_categories_modal(page):
    """Valide la modal via le bouton 'Enregistrer les catégories' (vérifié en live)."""
    clicked = page.evaluate(
        """() => {
            const b = [...document.querySelectorAll('button')].find(
                x => x.textContent.trim() === 'Enregistrer les catégories'
                     && x.offsetParent !== null);
            if (b) { b.click(); return true; }
            return false;
        }"""
    )
    if not clicked:
        raise Exception("Bouton 'Enregistrer les catégories' introuvable dans la modal.")
    page.wait_for_timeout(1500)


# ---------------------------------------------------------------------------
# ÉTAPE 2 — UPLOAD DU MANUSCRIT
# ---------------------------------------------------------------------------
def upload_content(page, config):
    """
    Étape 2 (page /content) : déclaration contenu IA, DRM, upload du manuscrit.

    Sélecteurs vérifiés en live (KDP fr_FR, ebook) :
      Manuscrit (input caché) : #data-assets-interior-file-upload-AjaxInput
      Succès upload           : #data-assets-interior-file-upload-success
      Échec upload            : #data-assets-interior-file-upload-failure
      DRM (radios)            : input[name="data[is_drm]-radio"][value="true|false"]
      Contenu IA (3 selects)  : #generative-ai-questionnaire-text / -images / -translations
    """
    log("Étape 2 : Contenu (IA, DRM, manuscrit)...")
    try:
        # L'input fichier est CACHÉ par design (déclenché par un bouton stylé KDP) :
        # attendre 'attached', pas 'visible' (sinon timeout) — set_input_files gère le hidden.
        page.wait_for_selector(
            "#data-assets-interior-file-upload-AjaxInput", state="attached", timeout=TIMEOUT
        )

        # --- Déclaration de contenu généré par IA (OBLIGATOIRE pour continuer) ---
        _fill_ai_questionnaire(page, config)

        # --- DRM (Gestion des droits numériques) ---
        # Valeur pilotée par la config ; défaut = ne pas activer le DRM (False).
        drm_value = "true" if config.get("drm", False) else "false"
        page.check(f'input[name="data[is_drm]-radio"][value="{drm_value}"]')

        # --- Upload du manuscrit ---
        docx_path = os.path.abspath(config["docx_path"])
        if not os.path.exists(docx_path):
            raise FileNotFoundError(f"Fichier manuscrit introuvable : {docx_path}")
        log(f"Manuscrit à uploader : {docx_path}")

        page.set_input_files("#data-assets-interior-file-upload-AjaxInput", docx_path)
        log("Téléchargement du manuscrit en cours...")

        # Attente du résultat : succès OU échec (course entre les deux alertes).
        page.wait_for_selector(
            "#data-assets-interior-file-upload-success", state="visible", timeout=180000
        )
        log("Manuscrit téléchargé avec succès.")

    except FileNotFoundError:
        raise
    except TimeoutError as e:
        # Si l'alerte d'échec est visible, remonter son message.
        fail = page.locator("#data-assets-interior-file-upload-failure")
        if fail.is_visible():
            raise Exception(f"Échec upload manuscrit (KDP) : {fail.inner_text().strip()}")
        raise Exception(f"Timeout étape 2 (upload manuscrit) — confirmation non reçue : {str(e)}")
    except Exception as e:
        raise Exception(f"Erreur étape 2 (upload manuscrit) : {str(e)}")


def _click_ai_toggle(page, oui):
    """
    Coche le toggle parent « Avez-vous utilisé des outils exploitant l'IA... ? » (Oui/Non).

    /!\\ Ce n'est PAS un <input type=radio> : ce sont deux lignes d'accordéon AUI
    (vérifié en live 2026-07-12) :
        <a data-action="a-accordion" class="a-accordion-row a-declarative" href="#">
            <i class="a-icon a-accordion-radio ..."></i><h5>Oui</h5></a>
        <a ... class="a-accordion-row a-declarative"><i ...></i><h5>Non</h5></a>
    scellées dans le .a-accordion qui contient aussi les 3 <select> IA.

    Cliquer « Oui » DÉPLIE le panneau (les 3 selects, jusque-là dans un accordéon
    replié en display:none, deviennent visibles) ET pose les inputs cachés
    data[generative_ai_questionnaire][user_affirmed]=true + [contains_ai_content]=YES.
    « Non » pose [contains_ai_content]=NO. Sans ce clic, les valeurs des selects ne
    sont pas prises en compte par KDP (affirmation obligatoire, cf. input caché
    data[view][require_generative_ai_questionnaire_affirmation]=true).
    """
    label = "Oui" if oui else "Non"
    # Périmètre : le .a-accordion qui contient le 1er <select> IA (évite de matcher
    # un autre accordéon Oui/Non de la page).
    ai_acc = page.locator(
        "div.a-accordion", has=page.locator("#generative-ai-questionnaire-text")
    )
    row = ai_acc.locator(
        "a.a-accordion-row", has=page.locator("h5", has_text=label)
    ).first
    row.wait_for(state="visible", timeout=TIMEOUT)
    row.scroll_into_view_if_needed()
    row.click()

    # Confirme que KDP a enregistré l'affirmation (input caché posé par le clic) —
    # indépendant de la visibilité géométrique du panneau.
    expected = "YES" if oui else "NO"
    try:
        page.wait_for_function(
            """(exp) => {
                const a = document.querySelector('input[name="data[generative_ai_questionnaire][user_affirmed]"]');
                const c = document.querySelector('input[name="data[generative_ai_questionnaire][contains_ai_content]"]');
                return !!(a && a.value === 'true' && c && c.value === exp);
            }""",
            arg=expected,
            timeout=TIMEOUT,
        )
    except TimeoutError:
        raise Exception(
            f"Toggle IA « {label} » : affirmation non enregistrée après le clic "
            "(input caché data[generative_ai_questionnaire][...] non posé)."
        )
    log(f"Toggle IA coché : « {label} » (contains_ai_content={expected}).")


def _fill_ai_questionnaire(page, config):
    """
    Renseigne la déclaration de contenu généré par IA (obligatoire sur /content).

    /!\\ Déclaration LÉGALE envers Amazon : aucune valeur par défaut trompeuse.
    La config DOIT fournir les 3 réponses sous config["contenu_ia"] :
      {
        "texte":       "NONE|PARTIAL_AND_MINIMAL|PARTIAL_AND_EXTENSIVE|ENTIRE_AND_MINIMAL|ENTIRE_AND_EXTENSIVE",
        "images":      "NONE|FEW_AND_MINIMAL|FEW_AND_EXTENSIVE|MANY_AND_MINIMAL|MANY_AND_EXTENSIVE",
        "traductions": "NONE|PARTIAL_AND_MINIMAL|PARTIAL_AND_EXTENSIVE|ENTIRE_AND_MINIMAL|ENTIRE_AND_EXTENSIVE"
      }

    Flux (ordre IMPÉRATIF) :
      1. Toggle parent Oui/Non (_click_ai_toggle). « Non » si les 3 réponses valent
         NONE (aucun contenu IA) -> on s'arrête là, KDP n'exige pas les selects.
         Sinon « Oui », qui DÉPLIE le panneau contenant les 3 selects.
      2. Poser les 3 <select> (uniquement dans le cas « Oui »).
    """
    ia = config.get("contenu_ia")
    if not ia or not all(k in ia for k in ("texte", "images", "traductions")):
        raise Exception(
            "Déclaration contenu IA manquante : renseigner config['contenu_ia'] "
            "avec les clés 'texte', 'images', 'traductions' (déclaration obligatoire KDP)."
        )

    # --- 1) Toggle parent Oui/Non ---
    # « Non » seulement si AUCUN contenu IA n'est déclaré (les 3 valent NONE) ;
    # dans ce cas les 3 selects ne sont pas requis.
    aucun_ia = all(ia.get(k) == "NONE" for k in ("texte", "images", "traductions"))
    if aucun_ia:
        _click_ai_toggle(page, oui=False)
        log("Déclaration IA : aucun contenu IA (3x NONE) -> toggle « Non », selects non requis.")
        return
    _click_ai_toggle(page, oui=True)  # déplie le panneau des 3 selects

    # --- 2) Les 3 <select> IA sont des dropdowns AUI NATIFS CACHÉS (tabindex=-1,
    # a-native-dropdown) : page.select_option échoue ("element is not visible").
    # On pose la valeur en JS + dispatch 'change' (même contournement que la langue
    # à l'étape 1). Validation stricte de l'option (déclaration légale).
    for sel_id, val in (
        ("generative-ai-questionnaire-text", ia["texte"]),
        ("generative-ai-questionnaire-images", ia["images"]),
        ("generative-ai-questionnaire-translations", ia["traductions"]),
    ):
        # Le questionnaire IA (AUI natif caché) peut être monté APRÈS l'input
        # manuscrit -> attendre sa présence (attached, pas visible) avant de poser.
        page.wait_for_selector(f"#{sel_id}", state="attached", timeout=TIMEOUT)
        res = page.evaluate(
            """([id, val]) => {
                const s = document.getElementById(id);
                if (!s) return { ok: false, reason: 'select introuvable' };
                const opt = [...s.options].find(o => o.value === val);
                if (!opt) return { ok: false, reason: 'option absente',
                                   dispo: [...s.options].map(o => o.value) };
                s.value = val;
                s.dispatchEvent(new Event('change', { bubbles: true }));
                return { ok: true };
            }""",
            [sel_id, val],
        )
        if not res or not res.get("ok"):
            raise Exception(
                f"Déclaration IA : impossible de poser #{sel_id}={val} "
                f"({(res or {}).get('reason', 'no ret')}; options dispo="
                f"{(res or {}).get('dispo')})."
            )
    log(f"Déclaration IA : texte={ia['texte']}, images={ia['images']}, traductions={ia['traductions']}")


# ---------------------------------------------------------------------------
# ÉTAPE 2.2 — COUVERTURE (Cover Creator)
# ---------------------------------------------------------------------------
def use_cover_creator(context, page, config):
    """
    Gère la couverture (toujours sur la page /content).

    Deux modes selon la config :
      1. config["cover_path"] fourni  -> UPLOAD d'une couverture perso
         (recommandé : fiable et entièrement automatisable).
         Input caché vérifié : #data-assets-cover-file-upload-AjaxInput
         (formats acceptés : .jpg/.jpeg/.tiff/.tif)
      2. Sinon -> lancement du Créateur de Couverture KDP
         Bouton vérifié : #data-assets-cover-cover-creator-cover-launch-button-announce
         /!\\ Les écrans internes du studio ne sont PAS encore cartographiés
         (template, validation) -> page.pause() à finaliser.
    """
    log("Étape 2.2 : Couverture...")
    try:
        cover_path = config.get("cover_path")

        # --- Mode 1 : upload d'une couverture déjà prête ---
        if cover_path:
            cover_path = os.path.abspath(cover_path)
            if not os.path.exists(cover_path):
                raise FileNotFoundError(f"Couverture introuvable : {cover_path}")
            log(f"Upload de la couverture : {cover_path}")
            page.set_input_files("#data-assets-cover-file-upload-AjaxInput", cover_path)

            # /!\ NE PAS attendre state="visible" sur #...-success : cet élément (et son
            # pendant d'erreur #...-failure) vit dans l'accordéon "Couverture" REPLIÉ
            # (#a-accordion-auto-...  en display:none). Playwright ne le verra JAMAIS
            # "visible" (offset 0x0, offsetParent null) même après un upload RÉUSSI.
            # Vérifié en live 2026-07 : POST .../DIGITAL_BOOK_COVER -> 201 (validation OK),
            # KDP retire alors la classe `a-hidden` du success SANS le rendre géométriquement
            # visible. On s'appuie donc sur `a-hidden` (le vrai signal KDP), pas sur la
            # visibilité. Séquence observée : a-hidden AJOUTÉE au success pendant l'upload,
            # puis RETIRÉE à la fin (ou a-hidden retirée de #...-failure en cas de rejet).
            _SUCC = "#data-assets-cover-file-upload-success"
            _FAIL = "#data-assets-cover-file-upload-failure"

            # Phase 1 (best-effort) : laisser l'upload s'enregistrer (KDP ajoute a-hidden
            # au success). Toléré si manqué (upload/branche déjà traité très vite).
            try:
                page.wait_for_function(
                    "() => { const ok = document.querySelector('%s');"
                    " return ok && ok.classList.contains('a-hidden'); }" % _SUCC,
                    timeout=15000,
                )
            except TimeoutError:
                pass

            # Phase 2 : fin de traitement — success ré-affiché (a-hidden retiré) OU
            # erreur affichée (a-hidden retiré de #...-failure).
            page.wait_for_function(
                "() => {"
                " const ok = document.querySelector('%s');"
                " const ko = document.querySelector('%s');"
                " const okShown = ok && !ok.classList.contains('a-hidden');"
                " const koShown = ko && !ko.classList.contains('a-hidden');"
                " return okShown || koShown; }" % (_SUCC, _FAIL),
                timeout=120000,
            )

            # Rejet éventuel (dimensions/format/poids) : l'alerte d'erreur perd a-hidden.
            ko_shown = page.evaluate(
                "() => { const ko = document.querySelector('%s');"
                " return !!(ko && !ko.classList.contains('a-hidden')); }" % _FAIL
            )
            if ko_shown:
                msg = page.evaluate(
                    "() => { const ko = document.querySelector('%s');"
                    " return (ko.innerText || '').replace(/\\s+/g, ' ').trim(); }" % _FAIL
                )
                raise Exception(f"KDP a rejeté la couverture uploadée : {msg}")

            log("Couverture téléchargée avec succès.")
            return

        # --- Mode 2 : Créateur de Couverture KDP ---
        log("Lancement du Créateur de Couverture KDP...")
        launch = "#data-assets-cover-cover-creator-cover-launch-button-announce"
        launch_id = "data-assets-cover-cover-creator-cover-launch-button-announce"
        page.wait_for_selector(launch, state="visible", timeout=TIMEOUT)
        page.locator(launch).scroll_into_view_if_needed()

        # /!\ Vérifié en live : le studio NAVIGUE LE MÊME ONGLET vers cc.amazon.com/layout
        # (pas de nouvel onglet, pas d'iframe ; token + designId + redirectOverride).
        cc_page = page
        page.click(launch)
        try:
            cc_page.wait_for_url("**cc.amazon.com/**", timeout=60000)
            log("Créateur de Couverture chargé (même onglet, cc.amazon.com).")
        except TimeoutError:
            # Le clic Playwright n'a pas déclenché la navigation. Diagnostic (URL +
            # éventuel modal intermédiaire sur un brouillon frais) puis fallback clic JS
            # (le bouton -announce délègue le handler, confirmé en live).
            diag = page.evaluate(
                """() => {
                    const m = document.querySelector('[role=dialog], .a-popover-modal, .a-modal-scroller');
                    return { url: location.href.slice(0, 90),
                             modal: m ? (m.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 300) : null }; }"""
            )
            log(f"DIAG lancement cover (nav cc.amazon.com absente) : {diag}")
            page.evaluate(f"() => document.getElementById('{launch_id}')?.click()")
            cc_page.wait_for_url("**cc.amazon.com/**", timeout=60000)
            log("Créateur de Couverture chargé (après fallback clic JS).")

        _drive_cover_creator(cc_page, config)

        # Après soumission, le studio redirige vers redirectOverride = /content.
        page.wait_for_url("**/title-setup/kindle/**/content", timeout=120000)
        page.wait_for_selector(
            "#data-assets-cover-file-upload-success", state="visible", timeout=120000
        )
        log("Couverture créée et appliquée avec succès.")

    except FileNotFoundError:
        raise
    except TimeoutError as e:
        raise Exception(f"Timeout étape 2.2 (couverture) — sélecteur introuvable : {str(e)}")
    except Exception as e:
        raise Exception(f"Erreur étape 2.2 (couverture) : {str(e)}")


# JS de DIAGNOSTIC du studio Cover Creator (cc.amazon.com). L'extension Chrome
# est bloquée sur ce domaine, mais Playwright y a accès : on cartographie le DOM
# (template, zones texte titre/auteur, mécanisme d'application de l'image uploadée)
# via un dump texte, puisque la sélection y est graphique (canvas, ids base64).
_JS_DIAG_CC = """([titre, auteur]) => {
    const vis = el => !!(el && (el.offsetParent !== null ||
        (el.getClientRects && el.getClientRects().length)));
    const desc = (el) => {
        const id = el.id ? '#' + el.id : '';
        const cls = (typeof el.className === 'string' && el.className.trim())
            ? '.' + el.className.trim().split(/\\s+/).slice(0, 3).join('.') : '';
        const data = [...el.attributes].filter(a => a.name.startsWith('data-'))
            .slice(0, 3).map(a => `[${a.name}=${(a.value || '').slice(0, 18)}]`).join('');
        return (el.tagName.toLowerCase() + id + cls + data).slice(0, 130);
    };
    const clickable = [...document.querySelectorAll(
        'button,a,[role=button],.a-button-input,input[type=button],input[type=submit]')]
        .filter(vis).map(el => ({ d: desc(el), t: (el.textContent || el.value || '').trim().slice(0, 45) }))
        .filter(x => x.t || x.d);
    const inputs = [...document.querySelectorAll(
        'input,textarea,select,[contenteditable=true],[contenteditable=""]')]
        .filter(vis).map(el => ({ d: desc(el), type: el.type || el.tagName.toLowerCase(),
            ph: el.placeholder || '', val: (el.value || el.textContent || '').trim().slice(0, 45) }));
    const canvases = [...document.querySelectorAll('canvas')]
        .map(c => ({ d: desc(c), w: c.width, h: c.height, vis: vis(c) }));
    const imgs = [...document.querySelectorAll('img')].filter(vis)
        .map(im => ({ d: desc(im), src: (im.src || '').slice(-45), w: im.naturalWidth, h: im.naturalHeight }));
    const leaf = (needle) => [...document.querySelectorAll('*')]
        .filter(el => vis(el) && el.children.length === 0 && (el.textContent || '').includes(needle))
        .map(desc).slice(0, 8);
    return {
        url: location.href.slice(0, 55), title: document.title,
        known: {
            fileupload: !!document.querySelector('#fileupload'),
            gallery: !!document.querySelector('.ccFromImageGalleryButton'),
            placeholder: !!document.querySelector('.ccPlaceholderImageButton'),
            submit: !!document.querySelector('#ccSubmitCoverButton'),
        },
        clickable: clickable.slice(0, 32),
        inputs: inputs.slice(0, 25),
        canvases: canvases,
        imgCount: imgs.length, imgs: imgs.slice(0, 12),
        titleTextEls: leaf(titre.slice(0, 15)),
        authorTextEls: leaf(auteur),
        bodyText: (document.body.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 500),
    };
}"""


def _diag_cover_studio(cc_page, config, label):
    """Journalise un instantané du DOM du studio Cover Creator (voir _JS_DIAG_CC).
    Sert à cartographier template / zones texte / application image sans Inspector."""
    titre = config.get("titre_livre", "")
    auteur = config.get("auteur_nom", "")
    try:
        res = cc_page.evaluate(_JS_DIAG_CC, [titre, auteur])
        log(f"DIAG CC STUDIO [{label}] : {json.dumps(res, ensure_ascii=False)}")
    except Exception as e:
        log(f"DIAG CC STUDIO [{label}] ÉCHEC du dump : {e}")


def _drive_cover_creator(cc_page, config):
    """
    Pilote l'assistant du Créateur de Couverture (cc.amazon.com).

    /!\\ ENVIRONNEMENT FRAGILE (constaté en live) : SPA canvas, ids en base64
    qui changent à chaque session, pas de classes stables. On s'appuie donc
    UNIQUEMENT sur les libellés texte (get_by_text / get_by_role), et on laisse
    des page.pause() là où la sélection est purement graphique (templates).

    Assistant en 3 étapes :
      1. Sélectionner une création   (avec image / sans image, puis template)
      2. Mettre en forme et modifier (optionnel — on garde les défauts)
      3. Aperçu                      (puis soumission -> redirect /content)

    RECOMMANDATION : pour un pipeline n8n robuste, préférer config["couverture_path"]
    (upload d'une image) plutôt que ce studio. Voir use_cover_creator mode 1.
    """
    log("Pilotage du Créateur de Couverture...")

    # --- Entrée du studio (ROBUSTE aux chargements variables de la SPA) ---
    # Selon la latence, soit le splash d'intro "Comment utiliser..." apparaît
    # (#splashContinueButton — 0-size pour Playwright, clic JS requis, handler délégué),
    # soit le studio va DIRECTEMENT au dialogue image (#fileupload). On attend donc que
    # l'UN OU l'AUTRE soit présent (budget large : SPA canvas lourde), puis on ferme le
    # splash s'il est là. (L'ancienne attente rigide sur le seul splash était flaky.)
    try:
        cc_page.wait_for_function(
            "() => !!(document.querySelector('#splashContinueButton') "
            "|| document.querySelector('#fileupload'))",
            timeout=TIMEOUT * 2,
        )
    except TimeoutError:
        # Filet : ni splash ni dialogue image -> écran inattendu (ex. reprise d'un
        # design en cours sur un brouillon déjà utilisé). On dumpe pour diagnostic.
        _diag_cover_studio(cc_page, config, "entrée studio (ni splash ni fileupload)")
        raise
    if cc_page.locator("#splashContinueButton").count() > 0:
        cc_page.evaluate(
            "() => { const b = document.querySelector('#splashContinueButton'); if (b) b.click(); }"
        )
        cc_page.wait_for_timeout(2500)
        log("Splash d'intro fermé (#splashContinueButton).")
    else:
        log("Pas de splash d'intro (studio direct sur le dialogue image).")

    cc_page.wait_for_timeout(1000)

    # --- Étape 1a : source de l'image (dialogue "Obtenir les images") ---
    # Sélecteurs STABLES vérifiés en live :
    #   Galerie  : .ccFromImageGalleryButton
    #   Ignorer  : .ccPlaceholderImageButton
    #   Upload   : input#fileupload (accept image/*) — pousser le fichier directement
    source = config.get("couverture_image", "ignorer")

    if source == "ignorer":
        cc_page.locator(".ccPlaceholderImageButton").first.click(timeout=TIMEOUT)

    elif source == "ordinateur":
        img_path = config.get("couverture_image_path")
        if not img_path or not os.path.exists(os.path.abspath(img_path)):
            raise Exception("couverture_image='ordinateur' mais couverture_image_path introuvable.")
        # #fileupload est un input file CACHÉ (dispo une fois le splash fermé) :
        # attendre 'attached' puis pousser le fichier ; laisser le studio traiter/rendre.
        cc_page.wait_for_selector("#fileupload", state="attached", timeout=TIMEOUT)
        cc_page.set_input_files("#fileupload", os.path.abspath(img_path))
        cc_page.wait_for_timeout(4000)

    elif source == "galerie":
        cc_page.locator(".ccFromImageGalleryButton").first.click(timeout=TIMEOUT)
        cc_page.wait_for_timeout(1500)
        # Rubrique de la galerie (texte stable), ex. "Santé et Beauté"
        rubrique = config.get("couverture_galerie_rubrique")
        if rubrique:
            cc_page.get_by_text(rubrique, exact=True).first.click(timeout=TIMEOUT)
            cc_page.wait_for_timeout(2500)
        # Image de la galerie : sélection positionnelle (img.userImage)
        index = int(config.get("couverture_galerie_index", 0))
        cc_page.locator("img.userImage").nth(index).click(timeout=TIMEOUT)
        cc_page.wait_for_timeout(1500)
    else:
        raise Exception(f"couverture_image inconnu : {source!r} (galerie|ordinateur|ignorer)")

    # =====================================================================
    # APPLICATION IMAGE : pour la source "ordinateur", l'image uploadée est
    # AUTO-APPLIQUÉE à tous les aperçus de templates (vérifié 2026-07-10 :
    # img.userImage passe aux dimensions du fichier après l'upload). Pas de
    # drag/double-clic nécessaire. (L'ancien "blocage" ne visait que la GALERIE.)
    # On valide ensuite la création (étape 1 -> 2) via #ccSelectDesignButton.
    # =====================================================================
    cc_page.wait_for_timeout(1500)

    # (Optionnel) sélection d'un template de mise en page par index (vignettes
    # img[src*='_med_']). Par défaut : template pré-sélectionné du studio.
    tpl_index = config.get("couverture_template_index")
    if tpl_index is not None:
        cc_page.locator("img[src*='_med_']").nth(int(tpl_index)).click(timeout=TIMEOUT)
        cc_page.wait_for_timeout(1000)
        log(f"Template sélectionné (index {tpl_index}).")

    # Valider la création : bouton AUI "Choisir cette création" -> clic JS
    # (Playwright click ne déclenche pas le handler, comme launch/splash).
    cc_page.wait_for_selector("#ccSelectDesignButton", state="attached", timeout=TIMEOUT)
    cc_page.evaluate(
        "() => { const b = document.querySelector('#ccSelectDesignButton'); if (b) b.click(); }"
    )
    cc_page.wait_for_timeout(3000)
    log("Création validée (#ccSelectDesignButton) -> étape 2 (mise en forme).")

    # CARTOGRAPHIE étape 2 (édition texte titre/auteur). Arrêt propre après le dump.
    # Étape 2 : le TITRE et l'AUTEUR sont AUTO-PLACÉS par KDP depuis les métadonnées
    # (title/author passés dans l'URL de lancement) -> texte overlay déjà correct et
    # éditable (div#ccTitle). Aucune saisie nécessaire (vérifié 2026-07-10).

    # --- Étape 3 : Aperçu ---  bouton AUI -> clic JS (Playwright click inopérant).
    clicked = cc_page.evaluate(
        """() => {
            const el = [...document.querySelectorAll('a, button')]
                .find(x => x.textContent.trim() === 'Aperçu' && x.offsetParent !== null);
            if (el) { el.click(); return true; }
            return false;
        }"""
    )
    if not clicked:
        raise Exception("Bouton 'Aperçu' introuvable dans le studio (étape 2->3).")
    cc_page.wait_for_timeout(2500)
    _diag_cover_studio(cc_page, config, "étape 3 (aperçu, avant soumission)")

    # Garde-fou : la soumission écrit la couverture sur le brouillon. On ne la
    # déclenche que si explicitement autorisé (le temps de valider le flux complet).
    if not os.environ.get("KDP_SUBMIT_COVER"):
        raise Exception(
            "DIAGNOSTIC COVER STUDIO (étape 3 aperçu) — flux mappé jusqu'à la soumission. "
            "Soumission NON déclenchée (garde-fou). Relancer avec KDP_SUBMIT_COVER=1 pour "
            "exécuter la soumission réelle (#ccSubmitCoverButton) et vérifier le retour /content."
        )

    # Laisser l'aperçu finir de se rendre (spinner #loadingIcon) avant de soumettre.
    try:
        cc_page.wait_for_selector("#loadingIcon", state="hidden", timeout=TIMEOUT)
    except TimeoutError:
        pass
    cc_page.wait_for_timeout(1000)

    # Soumission finale "Enregistrer et envoyer" -> redirige vers /content.
    submitted = cc_page.evaluate(
        "() => { const b = document.querySelector('#ccSubmitCoverButton'); if (b) { b.click(); return true; } return false; }"
    )
    if not submitted:
        raise Exception("Bouton de soumission #ccSubmitCoverButton introuvable (étape 3).")
    log("Couverture soumise (#ccSubmitCoverButton) — redirection /content attendue.")


# ---------------------------------------------------------------------------
# FIN ÉTAPE 2 — ENREGISTRER ET CONTINUER (/content -> /pricing)
# ---------------------------------------------------------------------------
def save_content_and_continue(page):
    """
    Fin de l'étape 2 (page /content) : clique « Enregistrer et continuer » pour
    passer à l'étape 3 (/pricing), APRÈS upload_content() + use_cover_creator().

    Sélecteur vérifié en live (KDP fr_FR, ebook) :
      Bouton continuer : #save-and-continue  (identique à l'étape 1 /details)

    /!\\ Le questionnaire IA replie/déplie un accordéon AUI sur cette page
    (cf. _click_ai_toggle). Par sécurité on attend le bouton 'attached' avant de
    cliquer — même logique que l'input manuscrit caché — plutôt que de supposer
    qu'il est toujours actionnable après manipulation de l'accordéon.

    KDP peut afficher un bandeau orange NON BLOQUANT « Table des matières
    manquante » sur cette page — confirmé non bloquant par la doc Amazon : il
    n'empêche pas le passage à /pricing et on l'ignore volontairement. La
    transition est confirmée en attendant l'URL /pricing (pas en se fiant à
    l'absence du bandeau) ; aucune modale de confirmation n'est attendue.
    """
    log("Étape 2 : « Enregistrer et continuer » -> /pricing...")
    try:
        page.wait_for_selector("#save-and-continue", state="attached", timeout=TIMEOUT)
        page.click("#save-and-continue")

        # Confirmation robuste du passage à l'étape 3 : l'URL doit contenir /pricing.
        page.wait_for_url("**/pricing**", timeout=TIMEOUT)
        log("Passage à l'étape 3 (/pricing) confirmé.")

    except TimeoutError as e:
        raise Exception(
            "Timeout étape 2 (« Enregistrer et continuer ») — /pricing non atteint. "
            "Vérifier un blocage BLOQUANT (erreur de validation KDP) distinct du "
            f"bandeau « Table des matières manquante » qui, lui, est non bloquant : {str(e)}"
        )
    except Exception as e:
        raise Exception(f"Erreur étape 2 (« Enregistrer et continuer ») : {str(e)}")


# ---------------------------------------------------------------------------
# ÉTAPE 3 — PRIX
# ---------------------------------------------------------------------------
def set_pricing(page, config):
    """
    Étape 3 (page /pricing) : KDP Select, marketplace principal = FR,
    taux de redevance, prix EUR. Les autres marketplaces sont auto-calculés.

    Sélecteurs vérifiés en live (KDP fr_FR, ebook) :
      KDP Select (checkbox)   : #data-is-select  (name data[is_select]-check)
      Marketplace principal   : select[name="data[digital][home_marketplace]"] (val "FR")
      Taux de redevance       : input[name="data[digital][royalty_rate]-radio"][value="70_PERCENT|35_PERCENT"]
      Prix FR (TTC, éditable) : input[name="data[digital][channels][amazon][FR][price_vat_inclusive]"]
        /!\\ Le champ devient éditable UNIQUEMENT après avoir mis le marketplace
            principal sur FR (sinon FR est en lecture seule = price_readonly).
    """
    log("Étape 3 : Configuration des prix...")
    try:
        prix = str(config["prix"])
        log(f"Prix cible (EUR TTC / amazon.fr) : {prix}")

        page.wait_for_selector('select[name="data[digital][home_marketplace]"]', timeout=TIMEOUT)

        # --- KDP Select (exclusivité) : piloté par config, défaut = non inscrit ---
        select_check = page.locator("#data-is-select")
        if config.get("kdp_select", False):
            if not select_check.is_checked():
                select_check.check()
        else:
            if select_check.is_checked():
                select_check.uncheck()

        # --- Marketplace principal ---
        # /!\\ Vérifié en live : sur /pricing ce <select> est DÉSACTIVÉ (disabled).
        # Le site de vente principal se choisit à l'étape /details (fill_book_details
        # le met déjà sur FR) et n'est ici qu'un rappel en lecture seule — KDP l'écrit
        # explicitement (« Pour modifier votre site de vente principal, revenez à
        # l'onglet Détails »). On NE tente donc PAS de le changer (select_option
        # échouerait sur un élément disabled). Comme il vaut déjà FR, le prix FR est
        # d'emblée éditable ; on confirme juste la valeur, sinon FR resterait en
        # lecture seule (price_readonly) et le fill échouerait plus bas.
        home_value = page.input_value('select[name="data[digital][home_marketplace]"]')
        if home_value != "FR":
            raise Exception(
                f"Site de vente principal = {home_value!r} (attendu 'FR') sur /pricing, "
                "et le champ y est en lecture seule. À corriger à l'étape /details."
            )

        # --- Taux de redevance (défaut 70 %) ---
        # /!\\ Vérifié en live : radio AUI « fancy ». Le <input> réel est recouvert
        # par <i class="a-icon a-icon-radio"> qui intercepte le clic -> page.check()
        # échoue (pointer events interceptés). On clique le <label> parent (qui, par
        # comportement natif, coche l'input), comme _click_ai_toggle clique la ligne
        # d'accordéon, puis on confirme l'état réel de l'input (indépendant du rendu).
        royalty = "35_PERCENT" if str(config.get("royalty", "70")) == "35" else "70_PERCENT"
        page.locator(
            f'div.a-radio:has(input[name="data[digital][royalty_rate]-radio"]'
            f'[value="{royalty}"]) label'
        ).click()
        page.wait_for_function(
            """(val) => {
                const i = document.querySelector(
                    'input[name="data[digital][royalty_rate]-radio"]:checked');
                return !!(i && i.value === val);
            }""",
            arg=royalty,
            timeout=TIMEOUT,
        )

        # --- Prix FR (EUR TTC) ; KDP recalcule les autres marketplaces ---
        # /!\\ Vérifié en live : sur amazon.fr, KDP valide le prix en locale FR.
        # Un point décimal ("7.99") est REJETÉ (« Utilisez un format tarifaire
        # correspondant à 0,00 ») et, tant que le format est invalide, la redevance
        # nette n'est PAS calculée (colonnes Taux/Redevance = « – »). On saisit donc
        # avec une VIRGULE. (Prix < 1000 EUR : pas de séparateur de milliers à gérer.)
        prix_fr = prix.replace(".", ",")
        page.fill(
            'input[name="data[digital][channels][amazon][FR][price_vat_inclusive]"]', prix_fr
        )
        page.wait_for_timeout(2000)

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
        # --- Garde-fou pré-publication ---
        # Si le bouton Publier est désactivé (compte KDP incomplet : infos
        # fiscales/bancaires manquantes, ou pré-requis livre non satisfaits),
        # on s'arrête proprement au lieu de cliquer dans le vide.
        page.wait_for_selector("#save-and-publish-announce", timeout=TIMEOUT)
        if _publish_is_blocked(page):
            raise Exception(
                "Publication impossible — bouton Publier désactivé "
                "(compte KDP incomplet : infos fiscales/bancaires, ou pré-requis "
                "du livre non satisfaits comme la couverture)."
            )

        # Bouton "Publier votre ebook Kindle" (vérifié en live)
        page.click("#save-and-publish-announce")
        # Après clic, KDP redirige vers la Bibliothèque (Bookshelf).
        page.wait_for_load_state("networkidle", timeout=60000)

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


def _publish_is_blocked(page):
    """
    True si le bouton 'Publier' est désactivé (compte/livre incomplet).

    Robuste aux variantes Amazon : classe contenant 'disabled' (span ou bouton),
    attribut aria-disabled='true', ou propriété .disabled du <button>.
    """
    return bool(page.evaluate(
        """() => {
            const span = document.querySelector('#save-and-publish');
            const btn  = document.querySelector('#save-and-publish-announce');
            const cls  = ((span && span.className) || '') + ' ' + ((btn && btn.className) || '');
            const ariaSpan = span && span.getAttribute('aria-disabled');
            const ariaBtn  = btn && btn.getAttribute('aria-disabled');
            const prop = !!(btn && btn.disabled === true);
            return /disabled/i.test(cls) || ariaSpan === 'true' || ariaBtn === 'true' || prop;
        }"""
    ))


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

    /!\\ DOM Bookshelf non encore cartographié : implémentation best-effort,
    à affiner si besoin (sélecteur exact des lignes / emplacement de l'ASIN).
    """
    try:
        row = page.locator("[data-asin]", has_text=titre_livre).first
        row.wait_for(timeout=10000)
        asin = row.get_attribute("data-asin")
        if asin and re.fullmatch(r"[A-Z0-9]{10}", asin):
            return asin
        return _extract_asin_from_text(row.inner_text())
    except Exception:
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

    page = None
    try:

        config = read_config(args.config)

        with sync_playwright() as p:
            log("Lancement de Chromium avec le profil persistant...")
            context = p.chromium.launch_persistent_context(
                user_data_dir=PROFILE_PATH,
                headless=HEADLESS,
                args=["--start-maximized"]
            )

            # Utilise l'onglet existant au lieu d'en créer un nouveau
            if context.pages:
                page = context.pages[0]
            else:
                page = context.new_page()

            log("Navigation vers KDP Setup...")
            page.goto(KDP_NEW_EBOOK_URL)

            # Vérifie la session (et point d'entrée du login manuel à coder)
            ensure_logged_in(page, config)

            # Déroulement du workflow
            fill_book_details(page, config)       # /details -> clique Continuer
            upload_content(page, config)          # /content : IA, DRM, manuscrit
            use_cover_creator(context, page, config)  # /content : couverture
            save_content_and_continue(page)       # /content -> /pricing
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
