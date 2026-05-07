import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
import re
from datetime import datetime
import sys
sys.stdout.reconfigure(encoding="utf-8")

BASE_URL = "https://www.amazon.fr"
START_URL = "https://www.amazon.fr/gp/bestsellers/books"

OUTPUT_FILE = r"C:\Users\luken\Desktop\LKN Digital\Automation\KDP-Automation\radar_kdp_clean.xlsx"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8"
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

# Nombre de livres pour lesquels on visite la page produit
PRODUCT_PAGE_LIMIT = 10


def clean_text(text):
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def get_soup(url):
    response = requests.get(url, headers=HEADERS, timeout=20)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def is_excluded(name):
    name = name.lower()
    return any(word in name for word in EXCLUDED)


def is_business_category(name):
    name = name.lower()
    return any(word in name for word in BUSINESS_KEYWORDS)


def valid_category(name, href):
    if not name or not href:
        return False
    name_lower = name.lower().strip()
    if len(name_lower) < 3:
        return False
    if name_lower.isdigit():
        return False
    if is_excluded(name_lower):
        return False
    if "/gp/bestsellers/books/" not in href:
        return False
    if "pg=" in href:
        return False
    return True


def normalize_url(href):
    if href.startswith("/"):
        return BASE_URL + href
    return href


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
    text = text.replace(",", ".")
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    if match:
        return float(match.group(1))
    return None


def get_price(item):
    selectors = [
        ".a-price .a-offscreen",
        ".p13n-sc-price",
        "span.a-color-price",
        "span.a-size-base.a-color-price"
    ]
    for selector in selectors:
        price_el = item.select_one(selector)
        if price_el:
            price = extract_price(price_el.get_text())
            if price is not None:
                return price
    return None


def get_title(item):
    img = item.select_one("img")
    if img and img.get("alt"):
        return clean_text(img.get("alt"))
    selectors = [
        "div._cDEzb_p13n-sc-css-line-clamp-1_1Fn1y",
        "div._cDEzb_p13n-sc-css-line-clamp-2_EWgCb",
        "span.a-size-medium",
        "span.a-size-base-plus"
    ]
    for selector in selectors:
        title_el = item.select_one(selector)
        if title_el:
            title = clean_text(title_el.get_text())
            if title:
                return title
    return ""


def get_rating(item):
    """Extrait la note moyenne depuis la page liste."""
    selectors = [
        "span.a-icon-alt",
        "i.a-icon-star span.a-icon-alt",
        "span[aria-label*='étoile']",
        "span[aria-label*='sur 5']"
    ]
    for selector in selectors:
        el = item.select_one(selector)
        if el:
            text = el.get_text()
            match = re.search(r"(\d+[,.]?\d*)\s*sur\s*5", text)
            if match:
                return float(match.group(1).replace(",", "."))
    # Fallback : aria-label sur l'élément étoile
    star_el = item.select_one("i[class*='a-icon-star']")
    if star_el:
        aria = star_el.get("aria-label", "")
        match = re.search(r"(\d+[,.]?\d*)", aria)
        if match:
            return float(match.group(1).replace(",", "."))
    return None


def get_reviews_count(item):
    """Extrait le nombre de reviews depuis la page liste."""
    selectors = [
        "span.a-size-small a span",
        "span[aria-label*='avis']",
        "a[href*='customerReviews'] span",
        "div.a-section span.a-size-small"
    ]
    for selector in selectors:
        el = item.select_one(selector)
        if el:
            text = el.get_text().replace("\xa0", "").replace(" ", "").replace(",", "")
            match = re.search(r"(\d+)", text)
            if match:
                return int(match.group(1))
    return None


def get_product_url(item):
    """Extrait l'URL de la page produit depuis un item de la liste."""
    link = item.select_one("a[href*='/dp/']")
    if link:
        href = link.get("href", "")
        return normalize_url(href.split("?")[0])
    img = item.select_one("img")
    if img:
        parent = img.find_parent("a")
        if parent and "/dp/" in parent.get("href", ""):
            return normalize_url(parent["href"].split("?")[0])
    return None


