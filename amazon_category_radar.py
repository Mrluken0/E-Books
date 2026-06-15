import sys
import io
import os
import copy
import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
import re
import random
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

os.environ["PYTHONIOENCODING"] = "utf-8"



"""
================================================================================
STRUCTURES HTML BSR GEREES PAR extract_bsr() :
1. Bullet Points (#detailBullets_feature_div / #detailBulletsWrapper_feature_div) :
   - <li> contenant "Classement des meilleures ventes d'Amazon : 1 542 en Livres"
   - Sous-categories imbriquees dans <ul class="zg_hrsr"> (retirees par clonage pour
     isoler le rang principal, puis re-extraites comme BSR secondaires).
2. Tableaux techniques (#productDetails_detailBullets_sections1,
   #productDetails_techSpec_section_1, #productDetails_db_sections, .prodDetTable) :
   - <tr> avec <th> "Classement des meilleures ventes" et <td> rang + categorie.
3. Variations linguistiques / typographiques tolerees :
   - "n1 542 en ...", "#8 204 en ...", "1.542 dans ...", "#2,340 in Books" (anglais).
   - Separateurs de milliers : espace, espace fine insecable, point, virgule.
   - Nettoyage des caracteres invisibles Amazon (U+200E, U+200F, U+00A0, U+202A-202E).
   - Dernier recours : rang nu sans categorie lisible (ex. <td>15673</td>).

SORTIE : bsr (int), bsr_category (str), bsr_sub (dict {categorie: rang}, JSON).
"""

BASE_URL = "https://www.amazon.fr"
START_URL = "https://www.amazon.fr/gp/bestsellers/books"

OUTPUT_FILE = r"C:/LKN_Digital/KDP/radar_kdp_clean.xlsx"
TEMP_FILE   = r"C:/LKN_Digital/KDP/radar_kdp_temp.xlsx"

# ── 1. Rotation User-Agent ────────────────────────────────────────────────────
UA_LIST = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
]

def get_headers(referer=None):
    headers = {
        "User-Agent": random.choice(UA_LIST),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin" if referer else "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
        "Device-Memory": "8",
    }
    if referer:
        headers["Referer"] = referer
    return headers

EXCLUDED = [
    "roman", "fiction", "thriller", "fantasy", "fantastique", "manga", "bd",
    "bandes dessinées", "religion", "spiritualité", "spiritualités",
    "ésotérisme", "paranormal", "art", "histoire", "humour", "enfant",
    "enfants", "adolescent", "adolescents", "amazon renewed",
    "appareils amazon", "animalerie", "comics", "comic", "beaux livres",
    "calendriers", "agendas", "érotisme", "livres anglais"
]

BUSINESS_KEYWORDS = [
    "entreprise", "bourse", "economie", "économie", "argent", "finance",
    "finances", "investissement", "santé", "bien-être", "forme",
    "diététique", "nutrition", "études", "scolaire", "parascolaire",
    "productivité", "organisation", "sport", "famille", "psychologie",
    "développement", "droit", "tourisme", "voyages", "cuisine"
]

PRACTICAL_KEYWORDS = [
    "guide", "méthode", "comment", "débutant", "débutants", "pratique",
    "plan", "gérer", "budget", "argent", "investir", "épargne",
    "productivité", "organisation", "réussir", "apprendre", "simple",
    "pas à pas", "habitudes"
]

# Nombre de livres (par sous-catégorie) pour lesquels on visite la page produit
# afin d'en extraire BSR / pages / description / date. Aligné sur max_books (20)
# pour qu'AUCUNE ligne de l'Excel ne reste sans données de détail.
PRODUCT_PAGE_LIMIT = 20

BLOCK_SIGNALS = [
    "robot check", "captcha", "enter the characters you see below",
    "sorry, we just need to make sure you're not a robot",
    "veuillez saisir les caractères", "api-services-support@amazon.com"
]

_print_lock = threading.Lock()
_save_lock  = threading.Lock()

def safe_print(*args, **kwargs):
    with _print_lock:
        print(*args, **kwargs, flush=True)

def clean_text(text):
    if not text:
        return ""
    text = re.sub(r"[\u200e\u200f\u202a-\u202e\xa0]", " ", text)
    return re.sub(r"\s+", " ", text).strip()

