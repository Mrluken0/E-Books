import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
import re
import random
import json
import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

BASE_URL = "https://www.amazon.fr"

BASE_DIR    = Path(r"C:/LKN_Digital/KDP")
RADAR_FILE  = BASE_DIR / "radar_kdp_clean.xlsx"
OUTPUT_FILE = BASE_DIR / "reviews_radar.xlsx"

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

MIN_REVELANT_BOOKS     = 6
TOP_BOOKS_PER_NICHE    = 5
MAX_BOOKS_TARGETED     = 10
MAX_REVIEWS_PER_FILTER = 15
STAR_FILTERS           = ["critical", "three_star"]
MIN_KDP_SCORE          = 40


def clean_text(text):
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


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


def get_soup(url, max_retries=3):
    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=get_headers(), timeout=25)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            page_text = soup.get_text().lower()
            if any(signal in page_text for signal in BLOCK_SIGNALS):
                wait = (attempt + 1) * random.uniform(8, 15)
                print(f"    [BLOCAGE] Attente {wait:.0f}s (tentative {attempt+1}/{max_retries})", file=sys.stderr)
                time.sleep(wait)
                continue
            return soup
        except requests.exceptions.HTTPError as e:
            code = e.response.status_code
            if code in (429, 503):
                wait = (attempt + 1) * random.uniform(5, 10)
                print(f"    [HTTP {code}] Attente {wait:.0f}s", file=sys.stderr)
                time.sleep(wait)
            else:
                raise
        except requests.exceptions.RequestException as e:
            if attempt < max_retries - 1:
                wait = (attempt + 1) * random.uniform(3, 6)
                print(f"    [RESEAU] {e}. Attente {wait:.0f}s", file=sys.stderr)
                time.sleep(wait)
            else:
                raise
    raise Exception(f"Echec apres {max_retries} tentatives : {url}")


def _tokenize(text):
    if not text:
        return set()
    text = text.lower()
    text = re.sub(r"[^a-zàâçéèêëîïôûùüÿœæ\s]", " ", text)
    stopwords = {
        "pour", "avec", "dans", "sans", "plus", "tout", "tous", "votre",
        "vous", "nous", "leur", "leurs", "comment", "guide", "livre",
        "méthode", "pratique", "simple", "débutant", "être", "faire",
        "avoir", "cette", "celui", "ceux", "comme", "mais", "donc",
        "aussi", "très", "bien", "moins", "même", "autre"
    }
    return {w for w in text.split() if len(w) >= 4 and w not in stopwords}


def angle_score(book_row, angle_tokens):
    title       = str(book_row.get("title", ""))
    description = str(book_row.get("description", ""))
    subtitle    = str(book_row.get("subtitle", ""))
    book_tokens = _tokenize(f"{title} {subtitle} {description}")
    return len(angle_tokens & book_tokens)


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
        for item in soup.select("div[data-hook='review']"):
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
        print(f"      [ERREUR] ASIN {asin}, filtre {star_filter}, p{page}: {e}", file=sys.stderr)
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
            time.sleep(random.uniform(0.8, 1.8))
            if page > 3:
                break
        collected = collected[:max_per_filter]
        all_reviews.extend(collected)
        print(f"        '{star_filter}' : {len(collected)} reviews", file=sys.stderr)
    return all_reviews


def extract_pain_points(reviews_df):
    if reviews_df.empty:
        return ""
    bodies = reviews_df["review_body"].dropna().tolist()
    combined = " | ".join(bodies[:30])
    return re.sub(r"\s+", " ", combined).strip()[:3000]


