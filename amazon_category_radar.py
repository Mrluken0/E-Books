import sys
import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
import re
import random
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

BASE_URL = "https://www.amazon.fr"
START_URL = "https://www.amazon.fr/gp/bestsellers/books"

OUTPUT_FILE = r"C:\Users\luken\Desktop\LKN Digital\Automation\KDP-Automation\radar_kdp_clean.xlsx"
TEMP_FILE   = r"C:\Users\luken\Desktop\LKN Digital\Automation\KDP-Automation\radar_kdp_temp.xlsx"

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

# ── 2. Headers complets ───────────────────────────────────────────────────────
def get_headers():
    return {
        "User-Agent": random.choice(UA_LIST),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Cache-Control": "max-age=0",
    }

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

PRODUCT_PAGE_LIMIT = 10

BLOCK_SIGNALS = [
    "robot check", "captcha", "enter the characters you see below",
    "sorry, we just need to make sure you're not a robot",
    "veuillez saisir les caractères", "api-services-support@amazon.com"
]


def clean_text(text):
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


# ── 3. Retry automatique ─────────────────────────────────────────────────────
def get_soup(url, max_retries=3):
    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=get_headers(), timeout=25)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")

            # ── 4. Détection blocage Amazon ──────────────────────────────────
            page_text = soup.get_text().lower()
            if any(signal in page_text for signal in BLOCK_SIGNALS):
                wait = (attempt + 1) * random.uniform(8, 15)
                print(f"    [BLOCAGE] Amazon bloque. Attente {wait:.0f}s (tentative {attempt+1}/{max_retries})")
                time.sleep(wait)
                continue

            return soup

        except requests.exceptions.HTTPError as e:
            code = e.response.status_code
            if code in (429, 503):
                wait = (attempt + 1) * random.uniform(5, 10)
                print(f"    [HTTP {code}] Rate limited. Attente {wait:.0f}s (tentative {attempt+1}/{max_retries})")
                time.sleep(wait)
            else:
                raise
        except requests.exceptions.RequestException as e:
            if attempt < max_retries - 1:
                wait = (attempt + 1) * random.uniform(3, 6)
                print(f"    [RESEAU] {e}. Attente {wait:.0f}s (tentative {attempt+1}/{max_retries})")
                time.sleep(wait)
            else:
                raise

    raise Exception(f"Echec apres {max_retries} tentatives : {url}")


# ── 5. Sauvegarde intermédiaire ───────────────────────────────────────────────
def save_intermediate(all_books):
    if not all_books:
        return
    try:
        df = pd.DataFrame(all_books)
        df = df[df["title"].notna() & (df["title"].str.strip() != "")]
        if df.empty:
            return
        summary = compute_scores(df)
        with pd.ExcelWriter(TEMP_FILE, engine="openpyxl") as writer:
            summary.to_excel(writer, sheet_name="scores", index=False)
            df.to_excel(writer, sheet_name="data", index=False)
        print(f"    [SAVE] Sauvegarde temp OK ({len(df)} livres, {len(summary)} categories)")
    except Exception as e:
        print(f"    [SAVE] Erreur : {e}")


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
    soup = get_soup(START_URL)
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
    soup = get_soup(url)
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
    for el in item.select("i[class*='a-icon-star'], span[class*='a-icon-star']"):
        match = re.search(r"(\d+[,.]?\d*)", el.get("aria-label", ""))
        if match:
            val = float(match.group(1).replace(",", "."))
            if 1.0 <= val <= 5.0:
                return val
    match = re.search(r"(\d+[,.]?\d*)\s*sur\s*5", item.get_text())
    if match:
        val = float(match.group(1).replace(",", "."))
        if 1.0 <= val <= 5.0:
            return val
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
    for el in item.select("span.a-size-small, span.a-size-base"):
        text = el.get_text().strip()
        if re.match(r"^\d[\d\s,\.]*$", text) or re.match(r"^\d[\d\s,\.]*\s*(avis|évaluation)", text, re.I):
            val = parse_count(text)
            if val and 1 <= val <= 500000:
                return val
    matches = re.findall(r"(\d[\d\s]*)\s*(?:avis|évaluations|évaluation)", item.get_text(), re.I)
    if matches:
        val = parse_count(matches[0])
        if val and val > 0:
            return val
    return None