def get_product_details(product_url):
    """
    Visite la page produit et extrait :
    - sous-titre
    - description
    - date de publication
    - nombre de pages
    - BSR (Best Seller Rank réel dans la catégorie)
    """
    details = {
        "subtitle": "",
        "description": "",
        "publication_date": "",
        "pages": None,
        "bsr": None
    }

    try:
        soup = get_soup(product_url)

        # --- Sous-titre ---
        subtitle_el = soup.select_one("span#subtitle")
        if subtitle_el:
            details["subtitle"] = clean_text(subtitle_el.get_text())

        # --- Description ---
        desc_selectors = [
            "#bookDescription_feature_div",
            "#productDescription",
            "div[data-feature-name='bookDescription']"
        ]
        for sel in desc_selectors:
            desc_el = soup.select_one(sel)
            if desc_el:
                # Supprimer les balises "Lire la suite" etc.
                for tag in desc_el.select("span.a-expander-prompt, noscript"):
                    tag.decompose()
                raw = clean_text(desc_el.get_text())
                if raw:
                    details["description"] = raw[:1500]  # cap à 1500 chars
                    break

        # --- Détails produit (pages, date, BSR) ---
        # Format 1 : detailBullets
        bullets = soup.select("#detailBulletsWrapper_feature_div li, #detailBullets_feature_div li")
        for li in bullets:
            text = clean_text(li.get_text())
            text_lower = text.lower()

            if "nombre de pages" in text_lower or "pages" in text_lower:
                match = re.search(r"(\d{2,4})\s*pages?", text, re.IGNORECASE)
                if match:
                    details["pages"] = int(match.group(1))

            if "date de publication" in text_lower or "éditeur" in text_lower:
                match = re.search(r"(\d{1,2}\s+\w+\s+\d{4}|\d{4})", text)
                if match:
                    details["publication_date"] = match.group(1)

            if "classement" in text_lower or "best.seller" in text_lower or "bsr" in text_lower:
                match = re.search(r"n[°o]?\s*(\d[\d\s]*)", text, re.IGNORECASE)
                if match:
                    details["bsr"] = int(match.group(1).replace(" ", ""))

        # Format 2 : productDetails table
        if not details["pages"] or not details["publication_date"]:
            rows = soup.select("#productDetails_detailBullets_sections1 tr, #productDetails_techSpec_section_1 tr")
            for row in rows:
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
                    match = re.search(r"(\d[\d\s]*)", value)
                    if match:
                        details["bsr"] = int(match.group(1).replace(" ", ""))

    except Exception as e:
        print(f"    ⚠️ Erreur page produit {product_url}: {e}")

    return details


def practical_score(title):
    title = title.lower()
    return sum(1 for word in PRACTICAL_KEYWORDS if word in title)


def analyze_category(category, url, max_books=20):
    soup = get_soup(url)
    books = []

    items = soup.select("div.zg-grid-general-faceout")
    if not items:
        items = soup.select("div[id^='gridItemRoot']")

    for rank, item in enumerate(items[:max_books], start=1):
        title = get_title(item)
        if not title:
            continue

        price = get_price(item)
        rating = get_rating(item)
        reviews_count = get_reviews_count(item)
        product_url = get_product_url(item)

        book = {
            "category": category,
            "category_url": url,
            "rank_in_category": rank,
            "title": title,
            "price": price,
            "rating": rating,
            "reviews_count": reviews_count,
            "product_url": product_url,
            "practical": practical_score(title),
            # Champs page produit — remplis uniquement pour top PRODUCT_PAGE_LIMIT
            "subtitle": "",
            "description": "",
            "publication_date": "",
            "pages": None,
            "bsr": None
        }

        # Visite page produit pour le top N
        if rank <= PRODUCT_PAGE_LIMIT and product_url:
            print(f"      → Page produit #{rank}: {title[:50]}...")
            details = get_product_details(product_url)
            book.update(details)
            time.sleep(0.8)  # délai entre pages produit

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

    summary["avg_price"] = summary["avg_price"].fillna(0).round(2)
    summary["median_price"] = summary["median_price"].fillna(0).round(2)
    summary["avg_rating"] = summary["avg_rating"].fillna(0).round(2)
    summary["total_reviews"] = summary["total_reviews"].fillna(0).astype(int)
    summary["avg_reviews"] = summary["avg_reviews"].fillna(0).round(0)

    summary["price_score"] = summary["avg_price"].apply(
        lambda x: 0 if x == 0 else min(x * 2, 30)
    )

    summary["practical_score"] = summary["practical"] * 4

    summary["business_score"] = summary["category"].apply(
        lambda x: 20 if is_business_category(x) else 0
    )

    # Score demande : basé sur le volume de reviews (proxy de popularité)
    max_reviews = summary["total_reviews"].max()
    summary["demand_score"] = summary["total_reviews"].apply(
        lambda x: round((x / max_reviews) * 20, 1) if max_reviews > 0 else 0
    )

    # Score qualité marché : note moyenne (entre 0 et 10)
    summary["quality_score"] = summary["avg_rating"].apply(
        lambda x: round(x * 2, 1)
    )

    summary["kdp_score"] = (
        summary["price_score"]
        + summary["practical_score"]
        + summary["business_score"]
        + summary["demand_score"]
        + summary["books"]
    ).round(1)

    summary["decision"] = summary["kdp_score"].apply(
        lambda x: "GO" if x >= 70 else "À surveiller" if x >= 40 else "STOP"
    )

    return summary.sort_values("kdp_score", ascending=False)


def main():
    all_books = []

    main_categories = get_main_categories()
    print(f"Catégories principales détectées : {len(main_categories)}")

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
                all_books.extend(books)
                time.sleep(0.8)
            except Exception as e:
                print(f"Erreur sur {sub_name}: {e}")

    if not all_books:
        print("Aucune donnée récupérée.")
        return

    df = pd.DataFrame(all_books)
    df = df[df["title"].notna()]
    df = df[df["title"].str.strip() != ""]

    summary = compute_scores(df)

    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="scores", index=False)
        df.to_excel(writer, sheet_name="data", index=False)

    print(f"\n✅ Terminé : {OUTPUT_FILE}")
    print(f"   Catégories : {len(summary)}")
    print(f"   Livres scrapés : {len(df)}")
    print(f"   Livres avec page produit : {df['description'].notna().sum()}")


if __name__ == "__main__":
    main()