def process_niches(df_go, df_data):
    all_reviews     = []
    niche_summaries = []

    for _, niche_row in df_go.iterrows():
        category = niche_row["category"]
        print(f"\nNiche : {category}", file=sys.stderr)

        df_books = df_data[df_data["category"] == category].copy()
        if "rank_in_category" in df_books.columns:
            df_books = df_books.sort_values("rank_in_category")

        top_books     = df_books.head(TOP_BOOKS_PER_NICHE)
        niche_reviews = []

        for _, book in top_books.iterrows():
            title       = book.get("title", "")
            product_url = book.get("product_url", "")
            asin        = extract_asin(product_url)
            if not asin:
                continue
            print(f"  Livre : {title[:60]}...", file=sys.stderr)
            reviews = scrape_book_reviews(asin, title)
            for review in reviews:
                review.update({"category": category, "book_title": title, "asin": asin, "product_url": product_url})
            niche_reviews.extend(reviews)
            all_reviews.extend(reviews)
            time.sleep(random.uniform(1.5, 3.0))

        if niche_reviews:
            niche_df    = pd.DataFrame(niche_reviews)
            pain_points = extract_pain_points(niche_df)
            niche_summaries.append({
                "category":            category,
                "kdp_score":           float(niche_row.get("kdp_score", 0)),
                "books_analyzed":      int(top_books.shape[0]),
                "reviews_collected":   len(niche_reviews),
                "avg_negative_rating": round(float(niche_df["rating"].mean()), 2) if "rating" in niche_df.columns else None,
                "pain_points_raw":     pain_points
            })
            print(f"  -> {len(niche_reviews)} reviews collectées", file=sys.stderr)

    return all_reviews, niche_summaries


def process_targeted(category, angle, df_data):
    # 1. Vérifier l'existence de la catégorie et remonter le parent
    match = df_data[df_data["category"] == category]
    if match.empty:
         return {"status": "error", "message": f"Catégorie '{category}' introuvable dans le radar"}

    parent_category = match.iloc[0]["parent_category"]
    print(f"Parent : {parent_category}", file=sys.stderr)

    # 2. Préparer les tokens de l'angle
    angle_tokens = _tokenize(angle)
    print(f"Tokens angle ({len(angle_tokens)}) : {angle_tokens}", file=sys.stderr)

    # 3. Premier essai : Pool restreint à la sous-catégorie demandée uniquement
    target_data = df_data[df_data["category"] == category].copy()
    target_data["_angle_score"] = target_data.apply(
        lambda row: angle_score(row, angle_tokens), axis=1
    )
    
    # Garde dure : Exclusion immédiate de tout score égal à 0
    relevant_books = target_data[target_data["_angle_score"] > 0].copy()
    
    sibling_fallback_used = False
    sibling_cats = []  # Reste vide si pas de fallback (conformément à la documentation)

    # 4. Fallback conditionnel vers les catégories sœurs
    if len(relevant_books) < MIN_RELEVANT_BOOKS:
        print(f"  [FALLBACK] Seulement {len(relevant_books)} livres avec score > 0. Élargissement aux sœurs.", file=sys.stderr)
        sibling_fallback_used = True
        
        sibling_data = df_data[df_data["parent_category"] == parent_category].copy()
        sibling_cats = sibling_data["category"].unique().tolist()
        
        sibling_data["_angle_score"] = sibling_data.apply(
            lambda row: angle_score(row, angle_tokens), axis=1
        )
        # On applique la garde dure également sur le pool élargi
        relevant_books = sibling_data[sibling_data["_angle_score"] > 0].copy()
    else:
        print(f"  [OK] {len(relevant_books)} livres trouvés dans la catégorie d'origine. Pas d'élargissement.", file=sys.stderr)

    # Si aucun livre n'a un score > 0, même après fallback
    if relevant_books.empty:
        return {"status": "error", "message": "Aucun livre pertinent pour cet angle"}

    # Tri par pertinence (score d'abord, puis BSR/rank)
    if "rank_in_category" in relevant_books.columns:
        relevant_books = relevant_books.sort_values(
            ["_angle_score", "rank_in_category"], ascending=[False, True]
        )
    else:
        relevant_books = relevant_books.sort_values("_angle_score", ascending=False)

    top_books = relevant_books.head(MAX_BOOKS_TARGETED)
    print(f"Livres sélectionnés : {len(top_books)} (sur {len(relevant_books)} candidats pertinents)", file=sys.stderr)
    for _, b in top_books.iterrows():
        print(f"  [{b['_angle_score']}pts] [{b['category']}] {str(b['title'])[:60]}", file=sys.stderr)

    # 5. Scraper les reviews
    all_reviews = []
    for _, book in top_books.iterrows():
        title       = book.get("title", "")
        product_url = book.get("product_url", "")
        asin        = extract_asin(product_url)
        if not asin:
            continue
        print(f"\n  Scrape : {title[:60]}...", file=sys.stderr)
        reviews = scrape_book_reviews(asin, title)
        for review in reviews:
            review.update({
                "category":    book.get("category", ""),
                "book_title":  title,
                "asin":        asin,
                "product_url": product_url,
                "angle_score": int(book["_angle_score"])
            })
        all_reviews.extend(reviews)
        time.sleep(random.uniform(1.5, 3.0))

    if not all_reviews:
        return {"status": "error", "message": "Aucune review collectée"}

    reviews_df  = pd.DataFrame(all_reviews)
    pain_points = extract_pain_points(reviews_df)

    by_cat = (
        reviews_df.groupby("category")
        .agg(books=("book_title", "nunique"), reviews=("review_body", "count"))
        .reset_index().to_dict(orient="records")
    )

    return {
        "status":                   "success",
        "mode":                     "targeted",
        "category_requested":       category,
        "parent_category":          parent_category,
        "sibling_categories":       sibling_cats,
        "sibling_fallback_used":    sibling_fallback_used,
        "books_scored":             len(relevant_books),
        "books_scraped":            len(top_books),
        "total_reviews":            len(all_reviews),
        "breakdown_by_subcategory": by_cat,
        "pain_points": [{
            "category":            category,
            "parent_category":     parent_category,
            "angle":               angle,
            "books_analyzed":      len(top_books),
            "reviews_collected":   len(all_reviews),
            "avg_negative_rating": round(float(reviews_df["rating"].mean()), 2) if "rating" in reviews_df.columns else None,
            "pain_points_raw":     pain_points
        }]
    }

