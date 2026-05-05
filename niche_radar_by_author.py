import sys
import json
import math
import requests
import pandas as pd
from pathlib import Path
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = Path(r"C:\Users\luken\Desktop\LKN Digital\Automation\KDP-Automation")
RADAR_FILE = BASE_DIR / "radar_kdp_clean.xlsx"
OUTPUT_DIR = BASE_DIR / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)

OLLAMA_URL = "http://127.0.0.1:11434/api/embeddings"
EMBED_MODEL = "nomic-embed-text"


def split_csv(text):
    if not text:
        return []
    return [x.strip().lower() for x in str(text).split(",") if x.strip()]


def embed(text):
    response = requests.post(
        OLLAMA_URL,
        json={"model": EMBED_MODEL, "prompt": text},
        timeout=60
    )
    response.raise_for_status()
    return response.json()["embedding"]


def cosine_similarity(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))

    if norm_a == 0 or norm_b == 0:
        return 0

    return dot / (norm_a * norm_b)


def main():
    if len(sys.argv) < 2:
        raise ValueError("Profil auteur manquant.")

    author = json.loads(sys.argv[1])

    author_name = author.get("Auteur_nom", "Auteur")
    excluded_categories = split_csv(author.get("Catégories exclues", ""))

    df_scores = pd.read_excel(RADAR_FILE, sheet_name="scores")
    df_data = pd.read_excel(RADAR_FILE, sheet_name="data")

    author_text = f"""
    Auteur : {author_name}
    Sexe : {author.get("Sexe", "")}
    Positionnement : {author.get("Positionnement", "")}
    Personnalité : {author.get("Personnalité", "")}
    Style d'écriture : {author.get("Style d'écriture", "")}
    Promesse : {author.get("Promesse", "")}
    Mots-clés principaux : {author.get("Mots-clés principaux", "")}
    Mots-clés secondaires : {author.get("Mots-clés secondaires", "")}
    Catégories autorisées : {author.get("Catégories autorisées", "")}
    Catégories exclues : {author.get("Catégories exclues", "")}
    Ce qu'il n'est pas : {author.get("Ce qu'il n'est pas", "")}
    Type de livre : {author.get("Type de livre", "")}
    """

    author_vec = embed(author_text)

    rows = []

    for _, row in df_scores.iterrows():
        category = str(row.get("category", ""))
        category_lower = category.lower()

        if any(excluded in category_lower for excluded in excluded_categories):
            continue

        titles = (
            df_data[df_data["category"] == category]["title"]
            .dropna()
            .astype(str)
            .head(15)
            .tolist()
        )

        titles_text = " | ".join(titles)

        category_text = f"""
        Catégorie Amazon : {category}
        Titres concurrents représentatifs : {titles_text}
        Prix moyen : {row.get("avg_price", 0)}
        Score KDP : {row.get("kdp_score", 0)}
        """

        category_vec = embed(category_text)
        semantic_score = cosine_similarity(author_vec, category_vec)

        kdp_score = float(row.get("kdp_score", 0))
        author_fit_score = round((semantic_score * 100) + (kdp_score * 0.3), 2)

        item = row.to_dict()

        item.update({
            "author_id": author.get("Auteur_id", ""),
            "author_name": author_name,
            "author_sex": author.get("Sexe", ""),
            "author_positioning": author.get("Positionnement", ""),
            "author_personality": author.get("Personnalité", ""),
            "author_style": author.get("Style d'écriture", ""),
            "author_promise": author.get("Promesse", ""),
            "author_forbidden": author.get("Ce qu'il n'est pas", ""),
            "author_book_types": author.get("Type de livre", ""),
            "semantic_score": round(semantic_score, 4),
            "author_fit_score": author_fit_score,
            "competitor_titles": titles_text
        })

        rows.append(item)

    result_df = pd.DataFrame(rows)

    if not result_df.empty:
        result_df = result_df.sort_values("author_fit_score", ascending=False).head(8)

    date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_author = author_name.replace(" ", "_")
    output_file = OUTPUT_DIR / f"semantic_categories_{safe_author}_{date_str}.xlsx"

    result_df.to_excel(output_file, index=False)

    result = {
        "status": "success",
        "author_name": author_name,
        "excel_file": str(output_file),
        "items_count": len(result_df),
        "items": result_df.to_dict(orient="records")
    }

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