def get_product_url(item):
    link = item.select_one("a[href*='/dp/']")
    if link:
        return normalize_url(link.get("href", "").split("?")[0])
    img = item.select_one("img")
    if img:
        parent = img.find_parent("a")
        if parent and "/dp/" in parent.get("href", ""):
            return normalize_url(parent["href"].split("?")[0])
    return None


def get_product_details(product_url):
    details = {
        "subtitle": "", "description": "", "publication_date": "",
        "pages": None, "bsr": None, "rating_page": None, "reviews_count_page": None
    }

    try:
        soup = get_soup(product_url)

        el = soup.select_one("span#subtitle")
        if el:
            details["subtitle"] = clean_text(el.get_text())

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

        el = soup.select_one("span#acrPopover, span[data-hook='rating-out-of-text']")
        if el:
            text = el.get("title", "") or el.get_text()
            match = re.search(r"(\d+[,.]?\d*)\s*sur\s*5", text)
            if match:
                details["rating_page"] = float(match.group(1).replace(",", "."))

        el = soup.select_one("span#acrCustomerReviewText, span[data-hook='total-review-count']")
        if el:
            match = re.search(r"(\d+)", re.sub(r"[\xa0\s,]", "", el.get_text()))
            if match:
                details["reviews_count_page"] = int(match.group(1))

        for li in soup.select("#detailBulletsWrapper_feature_div li, #detailBullets_feature_div li"):
            text = clean_text(li.get_text())
            tl = text.lower()
            if ("nombre de pages" in tl or "pages" in tl) and not details["pages"]:
                match = re.search(r"(\d{2,4})\s*pages?", text, re.I)
                if match:
                    details["pages"] = int(match.group(1))
            if ("date de publication" in tl or "éditeur" in tl) and not details["publication_date"]:
                match = re.search(r"(\d{1,2}\s+\w+\s+\d{4}|\d{4})", text)
                if match:
                    details["publication_date"] = match.group(1)
            if ("classement" in label or "best" in label) and not details["bsr"]:
                match = re.search(r":\s*([\d\s]+)\s+en\s+", value)
                if not match:
                    match = re.search(r":\s*([\d\s]+)\s+dans\s+", value)
                if not match:
                    match = re.search(r"n[°o]?\s*(\d[\d\s]*)", value, re.I)
                if match:
                    details["bsr"] = int(re.sub(r"\s", "", match.group(1)))

        for row in soup.select(
            "#productDetails_detailBullets_sections1 tr,"
            "#productDetails_techSpec_section_1 tr,"
            "#productDetails_db_sections tr"
        ):
            th = row.select_one("th")
            td = row.select_one("td")
            if not th or not td:
                continue
            label = clean_text(th.get_text()).lower()
            value = clean_text(td.get_text())
            if "pages" in label and not details["pages"]:
                match = re.search(r"(\d+)", value)
                if match:
                    details["pages"] = int(match.group(1))
            if ("date" in label or "publication" in label) and not details["publication_date"]:
                details["publication_date"] = value
            if ("classement" in label or "best" in label) and not details["bsr"]:
                match = re.search(r"#\s*(\d[\d\s\.]*)", value) or re.search(r"(\d[\d\s\.]*)", value)
                if match:
                    details["bsr"] = int(re.sub(r"[\s\.]", "", match.group(1)))

    except Exception as e:
        print(f"    [PAGE PRODUIT] Erreur {product_url}: {e}")

    return details


def practical_score(title):
    return sum(1 for word in PRACTICAL_KEYWORDS if word in title.lower())


def analyze_category(category, url, max_books=20):
    try:
        soup = get_soup(url)
    except Exception as e:
        print(f"    [ERREUR LISTE] {category}: {e}")
        return []

    items = soup.select("div.zg-grid-general-faceout")
    if not items:
        items = soup.select("div[id^='gridItemRoot']")

    if not items:
        page_text = soup.get_text().lower()
        if any(s in page_text for s in BLOCK_SIGNALS):
            print(f"    [BLOCAGE SILENCIEUX] {category}")
        else:
            print(f"    [VIDE] Aucun item pour {category}")
        return []

    books = []
    for rank, item in enumerate(items[:max_books], start=1):
        title = get_title(item)
        if not title:
            continue

        book = {
            "category": category,
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
            "bsr": None
        }

        if rank <= PRODUCT_PAGE_LIMIT and book["product_url"]:
            print(f"      -> Page produit #{rank}: {title[:50]}...")
            details = get_product_details(book["product_url"])
            if book["rating"] is None:
                book["rating"] = details.get("rating_page")
            if book["reviews_count"] is None:
                book["reviews_count"] = details.get("reviews_count_page")
            for key in ["subtitle", "description", "publication_date", "pages", "bsr"]:
                book[key] = details.get(key)
            # ── 2. Délai aléatoire page produit ──────────────────────────────
            time.sleep(random.uniform(1.0, 2.5))

        books.append(book)

    return books