# ── Sessions HTTP persistantes (cookies) — une par thread ─────────────────────
# Amazon dépose des cookies de session (session-id, ubid...) qui réduisent
# fortement les "soft-blocks" (pages 200 mais vidées de leur contenu). On réutilise
# une Session requests par thread (warm-up unique sur la home), conservée tant
# qu'aucun captcha n'est levé — c'est cette persistance des cookies qui fait passer
# le taux de pages complètes (donc le taux BSR) près de 100%.
_thread_local = threading.local()

def _new_session():
    sess = requests.Session()
    try:
        # Warm-up : on récupère les cookies anonymes depuis la home Amazon
        sess.get(BASE_URL, headers=get_headers(), timeout=20)
        time.sleep(random.uniform(0.5, 1.2))
    except Exception:
        pass
    return sess

def _get_session():
    sess = getattr(_thread_local, "session", None)
    if sess is None:
        sess = _new_session()
        _thread_local.session = sess
    return sess

def _reset_session():
    """Force la création d'une nouvelle session (nouveaux cookies) au prochain appel."""
    old = getattr(_thread_local, "session", None)
    if old is not None:
        try:
            old.close()
        except Exception:
            pass
    _thread_local.session = None

def get_soup(url, max_retries=3, require=None, referer=None):
    """
    Récupère et parse une page Amazon.

    require : callback optionnel(soup)->bool. S'il renvoie False, la page est
    considérée comme un soft-block (200 mais contenu manquant) et on réessaie en
    CONSERVANT la session (les cookies sont précisément ce qui résout les blocages :
    on rotationne seulement l'User-Agent via get_headers). Au dernier essai, on
    renvoie quand même la page obtenue pour exploiter les champs disponibles.

    NB : on ne réinitialise la session (perte des cookies + nouveau warm-up coûteux)
    QUE sur un vrai captcha — là, les cookies sont probablement « flaggés ».
    """
    last_soup = None
    for attempt in range(max_retries):
        try:
            sess = _get_session()
            response = sess.get(url, headers=get_headers(referer=referer), timeout=25)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            last_soup = soup

            page_text = soup.get_text().lower()
            if any(signal in page_text for signal in BLOCK_SIGNALS):
                # Vrai captcha : cookies probablement flaggés -> on régénère la session
                wait = (attempt + 1) * random.uniform(8, 15)
                safe_print(f"    [BLOCAGE] Amazon bloque. Attente {wait:.0f}s (tentative {attempt+1}/{max_retries})")
                _reset_session()
                time.sleep(wait)
                continue

            # Soft-block : page renvoyée mais contenu attendu absent.
            # On garde la session (cookies OK) et on réessaie après une courte pause.
            if require is not None and not require(soup):
                if attempt < max_retries - 1:
                    wait = random.uniform(2, 4)
                    safe_print(f"    [SOFT-BLOCK] Contenu manquant. Attente {wait:.0f}s (tentative {attempt+1}/{max_retries})")
                    time.sleep(wait)
                    continue
                return soup  # dernier essai : on rend ce qu'on a

            return soup

        except requests.exceptions.HTTPError as e:
            code = e.response.status_code
            if code in (429, 503):
                wait = (attempt + 1) * random.uniform(5, 10)
                safe_print(f"    [HTTP {code}] Rate limited. Attente {wait:.0f}s (tentative {attempt+1}/{max_retries})")
                time.sleep(wait)
            else:
                raise
        except requests.exceptions.RequestException as e:
            if attempt < max_retries - 1:
                wait = (attempt + 1) * random.uniform(3, 6)
                safe_print(f"    [RESEAU] {e}. Attente {wait:.0f}s (tentative {attempt+1}/{max_retries})")
                time.sleep(wait)
            else:
                raise

    if last_soup is not None:
        return last_soup
    raise Exception(f"Echec apres {max_retries} tentatives : {url}")

def _has_product_details(soup):
    """Valide qu'une page produit contient bien sa section détails (anti soft-block)."""
    return bool(
        soup.select_one("#detailBullets_feature_div")
        or soup.select_one("#detailBulletsWrapper_feature_div")
        or soup.select_one("#productDetails_db_sections")
        or soup.select_one("#productDetails_detailBullets_sections1")
        or soup.select_one("#productDetails_techSpec_section_1")
        or soup.select_one(".prodDetTable")
    )