def main():
    parser = argparse.ArgumentParser(description="Scrape reviews Amazon par niche")
    parser.add_argument("--category", type=str, default=None,
        help="Sous-catégorie ciblée (ex: \"Santé personnelle\"). Active le mode ciblé.")
    parser.add_argument("--angle", type=str, default=None,
        help="Angle éditorial auteur (angle_propose du Module 2).")
    args = parser.parse_args()

    print(f"Lecture du radar : {RADAR_FILE}", file=sys.stderr)
    df_scores = pd.read_excel(RADAR_FILE, sheet_name="scores")
    df_data   = pd.read_excel(RADAR_FILE, sheet_name="data")

    # MODE CIBLÉ
    if args.category:
        if not args.angle:
            print(json.dumps({"status": "error", "message": "--angle requis en mode ciblé"}, ensure_ascii=False))
            sys.exit(1)
        print(f"Mode ciblé : category='{args.category}'", file=sys.stderr)
        result = process_targeted(args.category, args.angle, df_data)
        print(json.dumps(result, ensure_ascii=False))
        return

    # MODE BATCH (original)
    df_go = df_scores[df_scores["kdp_score"] >= MIN_KDP_SCORE].copy()
    print(f"Niches retenues (score >= {MIN_KDP_SCORE}) : {len(df_go)}", file=sys.stderr)
    all_reviews, niche_summaries = process_niches(df_go, df_data)

    if not all_reviews:
        print("\nAucune review récupérée.")
        return

    df_reviews   = pd.DataFrame(all_reviews)
    df_summaries = pd.DataFrame(niche_summaries)
    review_cols  = ["category", "book_title", "asin", "product_url",
                    "rating", "review_title", "review_body", "review_date"]
    review_cols  = [c for c in review_cols if c in df_reviews.columns]
    df_reviews   = df_reviews[review_cols]

    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        df_summaries.to_excel(writer, sheet_name="pain_points", index=False)
        df_reviews.to_excel(writer, sheet_name="reviews_detail", index=False)

    print(f"\nTermine : {OUTPUT_FILE}")
    print(f"   Niches analysées    : {len(df_summaries)}")
    print(f"   Reviews collectées  : {len(df_reviews)}")


if __name__ == "__main__":
    main()