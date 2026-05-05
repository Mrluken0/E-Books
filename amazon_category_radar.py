import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
import re
from datetime import datetime

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


def practical_score(title):
    title = title.lower()
    return sum(1 for word in PRACTICAL_KEYWORDS if word in title)


def analyze_category(category, url, max_books=20):
    soup = get_soup(url)
    books = []

    items = soup.select("div.zg-grid-general-faceout")

    if not items:
        items = soup.select("div[id^='gridItemRoot']")

    for item in items[:max_books]:
        title = get_title(item)

        if not title:
            continue

        books.append({
            "category": category,
            "url": url,
            "title": title,
            "price": get_price(item),
            "practical": practical_score(title)
        })

    return books


def compute_scores(df):
    summary = df.groupby("category").agg(
        url=("url", "first"),
        books=("title", "count"),
        avg_price=("price", "mean"),
        median_price=("price", "median"),
        practical=("practical", "sum")
    ).reset_index()

    summary["avg_price"] = summary["avg_price"].fillna(0).round(2)
    summary["median_price"] = summary["median_price"].fillna(0).round(2)

    summary["price_score"] = summary["avg_price"].apply(
        lambda x: 0 if x == 0 else min(x * 2, 30)
    )

    summary["practical_score"] = summary["practical"] * 4

    summary["business_score"] = summary["category"].apply(
        lambda x: 20 if is_business_category(x) else 0
    )

    summary["kdp_score"] = (
        summary["price_score"]
        + summary["practical_score"]
        + summary["business_score"]
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

    print(f"OK : {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