def _has_listing_items(soup):
    """Valide qu'une page liste bestsellers contient bien sa grille de produits."""
    return bool(
        soup.select_one("div.zg-grid-general-faceout")
        or soup.select_one("div[id^='gridItemRoot']")
    )

def save_intermediate(all_books):
    if not all_books:
        return
    with _save_lock:
        try:
            df = pd.DataFrame(all_books)
            df = df[df["title"].notna() & (df["title"].str.strip() != "")]
            if df.empty:
                return
            summary = compute_scores(df)
            with pd.ExcelWriter(TEMP_FILE, engine="openpyxl") as writer:
                summary.to_excel(writer, sheet_name="scores", index=False)
                df.to_excel(writer, sheet_name="data", index=False)
            safe_print(f"    [SAVE] Sauvegarde temp OK ({len(df)} livres, {len(summary)} categories)")
        except Exception as e:
            safe_print(f"    [SAVE] Erreur : {e}")

def is_excluded(name):
    return any(word in name.lower() for word in EXCLUDED)

def is_business_category(name):
    return any(word in name.lower() for word in BUSINESS_KEYWORDS)

def valid_category(name, href):
    if not name or not href:
        return False
    nl = name.lower().strip()
    if len(nl) < 3 or nl.isdigit():
        return False
    if is_excluded(nl):
        return False
    if "/gp/bestsellers/books/" not in href or "pg=" in href:
        return False
    return True

def normalize_url(href):
    return BASE_URL + href if href.startswith("/") else href

def get_main_categories():
    soup = get_soup(START_URL, require=_has_listing_items)
    categories = []
    for a in soup.select("a[href*='/gp/bestsellers/books/']"):
        name = clean_text(a.get_text())
        href = a.get("href")
        if not href:
            continue
        href = normalize_url(href)
        if valid_category(name, href):
            categories.append((name, href))
    return list(dict(categories).items())

def get_subcategories(url):
    soup = get_soup(url, require=_has_listing_items)
    subcategories = []
    for a in soup.select("a[href*='/gp/bestsellers/books/']"):
        name = clean_text(a.get_text())
        href = a.get("href")
        if not href:
            continue
        href = normalize_url(href)
        if valid_category(name, href):
            subcategories.append((name, href))
    return list(dict(subcategories).items())

def extract_price(text):
    if not text:
        return None
    match = re.search(r"(\d+(?:[,.]\d+)?)", text.replace(",", "."))
    return float(match.group(1)) if match else None

def get_price(item):
    for selector in [".a-price .a-offscreen", ".p13n-sc-price", "span.a-color-price", "span.a-size-base.a-color-price"]:
        el = item.select_one(selector)
        if el:
            price = extract_price(el.get_text())
            if price is not None:
                return price
    return None

def get_title(item):
    img = item.select_one("img")
    if img and img.get("alt"):
        return clean_text(img.get("alt"))
    for selector in [
        "div._cDEzb_p13n-sc-css-line-clamp-1_1Fn1y",
        "div._cDEzb_p13n-sc-css-line-clamp-2_EWgCb",
        "span.a-size-medium",
        "span.a-size-base-plus"
    ]:
        el = item.select_one(selector)
        if el:
            title = clean_text(el.get_text())
            if title:
                return title
    return ""

def get_rating(item):
    for el in item.select("span.a-icon-alt"):
        match = re.search(r"(\d+[,.]?\d*)\s*sur\s*5", el.get_text())
        if match:
            return float(match.group(1).replace(",", "."))
    for el in item.select("[aria-label]"):
        match = re.search(r"(\d+[,.]?\d*)\s*sur\s*5", el.get("aria-label", ""))
        if match:
            return float(match.group(1).replace(",", "."))
    return None

def get_reviews_count(item):
    def parse_count(text):
        text = re.sub(r"[\xa0\s,\.]", "", str(text))
        match = re.search(r"(\d+)", text)
        return int(match.group(1)) if match else None

    for el in item.select("[aria-label]"):
        aria = el.get("aria-label", "")
        if "évaluation" in aria.lower() or "avis" in aria.lower():
            val = parse_count(aria)
            if val and val > 0:
                return val
    for el in item.select("a[href*='customerReviews'], a[href*='#customerReviews']"):
        val = parse_count(el.get_text())
        if val and val > 0:
            return val
    return None

