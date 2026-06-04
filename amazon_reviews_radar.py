import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
import re
import random
from datetime import datetime
from pathlib import Path

BASE_URL = "https://www.amazon.fr"

BASE_DIR = Path(r"C:\Users\luken\Desktop\LKN Digital\Automation\KDP-Automation")
RADAR_FILE = BASE_DIR / "radar_kdp_clean.xlsx"
OUTPUT_FILE = BASE_DIR / "reviews_radar.xlsx"

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

BLOCK_SIGNALS = [
    "robot check", "captcha", "enter the characters you see below",
    "sorry, we just need to make sure you're not a robot",
    "veuillez saisir les caractères", "api-services-support@amazon.com"
]

# Nombre de bestsellers par niche dont on scrape les reviews
TOP_BOOKS_PER_NICHE = 5

# Nombre max de reviews par livre (par filtre étoile)
MAX_REVIEWS_PER_FILTER = 15

# Filtres étoiles : critical = 1-2 étoiles, three_star = 3 étoiles
STAR_FILTERS = ["critical", "three_star"]

# Score minimum de la niche pour être traitée
MIN_KDP_SCORE = 40


def clean_text(text):
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


# ── 2. Headers complets avec rotation ────────────────────────────────────────
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


# ── 3. get_soup avec retry x3 ────────────────────────────────────────────────
def get_soup(url, max_retries=3):
    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=get_headers(), timeout=25)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")

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


def extract_asin(product_url):
    if not product_url:
        return None
    match = re.search(r"/dp/([A-Z0-9]{10})", str(product_url))
    if match:
        return match.group(1)
    match = re.search(r"/gp/product/([A-Z0-9]{10})", str(product_url))
    if match:
        return match.group(1)
    return None


def build_reviews_url(asin, star_filter, page=1):
    return (
        f"{BASE_URL}/product-reviews/{asin}/"
        f"?sortBy=recent&filterByStar={star_filter}&pageNumber={page}"
    )


def parse_rating_from_text(text):
    if not text:
        return None
    match = re.search(r"(\d+[,.]?\d*)\s*sur\s*5", text)
    if match:
        return float(match.group(1).replace(",", "."))
    return None


def scrape_reviews_page(asin, star_filter, page=1):
    url = build_reviews_url(asin, star_filter, page)
    reviews = []

    try:
        soup = get_soup(url)
        review_items = soup.select("div[data-hook='review']")

        for item in review_items:
            rating = None
            rating_el = item.select_one("i[data-hook='review-star-rating'] span.a-icon-alt")
            if not rating_el:
                rating_el = item.select_one("i[data-hook='cmps-review-star-rating'] span.a-icon-alt")
            if rating_el:
                rating = parse_rating_from_text(rating_el.get_text())

            title = ""
            title_el = item.select_one("a[data-hook='review-title'] span:not(.a-icon-alt)")
            if not title_el:
                title_el = item.select_one("span[data-hook='review-title']")
            if title_el:
                title = clean_text(title_el.get_text())

            body = ""
            body_el = item.select_one("span[data-hook='review-body'] span")
            if not body_el:
                body_el = item.select_one("div[data-hook='review-collapsed'] span")
            if body_el:
                body = clean_text(body_el.get_text())

            date = ""
            date_el = item.select_one("span[data-hook='review-date']")
            if date_el:
                date = clean_text(date_el.get_text())

            if body:
                reviews.append({
                    "rating": rating,
                    "review_title": title,
                    "review_body": body,
                    "review_date": date
                })

    except Exception as e:
        print(f"      [ERREUR] Reviews ASIN {asin}, filtre {star_filter}, p{page}: {e}")

    return reviews


def scrape_book_reviews(asin, book_title, max_per_filter=MAX_REVIEWS_PER_FILTER):
    all_reviews = []

    for star_filter in STAR_FILTERS:
        collected = []
        page = 1

        while len(collected) < max_per_filter:
            reviews = scrape_reviews_page(asin, star_filter, page)

            if not reviews:
                break

            collected.extend(reviews)
            page += 1
            # ── Délai aléatoire entre pages ──────────────────────────────────
            time.sleep(random.uniform(0.8, 1.8))

            if page > 3:
                break

        collected = collected[:max_per_filter]
        all_reviews.extend(collected)
        print(f"        Filtre '{star_filter}' : {len(collected)} reviews")

    return all_reviews


def extract_pain_points(reviews_df):
    if reviews_df.empty:
        return ""

    bodies = reviews_df["review_body"].dropna().tolist()
    combined = " | ".join(bodies[:30])
    combined = re.sub(r"\s+", " ", combined).strip()
    return combined[:3000]


def main():
    print(f"Lecture du radar : {RADAR_FILE}")

    df_scores = pd.read_excel(RADAR_FILE, sheet_name="scores")
    df_data = pd.read_excel(RADAR_FILE, sheet_name="data")

    df_go = df_scores[df_scores["kdp_score"] >= MIN_KDP_SCORE].copy()
    print(f"Niches retenues (score >= {MIN_KDP_SCORE}) : {len(df_go)}")

    all_reviews = []
    niche_summaries = []

    for _, niche_row in df_go.iterrows():
        category = niche_row["category"]
        print(f"\nNiche : {category}")

        df_books = df_data[df_data["category"] == category].copy()

        if "rank_in_category" in df_books.columns:
            df_books = df_books.sort_values("rank_in_category")

        top_books = df_books.head(TOP_BOOKS_PER_NICHE)

        niche_reviews = []

        for _, book in top_books.iterrows():
            title = book.get("title", "")
            product_url = book.get("product_url", "")
            asin = extract_asin(product_url)

            if not asin:
                print(f"  [ASIN MANQUANT] {title[:60]}")
                continue

            print(f"  Livre : {title[:60]}...")
            print(f"  ASIN  : {asin}")

            reviews = scrape_book_reviews(asin, title)

            for review in reviews:
                review.update({
                    "category": category,
                    "book_title": title,
                    "asin": asin,
                    "product_url": product_url
                })

            niche_reviews.extend(reviews)
            all_reviews.extend(reviews)

            # ── Délai aléatoire entre livres ─────────────────────────────────
            time.sleep(random.uniform(1.5, 3.0))

        if niche_reviews:
            niche_df = pd.DataFrame(niche_reviews)
            pain_points = extract_pain_points(niche_df)

            niche_summaries.append({
                "category": category,
                "kdp_score": niche_row.get("kdp_score", 0),
                "books_analyzed": top_books.shape[0],
                "reviews_collected": len(niche_reviews),
                "avg_negative_rating": round(niche_df["rating"].mean(), 2) if "rating" in niche_df else None,
                "pain_points_raw": pain_points
            })

            print(f"  -> {len(niche_reviews)} reviews collectées")
        else:
            print(f"  -> Aucune review collectée")

    if not all_reviews:
        print("\nAucune review récupérée.")
        return

    df_reviews = pd.DataFrame(all_reviews)
    df_summaries = pd.DataFrame(niche_summaries)

    review_cols = [
        "category", "book_title", "asin", "product_url",
        "rating", "review_title", "review_body", "review_date"
    ]
    review_cols = [c for c in review_cols if c in df_reviews.columns]
    df_reviews = df_reviews[review_cols]

    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        df_summaries.to_excel(writer, sheet_name="pain_points", index=False)
        df_reviews.to_excel(writer, sheet_name="reviews_detail", index=False)

    print(f"\nTermine : {OUTPUT_FILE}")
    print(f"   Niches analysées    : {len(df_summaries)}")
    print(f"   Reviews collectées  : {len(df_reviews)}")


if __name__ == "__main__":
    main()
