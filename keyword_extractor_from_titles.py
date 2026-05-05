import sys
import json
import re
import time
from pathlib import Path
from datetime import datetime
from collections import Counter
from itertools import combinations

import requests
import pandas as pd
from bs4 import BeautifulSoup

sys.stdout.reconfigure(encoding="utf-8")


BASE_DIR = Path(r"C:\Users\luken\Desktop\LKN Digital\Automation\KDP-Automation")
OUTPUT_DIR = BASE_DIR / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8"
}

STOPWORDS = {
    "les", "des", "une", "pour", "avec", "dans", "sur", "sans", "plus", "tout",
    "tous", "toutes", "votre", "vous", "nous", "leur", "leurs", "son", "ses",
    "aux", "du", "de", "la", "le", "un", "en", "et", "ou", "au", "ce", "ces",
    "comment", "guide", "livre", "méthode", "édition", "poche", "nouvelle",
    "pratique", "manuel", "simple", "débutant", "débutants", "pas", "est",
    "petit", "frère", "mots", "nuls", "personnels", "registre", "main",
    "apprend", "apprendre", "devenir", "découvrir", "complet", "maîtriser",
    "étapes", "formation", "top", "cahier", "comptes", "être", "faire", "atteindre",
    "libre", "financière"
}


def clean_text(text):
    if not text:
        return ""
    text = str(text).lower()
    text = re.sub(r"[^a-zàâçéèêëîïôûùüÿñæœ0-9\s-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def tokenize(text):
    text = clean_text(text)
    words = []

    for word in text.split():
        word = word.strip("-")
        if len(word) < 3:
            continue
        if word in STOPWORDS:
            continue
        if word.isdigit():
            continue
        words.append(normalize_keyword(word))

    return words

def normalize_keyword(word):
    return clean_text(word).lower().strip()

def get_soup(url):
    response = requests.get(url, headers=HEADERS, timeout=20)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def extract_titles_from_category(url, max_books=20):
    soup = get_soup(url)

    items = soup.select("div.zg-grid-general-faceout")
    if not items:
        items = soup.select("div[id^='gridItemRoot']")

    titles = []

    for item in items[:max_books]:
        img = item.select_one("img")
        title = ""

        if img and img.get("alt"):
            title = img.get("alt").strip()

        if not title:
            title_el = (
                item.select_one("div._cDEzb_p13n-sc-css-line-clamp-1_1Fn1y")
                or item.select_one("div._cDEzb_p13n-sc-css-line-clamp-2_EWgCb")
                or item.select_one("span.a-size-medium")
            )
            if title_el:
                title = title_el.get_text(strip=True)

        if title:
            titles.append(title)

    return titles


def main():
    if len(sys.argv) < 2:
        raise ValueError("Données catégorie/auteur manquantes.")

    item = json.loads(sys.argv[1])

    category = item.get("category", "")
    url = item.get("url", "")

    if not category or not url:
        raise ValueError("La catégorie ou l'URL est manquante.")

    titles = extract_titles_from_category(url, max_books=20)
    time.sleep(0.5)

    all_words = []
    for title in titles:
        all_words.extend(tokenize(title))

    counter = Counter(all_words)

    keyword_rows = []
    niche_rows = []

    for keyword, frequency in counter.most_common(50):
        keyword_rows.append({
            "category": category,
            "keyword": keyword,
            "frequency": frequency,
            "keyword_score": frequency * 10
        })

    top_words = [word for word, freq in counter.most_common(15)]

    seen_pairs = set()

    for combo in combinations(top_words, 2):
        k1 = normalize_keyword(combo[0])
        k2 = normalize_keyword(combo[1])

        if k1 == k2:
            continue

        pair_key = tuple(sorted([k1, k2]))

        if pair_key in seen_pairs:
            continue

        seen_pairs.add(pair_key)

        niche_score = (counter[k1] + counter[k2]) * 10

        niche_rows.append({
            **item,
            "category": category,
            "niche_raw": f"{pair_key[0]} + {pair_key[1]}",
            "keyword_1": pair_key[0],
            "keyword_2": pair_key[1],
            "niche_score": niche_score
        })

    niches_df = pd.DataFrame(niche_rows)
    keywords_df = pd.DataFrame(keyword_rows)
    titles_df = pd.DataFrame({"category": category, "title": titles})

    if not niches_df.empty:
        niches_df = niches_df.sort_values("niche_score", ascending=False)

    author_name = item.get("author_name", "Auteur").replace(" ", "_")
    safe_category = re.sub(r"[^a-zA-Z0-9_-]", "_", category)

    date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = OUTPUT_DIR / f"keywords_{author_name}_{safe_category}_{date_str}.xlsx"

    with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
        titles_df.to_excel(writer, sheet_name="titles", index=False)
        keywords_df.to_excel(writer, sheet_name="keywords", index=False)
        niches_df.to_excel(writer, sheet_name="niches", index=False)

    if not niches_df.empty:
        niches_df = niches_df[niches_df["niche_score"] >= 80]
        niches_df = niches_df.sort_values("niche_score", ascending=False)

    records = niches_df.to_dict(orient="records")

    result = {
        "status": "success",
        "author_name": item.get("author_name", ""),
        "category": category,
        "excel_file": str(output_file),
        "items_count": len(records),
        "items": records
    }

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