def get_product_url(item):
    link = item.select_one("a[href*='/dp/']")
    if link:
        return normalize_url(link.get("href", "").split("?")[0])
    return None

# ── Helpers BSR ───────────────────────────────────────────────────────────────
# Caractères invisibles / espaces spéciaux qu'Amazon injecte dans le HTML du BSR
_BSR_INVISIBLE = "‎‏‪-‮\xa0  - "

# Rang + catégorie : tolère "n°1", "#1", "1 542", "1.542", "1,542", "en|dans|in"
_BSR_RANK_RE = re.compile(
    r"(?:n[°ºo]\s*|#)?\s*"                # préfixe optionnel n° / #
    r"([\d][\d\s.,\xa0 ]*?)"                   # le rang (chiffres + séparateurs)
    r"\s+(?:en|dans|in)\s+"                         # connecteur FR/EN
    r"([^()<\x00-\x1f]+)",                          # nom de catégorie
    re.IGNORECASE,
)
# Premier nombre isolé, en dernier recours (BSR sans catégorie lisible)
_BSR_NUM_RE = re.compile(r"([\d][\d\s.,\xa0 ]*\d|\d)")
# Préfixe "Classement des meilleures ventes (d'Amazon) :" / "Best Sellers Rank"
_BSR_LABEL_RE = re.compile(
    r"classement\s+des\s+meilleures\s+ventes(?:\s+d'amazon)?\s*:?|best\s*sellers\s*rank\s*:?",
    re.IGNORECASE,
)


