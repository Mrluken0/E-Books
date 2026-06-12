from bs4 import BeautifulSoup
html = open("debug_dp.html", encoding="utf-8").read()
soup = BeautifulSoup(html, "html.parser")
first = soup.select("div[data-hook='review']")[0]

# Le body est plus bas — on imprime du char 2000 à 4000
print("--- Suite du bloc (chars 2000-4200) ---")
print(first.prettify()[2000:4200])

print("\n--- Test sélecteurs body candidats ---")
candidats = {
    "data-hook review-body":        "span[data-hook='review-body']",
    "data-hook reviewBody":         "span[data-hook='reviewBody']",
    "div data-hook review-body":    "div[data-hook='review-body']",
    "review-collapsed":             "div[data-hook='review-collapsed']",
    "classe _Y3Itd body (regex)":   "[class*='review-body'], [class*='single-review-body'], [class*='reviewText']",
}
for label, sel in candidats.items():
    el = first.select_one(sel)
    txt = el.get_text(" ", strip=True)[:90] if el else None
    print(f"  {'OK ' if el else 'KO '} {label}: {txt!r}")

# Titre confirmé
h5 = first.select_one("h5[data-hook='reviewTitle']")
print(f"\n  titre h5[reviewTitle]: {h5.get_text(' ', strip=True)[:80]!r}" if h5 else "  KO titre")