def compute_scores(df):
    summary = df.groupby("category").agg(
        url=("category_url", "first"),
        books=("title", "count"),
        avg_price=("price", "mean"),
        median_price=("price", "median"),
        avg_rating=("rating", "mean"),
        total_reviews=("reviews_count", "sum"),
        avg_reviews=("reviews_count", "mean"),
        practical=("practical", "sum")
    ).reset_index()

    summary["avg_price"]     = summary["avg_price"].fillna(0).round(2)
    summary["median_price"]  = summary["median_price"].fillna(0).round(2)
    summary["avg_rating"]    = summary["avg_rating"].fillna(0).round(2)
    summary["total_reviews"] = summary["total_reviews"].fillna(0).astype(int)
    summary["avg_reviews"]   = summary["avg_reviews"].fillna(0).round(0)

    summary["price_score"]    = summary["avg_price"].apply(lambda x: 0 if x == 0 else min(x * 2, 30))
    summary["practical_score"] = summary["practical"] * 4
    summary["business_score"] = summary["category"].apply(lambda x: 20 if is_business_category(x) else 0)

    max_rev = summary["total_reviews"].max()
    summary["demand_score"]  = summary["total_reviews"].apply(
        lambda x: round((x / max_rev) * 20, 1) if max_rev > 0 else 0
    )
    summary["quality_score"] = summary["avg_rating"].apply(lambda x: round(x * 2, 1))

    summary["kdp_score"] = (
        summary["price_score"] + summary["practical_score"]
        + summary["business_score"] + summary["demand_score"]
        + summary["books"]
    ).round(1)

    summary["decision"] = summary["kdp_score"].apply(
        lambda x: "GO" if x >= 70 else "A surveiller" if x >= 40 else "STOP"
    )

    return summary.sort_values("kdp_score", ascending=False)


def main():
    all_books = []
    categories_done = 0

    main_categories = get_main_categories()
    print(f"Categories principales detectees : {len(main_categories)}")

    for main_name, main_url in main_categories:
        if not is_business_category(main_name):
            continue

        print(f"MAIN: {main_name}")

        subcategories = get_subcategories(main_url)
        if not subcategories:
            subcategories = [(main_name, main_url)]

        for sub_name, sub_url in subcategories:
            if not is_business_category(sub_name):
                continue

            print(f"  SUB: {sub_name}")

            try:
                books = analyze_category(sub_name, sub_url, max_books=20)
                if books:
                    all_books.extend(books)
                    categories_done += 1
                    print(f"    -> {len(books)} livres collectes")

                    # ── 5. Sauvegarde toutes les 5 catégories ────────────────
                    if categories_done % 5 == 0:
                        save_intermediate(all_books)

            except Exception as e:
                print(f"  [ERREUR] {sub_name}: {e}")

            # ── 2. Délai aléatoire entre sous-catégories ────────────────────
            time.sleep(random.uniform(1.5, 3.0))

    if not all_books:
        print("Aucune donnee recuperee.")
        return

    df = pd.DataFrame(all_books)
    df = df[df["title"].notna() & (df["title"].str.strip() != "")]

    summary = compute_scores(df)

    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="scores", index=False)
        df.to_excel(writer, sheet_name="data", index=False)

    try:
        Path(TEMP_FILE).unlink(missing_ok=True)
    except Exception:
        pass

    print(f"\nTermine : {OUTPUT_FILE}")
    print(f"   Categories     : {len(summary)}")
    print(f"   Livres scrapes : {len(df)}")
    print(f"   Descriptions   : {df['description'].notna().sum()} / {len(df)}")
    print(f"   Ratings        : {df['rating'].notna().sum()} / {len(df)}")
    print(f"   Reviews count  : {df['reviews_count'].notna().sum()} / {len(df)}")
    print(f"   BSR            : {df['bsr'].notna().sum()} / {len(df)}")


if __name__ == "__main__":
    main()