def _flatten(text):
    """Aplatit un texte HTML : supprime retours ligne et caractères invisibles d'Amazon."""
    if not text:
        return ""
    t = text.replace("\n", " ").replace("\r", " ")
    t = re.sub(rf"[{_BSR_INVISIBLE}]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _parse_rank_int(raw):
    """'1 542' / '1.542' / 'n°1 542' -> 1542 (ou None)."""
    digits = re.sub(r"[^\d]", "", raw or "")
    return int(digits) if digits else None


def _clean_bsr_category(raw):
    """Isole le nom de catégorie en coupant aux parenthèses, 'Voir', 'n°', '#', '<'."""
    cat = _flatten(raw)
    cat = re.split(r"\(|Voir|n[°ºo]\s|#|<", cat, flags=re.IGNORECASE)[0]
    return clean_text(cat)


def _parse_zg_hrsr(container):
    """Extrait les BSR secondaires depuis un bloc <ul class='zg_hrsr'>."""
    subs = {}
    for li in container.select("ul.zg_hrsr li, .zg_hrsr li"):
        txt = _flatten(li.get_text())
        m = _BSR_RANK_RE.search(txt)
        if not m:
            continue
        rank = _parse_rank_int(m.group(1))
        cat = _clean_bsr_category(m.group(2))
        if rank and cat and len(cat) < 120:
            subs[cat] = rank
    return subs


def extract_bsr(soup):
    """
    Extracteur BSR unifié, couvrant toutes les structures HTML Amazon.fr connues.

    Retourne (bsr:int|None, bsr_category:str, bsr_sub:dict{cat: rang}).

    Stratégie : on collecte les conteneurs candidats (li de detailBullets + td des
    tableaux techniques), on extrait d'abord les sous-catégories (zg_hrsr), puis on
    isole la ligne du rang principal en retirant le bloc zg_hrsr cloné.
    """
    bsr, bsr_category, bsr_sub = None, "", {}
    candidates = []

    # 1. Structure "detail bullets" (liste à puces, cas le plus fréquent pour les livres)
    bullets = (soup.select_one("#detailBulletsWrapper_feature_div")
               or soup.select_one("#detailBullets_feature_div"))
    if bullets:
        for li in bullets.select("li"):
            label = _flatten(li.get_text()).lower()
            if "classement des meilleures ventes" in label or "best sellers rank" in label:
                candidates.append(li)

    # 2. Structures "tableau technique" (productDetails_* / prodDetTable)
    for row in soup.select(
        "#productDetails_detailBullets_sections1 tr,"
        "#productDetails_techSpec_section_1 tr,"
        "#productDetails_db_sections tr, .prodDetTable tr"
    ):
        th = row.select_one("th")
        td = row.select_one("td")
        if not th or not td:
            continue
        label = _flatten(th.get_text()).lower()
        if "classement" in label or "best sellers rank" in label or "meilleures ventes" in label:
            candidates.append(td)

    for container in candidates:
        if not bsr_sub:
            bsr_sub = _parse_zg_hrsr(container)

        # Clone pour isoler la ligne principale sans les sous-catégories
        clone = copy.copy(container)
        for nested in clone.select("ul.zg_hrsr, .zg_hrsr"):
            nested.decompose()

        text = _flatten(clone.get_text())
        # On se place après le libellé "Classement des meilleures ventes :"
        parts = _BSR_LABEL_RE.split(text)
        text = parts[-1] if len(parts) > 1 else text

        m = _BSR_RANK_RE.search(text)
        if m:
            rank = _parse_rank_int(m.group(1))
            if rank:
                bsr = rank
                bsr_category = _clean_bsr_category(m.group(2))
                break

        # Dernier recours : premier nombre rencontré, sans catégorie
        if bsr is None:
            m2 = _BSR_NUM_RE.search(text)
            if m2:
                rank = _parse_rank_int(m2.group(1))
                if rank:
                    bsr = rank

    # 3. Ultime repli : la page n'a pas exposé le BSR dans un conteneur connu
    #    (variantes allégées / soft-blocks Amazon) mais le libellé est dans le texte.
    if bsr is None:
        full = _flatten(soup.get_text())
        parts = _BSR_LABEL_RE.split(full)
        if len(parts) > 1:
            tail = parts[-1]
            m = _BSR_RANK_RE.search(tail)
            if m:
                rank = _parse_rank_int(m.group(1))
                if rank:
                    bsr = rank
                    bsr_category = _clean_bsr_category(m.group(2))

    return bsr, bsr_category, bsr_sub

def get_product_details(product_url):
    """
    Récupère les détails d'un produit (livre) à partir de sa page Amazon.
    Version de Production : Extraction par mots-clés à plat, tolérante aux structures instables.
    """
    details = {
        "subtitle": "", 
        "description": "", 
        "publication_date": "",
        "pages": None, 
        "bsr": None, 
        "bsr_category": "", 
        "bsr_sub": "{}",
        "rating_page": None, 
        "reviews_count_page": None
    }

    try:
        soup = get_soup(product_url, require=_has_product_details, referer=START_URL)

        # 1. Sous-titre
        el = soup.select_one("span#subtitle, #productSubtitle")
        if el:
            details["subtitle"] = clean_text(el.get_text())

        # 2. Description
        for sel in ["#bookDescription_feature_div", "#productDescription",
                    "div[data-feature-name='bookDescription']", "#feature-bullets"]:
            el = soup.select_one(sel)
            if el:
                for tag in el.select("span.a-expander-prompt, noscript, style, script"):
                    tag.decompose()
                raw = clean_text(el.get_text())
                if raw and len(raw) > 30:
                    details["description"] = raw[:1500]
                    break

        # 3. Note (Rating)
        el = soup.select_one("span#acrPopover, span[data-hook='rating-out-of-text']")
        if el:
            text = el.get("title", "") or el.get_text()
            match = re.search(r"(\d+[,.]?\d*)\s*sur\s*5", text)
            if match:
                details["rating_page"] = float(match.group(1).replace(",", "."))

        # 4. Nombre de commentaires
        el = soup.select_one("span#acrCustomerReviewText, span[data-hook='total-review-count']")
        if el:
            match = re.search(r"(\d+)", re.sub(r"[\xa0\s\u202f,]", "", el.get_text()))
            if match:
                details["reviews_count_page"] = int(match.group(1))

        # --- BSR : extracteur unifié multi-structures (detailBullets + tableaux) ---
        bsr, bsr_category, bsr_sub = extract_bsr(soup)
        details["bsr"] = bsr
        details["bsr_category"] = bsr_category
        details["bsr_sub"] = json.dumps(bsr_sub, ensure_ascii=False)

        # --- Pages & date de publication : bullets à puces d'abord ---
        bullets_div = soup.select_one("#detailBulletsWrapper_feature_div") or soup.select_one("#detailBullets_feature_div")
        if bullets_div:
            for li in bullets_div.select("li"):
                raw_text = _flatten(li.get_text())
                if not raw_text:
                    continue
                tl = raw_text.lower()

                if "pages" in tl and not details["pages"]:
                    match = re.search(r"(\d{1,4})\s*pages?", raw_text, re.I)
                    if match:
                        details["pages"] = int(match.group(1))

                if ("date de publication" in tl or "éditeur" in tl) and not details["publication_date"]:
                    match = re.search(r"(\d{1,2}\s+\w+\s+\d{4}|\d{4})", raw_text)
                    if match:
                        details["publication_date"] = match.group(1)

        # --- Pages & date : repli sur les tableaux techniques ---
        if not details["pages"] or not details["publication_date"]:
            for row in soup.select(
                "#productDetails_detailBullets_sections1 tr,"
                "#productDetails_techSpec_section_1 tr,"
                "#productDetails_db_sections tr, .prodDetTable tr"
            ):
                th = row.select_one("th")
                td = row.select_one("td")
                if not th or not td:
                    continue
                col_label = clean_text(th.get_text()).lower()
                value = clean_text(td.get_text())
                if "pages" in col_label and not details["pages"]:
                    match = re.search(r"(\d+)", value)
                    if match:
                        details["pages"] = int(match.group(1))
                if ("date" in col_label or "publication" in col_label) and not details["publication_date"]:
                    details["publication_date"] = value

    except Exception as e:
        safe_print(f"    [PAGE PRODUIT] Erreur {product_url}: {e}")

    return details

def practical_score(title):
    return sum(1 for word in PRACTICAL_KEYWORDS if word in title.lower())

def analyze_category(category_context, url, max_books=20):
    parent_name, parent_url, sub_name = category_context
    try:
        soup = get_soup(url, require=_has_listing_items, referer=START_URL)
    except Exception as e:
        safe_print(f"    [ERREUR LISTE] {sub_name}: {e}")
        return []

    items = soup.select("div.zg-grid-general-faceout")
    if not items:
        items = soup.select("div[id^='gridItemRoot']")

    if not items:
        return []

    books = []
    for rank, item in enumerate(items[:max_books], start=1):
        title = get_title(item)
        if not title:
            continue

        book = {
            "parent_category": parent_name,
            "parent_category_url": parent_url,
            "category": sub_name,
            "category_url": url,
            "rank_in_category": rank,
            "title": title,
            "price": get_price(item),
            "rating": get_rating(item),
            "reviews_count": get_reviews_count(item),
            "product_url": get_product_url(item),
            "practical": practical_score(title),
            "subtitle": "",
            "description": "",
            "publication_date": "",
            "pages": None,
            "bsr": None,
            "bsr_category": "",
            "bsr_sub": "{}"
        }

        if rank <= PRODUCT_PAGE_LIMIT and book["product_url"]:
            safe_print(f"      -> Page produit #{rank}: {title[:50]}...")
            details = get_product_details(book["product_url"])
            if book["rating"] is None:
                book["rating"] = details.get("rating_page")
            if book["reviews_count"] is None:
                book["reviews_count"] = details.get("reviews_count_page")
            for key in ["subtitle", "description", "publication_date", "pages", "bsr", "bsr_category", "bsr_sub"]:
                book[key] = details.get(key)
            time.sleep(random.uniform(0.8, 1.5))

        books.append(book)

    return books

def process_subcategory(args):
    parent_name, parent_url, sub_name, sub_url = args
    safe_print(f"  SUB: {sub_name} (Parent: {parent_name})")
    try:
        context = (parent_name, parent_url, sub_name)
        books = analyze_category(context, sub_url, max_books=20)
        return books
    except Exception as e:
        safe_print(f"  [ERREUR] {sub_name}: {e}")
        return []

def compute_scores(df):
    df_unique = df.drop_duplicates(subset=["product_url"], keep="first").copy()
    
    # Assurer le type pour éviter les erreurs de calcul d'indicateurs
    if "bsr" in df_unique.columns:
        df_unique["bsr"] = pd.to_numeric(df_unique["bsr"], errors="coerce")

    def bsr_metrics(x):
        valid_bsrs = x.dropna()
        if valid_bsrs.empty:
            return pd.Series([None, None, 0.0], index=["bsr_median", "bsr_min", "pct_with_bsr"])
        pct = (len(valid_bsrs) / len(x)) * 100
        return pd.Series([valid_bsrs.median(), valid_bsrs.min(), round(pct, 1)], index=["bsr_median", "bsr_min", "pct_with_bsr"])

    # Groupby préservant la hiérarchie parent_category
    grp = df_unique.groupby(["parent_category", "category"])
    
    summary = grp.agg(
        url=("category_url", "first"),
        books=("title", "count"),
        avg_price=("price", "mean"),
        median_price=("price", "median"),
        avg_rating=("rating", "mean"),
        total_reviews=("reviews_count", "sum"),
        avg_reviews=("reviews_count", "mean"),
        practical=("practical", "sum")
    ).reset_index()

    # Jointure avec nos métriques personnalisées du BSR
    bsr_df = grp["bsr"].apply(bsr_metrics).unstack(level=-1).reset_index()
    summary = pd.merge(summary, bsr_df, on=["parent_category", "category"], how="left")

    summary["avg_price"]     = summary["avg_price"].fillna(0).round(2)
    summary["median_price"]  = summary["median_price"].fillna(0).round(2)
    summary["avg_rating"]    = summary["avg_rating"].fillna(0).round(2)
    summary["total_reviews"] = summary["total_reviews"].fillna(0).astype(int)
    summary["avg_reviews"]   = summary["avg_reviews"].fillna(0).round(0)

    summary["price_score"] = summary["avg_price"].apply(lambda x: round(min((x / 20) * 25, 25), 1) if x > 0 else 0)
    max_prac = summary["practical"].max()
    summary["practical_score"] = summary["practical"].apply(lambda x: round((x / max_prac) * 20, 1) if max_prac > 0 else 0)
    summary["business_score"] = summary["category"].apply(lambda x: 20 if is_business_category(x) else 0)
    max_rev = summary["total_reviews"].max()
    summary["demand_score"] = summary["total_reviews"].apply(lambda x: round((x / max_rev) * 25, 1) if max_rev > 0 else 0)
    summary["quality_score"] = summary["avg_rating"].apply(lambda x: round((x / 5) * 10, 1))

    summary["kdp_score"] = (
        summary["price_score"] + summary["practical_score"] + summary["business_score"] + summary["demand_score"] + summary["quality_score"]
    ).round(1)

    summary["decision"] = summary["kdp_score"].apply(lambda x: "GO" if x >= 70 else "A surveiller" if x >= 40 else "STOP")

    return summary.sort_values("kdp_score", ascending=False)

def main():
    all_books = []
    categories_done = 0

    main_categories = get_main_categories()
    safe_print(f"Categories principales detectees : {len(main_categories)}")

    for main_name, main_url in main_categories:
        if not is_business_category(main_name):
            continue

        safe_print(f"MAIN: {main_name}")
        subcategories = get_subcategories(main_url)
        if not subcategories:
            subcategories = [(main_name, main_url)]

        subs_to_process = [
            (main_name, main_url, sub_name, sub_url)
            for sub_name, sub_url in subcategories
            if is_business_category(sub_name)
        ]

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {
                executor.submit(process_subcategory, args): args[2]
                for args in subs_to_process
            }
            for future in as_completed(futures):
                books = future.result()
                if books:
                    all_books.extend(books)
                    categories_done += 1
                    if categories_done % 5 == 0:
                        save_intermediate(all_books)

        time.sleep(random.uniform(1.0, 2.0))

    if not all_books:
        safe_print("Aucune donnee recuperee.")
        return

    df = pd.DataFrame(all_books)
    df = df[df["title"].notna() & (df["title"].str.strip() != "")]
    df_dedup = df.drop_duplicates(subset=["title", "category"], keep="first")

    summary = compute_scores(df_dedup)

    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="scores", index=False)
        df_dedup.to_excel(writer, sheet_name="data", index=False)

    try:
        Path(TEMP_FILE).unlink(missing_ok=True)
    except Exception:
        pass

    safe_print(f"\nTermine : {OUTPUT_FILE}")
    safe_print(f"   Categories     : {len(summary)}")
    safe_print(f"   Livres scrapes : {len(df_dedup)}")


if __name__ == "__main__":
    main()