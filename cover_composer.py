# -*- coding: utf-8 -*-
"""
cover_composer.py — Compose titre + sous-titre + auteur sur un artwork nu (Pillow).

Remplace le studio Cover Creator KDP (Mode 2, instable) pour le pipeline :
l'artwork Imagen (sans texte, ~1536x2816) est composé ici, puis le fichier
produit est passé à book_publisher.use_cover_creator Mode 1 (upload direct).

Deux sources pilotent le rendu :
  1. Couleur du texte : luminance BT.709 des PIXELS REELS sous chaque bloc
     (titre / sous-titre / auteur, mesurés independamment) -> texte foncé sur
     fond clair, texte clair sur fond sombre. Halo doux auto si la zone est
     "chargée" (forte variance) pour garantir la lisibilité.
  2. Police : mappée depuis les champs Notion "Cover Style" + "Cover Visual
     Mood" par scoring pondéré (voir FONT_KEYWORDS / score_family).

Sortie NON destructive : ecrit cover_<titre>_composed.jpg a cote de l'artwork,
l'artwork nu reste intact. Retourne le chemin du fichier composé.
"""
import os
import sys
import glob
import json
import argparse
import unicodedata
import urllib.request
import urllib.error
from PIL import Image, ImageDraw, ImageFont, ImageStat, ImageFilter

# --- ENCODAGE (même convention que extract_for_validation.py / Prompt_sender.py) ---
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


def log(*args):
    """Logs de diagnostic -> stderr (stdout reste réservé au JSON de résultat)."""
    print(*args, file=sys.stderr, flush=True)

# --------------------------------------------------------------------------- #
#  Polices — cache local persistant + téléchargement paresseux depuis GitHub.
#
#  En prod, le script est exécuté depuis un fichier temporaire téléchargé (les
#  assets ne sont donc PAS à côté de __file__). On résout chaque .ttf via un
#  cache local persistant (jamais supprimé auto), en ne téléchargeant QUE les
#  familles réellement utilisées par le run (voir load_font/_font_file, appelés
#  uniquement pour la famille choisie + DancingScript si script_author).
# --------------------------------------------------------------------------- #
# Cache overridable par env ; défaut hors du dossier temporaire du script.
FONT_DIR = os.environ.get("COVER_COMPOSER_FONT_DIR", r"C:/LKN_Digital/KDP/assets/fonts")

_GH_REPO = "Mrluken0/E-Books"
_GH_CONTENTS = "https://api.github.com/repos/{repo}/contents/assets/fonts/{folder}"
_GH_RAW = "https://raw.githubusercontent.com/{repo}/main/assets/fonts/{folder}/{name}"
_HTTP_HEADERS = {"User-Agent": "cover-composer/1.0"}


def _cached_ttf(family_folder):
    """Retourne le .ttf en cache local pour cette famille, ou None."""
    hits = glob.glob(os.path.join(FONT_DIR, family_folder, "*.ttf"))
    return hits[0] if hits else None


def _download_font(family_folder):
    """Télécharge le 1er .ttf de assets/fonts/<family_folder>/ (GitHub) vers le cache.

    Toute défaillance réseau/404/rate-limit est convertie en RuntimeError explicite
    (capturée par le try/except de main() -> JSON status:error).
    """
    url = _GH_CONTENTS.format(repo=_GH_REPO, folder=family_folder)
    try:
        req = urllib.request.Request(url, headers=_HTTP_HEADERS)
        with urllib.request.urlopen(req, timeout=30) as r:
            listing = json.load(r)
    except urllib.error.HTTPError as e:
        raison = "rate-limit GitHub" if e.code in (403, 429) else \
                 f"famille absente du repo (HTTP 404)" if e.code == 404 else f"HTTP {e.code}"
        raise RuntimeError(f"Téléchargement police {family_folder} échoué: {raison}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Téléchargement police {family_folder} échoué: réseau ({e.reason})")
    except Exception as e:
        raise RuntimeError(f"Téléchargement police {family_folder} échoué: {e}")

    if not isinstance(listing, list):
        raise RuntimeError(
            f"Téléchargement police {family_folder} échoué: réponse GitHub inattendue")
    ttfs = [x for x in listing
            if isinstance(x, dict) and str(x.get("name", "")).lower().endswith(".ttf")]
    if not ttfs:
        raise RuntimeError(
            f"Téléchargement police {family_folder} échoué: aucun .ttf dans assets/fonts/{family_folder}")

    entry = ttfs[0]
    name = entry["name"]
    dl_url = entry.get("download_url") or _GH_RAW.format(repo=_GH_REPO, folder=family_folder, name=name)

    log(f"  [font] téléchargement {family_folder}/{name} ...")
    try:
        req = urllib.request.Request(dl_url, headers=_HTTP_HEADERS)
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
    except Exception as e:
        raise RuntimeError(f"Téléchargement police {family_folder} échoué: {e}")

    dest_dir = os.path.join(FONT_DIR, family_folder)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, name)
    tmp = dest + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, dest)   # écriture atomique -> pas de .ttf tronqué en cache
    log(f"  [font] mis en cache : {dest} ({len(data) // 1024} Ko)")
    return dest


def _font_file(family_folder):
    """Résout le .ttf d'une famille : cache local d'abord, sinon GitHub (paresseux)."""
    cached = _cached_ttf(family_folder)
    if cached:
        log(f"  [font] cache hit : {family_folder}")
        return cached
    return _download_font(family_folder)


# nom logique -> dossier
FONT_FILES = {
    "Montserrat": "Montserrat",
    "Raleway": "Raleway",
    "WorkSans": "WorkSans",
    "Lora": "Lora",
    "PlayfairDisplay": "PlayfairDisplay",
    "Merriweather": "Merriweather",
    "Nunito": "Nunito",
    "Quicksand": "Quicksand",
    "CormorantGaramond": "CormorantGaramond",
    "DancingScript": "DancingScript",
}

# Graisse (axe wght) par bloc, par famille. 400=Regular 500=Medium 600=SemiBold 700=Bold
TITLE_WEIGHT = {"Montserrat": 700}          # défaut 600 sinon
DEFAULT_TITLE_WEIGHT = 600
SUBTITLE_WEIGHT = 400
AUTHOR_WEIGHT = 500
SCRIPT_AUTHOR_WEIGHT = 600                   # DancingScript

# --------------------------------------------------------------------------- #
#  Mapping mots-clés -> police (scoring pondéré)
# --------------------------------------------------------------------------- #
# Chaque mot-clé est normalisé (minuscules, sans accents). La recherche se fait
# par sous-chaîne dans (Style + Mood), donc les expressions multi-mots marchent.
FONT_KEYWORDS = {
    "Montserrat": [
        "impactant", "fort", "forte", "audacieux", "affirme", "dynamique",
        "energique", "punchy", "moderne", "direct", "efficace", "sans fioritures",
        "percutant", "concret", "sans detour", "performance", "ambition", "motivant",
    ],
    "Raleway": [
        "minimaliste", "epure", "sobre", "clean", "graphique", "contemporain", "ligne",
    ],
    "Quicksand": [
        "arrondi", "leger", "aerien", "apaisant", "zen", "doux", "douce",
        "rond", "ronde", "souple", "moelleux",
    ],
    "Lora": [
        "chaleureux", "chaleureuse", "reconfortant", "naturel", "authentique",
        "bienveillant", "humain", "sincere",
    ],
    "Nunito": [
        "rassurant", "accessible", "amical", "friendly", "cocon", "ludique",
        "joyeux", "familial", "famille", "enfant", "tendre", "complice", "complicite",
    ],
    "PlayfairDisplay": [
        "elegant", "raffine", "haut de gamme", "premium", "chic", "sophistique",
        "prestige", "inspirant",
    ],
    "CormorantGaramond": [
        "luxe", "delicat", "feminin", "poetique", "spirituel", "sacre", "intime",
        "romantique", "sentimental", "amour", "relation", "coeur", "passion", "ethere",
    ],
    "Merriweather": [
        "classique", "professionnel", "serieux", "expert", "credible",
        "institutionnel", "rigoureux", "factuel", "academique", "reference",
    ],
    "WorkSans": [
        "neutre", "clair", "pratique", "pedagogique", "informatif",
    ],
}

# Bonus "double axe" Quicksand : couvre à la fois l'épuré ET la douceur.
_EPURE_FAMILY = ("minimaliste", "epure", "sobre", "clean", "graphique")
_DOUCEUR_FAMILY = ("doux", "douce", "apaisant", "tendre", "rond", "ronde",
                   "leger", "zen", "reconfortant", "moelleux")

# Ordre de priorité en cas d'égalité de score : les arrondies/chaleureuses
# passent avant les serifs, avant les sans froids.
TIE_PRIORITY = ["Quicksand", "Nunito", "Lora", "CormorantGaramond",
                "PlayfairDisplay", "Montserrat", "Raleway", "Merriweather", "WorkSans"]

DEFAULT_FAMILY = "Lora"   # défaut global (sûr pour non-fiction bien-être)

# Mood déclenchant l'accent auteur scripté (DancingScript)
_SCRIPT_TRIGGERS = ("elegant", "raffine", "chaleureux", "chaleureuse", "doux",
                    "douce", "feminin", "romantique", "sentimental", "poetique",
                    "delicat", "intime", "chic", "premium", "luxe")


def _norm(s):
    """Minuscules + suppression des accents."""
    s = (s or "").lower()
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


def score_family(cover_style, cover_mood, verbose=False):
    """Retourne (famille, detail_scores). Mood pondéré x1.5."""
    style_n = _norm(cover_style)
    mood_n = _norm(cover_mood)
    both = style_n + " || " + mood_n

    scores = {fam: 0.0 for fam in FONT_KEYWORDS}
    for fam, kws in FONT_KEYWORDS.items():
        for kw in kws:
            if kw in style_n:
                scores[fam] += 1.0
            if kw in mood_n:
                scores[fam] += 1.5

    # Bonus double-axe Quicksand : épuré ET doux présents simultanément.
    has_epure = any(k in both for k in _EPURE_FAMILY)
    has_douceur = any(k in both for k in _DOUCEUR_FAMILY)
    if has_epure and has_douceur:
        scores["Quicksand"] += 2.0

    top = max(scores.values())
    if top == 0.0:
        chosen = DEFAULT_FAMILY
    else:
        winners = [f for f, s in scores.items() if s == top]
        chosen = min(winners, key=lambda f: TIE_PRIORITY.index(f))

    if verbose:
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        log(f"  Style={cover_style!r}")
        log(f"  Mood ={cover_mood!r}")
        for f, s in ranked:
            if s > 0:
                log(f"    {f:20s} {s}")
        log(f"  -> {chosen}")
    return chosen, scores


def use_script_author(cover_mood):
    return any(t in _norm(cover_mood) for t in _SCRIPT_TRIGGERS)


# --------------------------------------------------------------------------- #
#  Chargement police variable + graisse
# --------------------------------------------------------------------------- #
def load_font(family, size, weight):
    """Charge un TTF variable et fixe l'axe wght (les autres axes -> défaut)."""
    font = ImageFont.truetype(_font_file(FONT_FILES[family]), size)
    try:
        axes = font.get_variation_axes()
    except Exception:
        return font  # pas variable -> tel quel
    if not axes:
        return font
    values = []
    for ax in axes:
        name = ax.get("name", b"")
        if isinstance(name, bytes):
            name = name.decode("latin-1", "ignore")
        if name.lower() == "weight":
            w = max(ax["minimum"], min(ax["maximum"], weight))
            values.append(w)
        else:
            values.append(ax["default"])
    try:
        font.set_variation_by_axes(values)
    except Exception:
        pass
    return font


# --------------------------------------------------------------------------- #
#  Mise en page texte (wrap + auto-fit)
# --------------------------------------------------------------------------- #
def _wrap(draw, text, font, max_w):
    lines, cur = [], ""
    for word in text.split():
        trial = (cur + " " + word).strip()
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _fit(draw, text, family, weight, target, min_size, max_w, max_lines):
    """Réduit la taille jusqu'à ce que le texte tienne (largeur + nb de lignes)."""
    size = target
    while size >= min_size:
        font = load_font(family, size, weight)
        lines = _wrap(draw, text, font, max_w)
        widest = max(draw.textlength(l, font=font) for l in lines)
        if widest <= max_w and len(lines) <= max_lines:
            return font, lines
        size -= 4
    font = load_font(family, min_size, weight)
    return font, _wrap(draw, text, font, max_w)


def _block_bbox(draw, lines, font, x_center, top):
    asc, desc = font.getmetrics()
    line_h = int((asc + desc) * 1.15)
    widest = max(draw.textlength(l, font=font) for l in lines)
    height = line_h * len(lines)
    x0 = int(x_center - widest / 2)
    x1 = int(x_center + widest / 2)
    return (x0, top, x1, top + height), line_h


# --------------------------------------------------------------------------- #
#  Décision couleur + halo (luminance BT.709 des pixels réels)
# --------------------------------------------------------------------------- #
def _decide_color(rgb_src, bbox, halo_std_threshold=0.12):
    W, H = rgb_src.size
    x0, y0, x1, y1 = bbox
    x0 = max(0, min(W - 1, x0)); x1 = max(x0 + 1, min(W, x1))
    y0 = max(0, min(H - 1, y0)); y1 = max(y0 + 1, min(H, y1))
    crop = rgb_src.crop((x0, y0, x1, y1))
    stat = ImageStat.Stat(crop)
    r, g, b = stat.mean[:3]
    lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0
    rs, gs, bs = stat.stddev[:3]
    std = (0.2126 * rs + 0.7152 * gs + 0.0722 * bs) / 255.0
    if lum > 0.5:
        text, halo = (26, 26, 26), (247, 247, 247)
    else:
        text, halo = (247, 247, 247), (26, 26, 26)
    return text, halo, (std > halo_std_threshold), lum, std


def _draw_block(base_rgba, lines, font, x_center, top, line_h,
                color, halo, use_halo):
    """Dessine un bloc centré, avec halo doux optionnel derrière le texte."""
    if use_halo:
        layer = Image.new("RGBA", base_rgba.size, (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        for i, line in enumerate(lines):
            w = ld.textlength(line, font=font)
            x = x_center - w / 2
            y = top + i * line_h
            ld.text((x, y), line, font=font, fill=halo + (210,))
        radius = max(4, int(font.size * 0.07))
        layer = layer.filter(ImageFilter.GaussianBlur(radius))
        base_rgba.alpha_composite(layer)

    d = ImageDraw.Draw(base_rgba)
    for i, line in enumerate(lines):
        w = d.textlength(line, font=font)
        x = x_center - w / 2
        y = top + i * line_h
        d.text((x, y), line, font=font, fill=color + (255,))


# --------------------------------------------------------------------------- #
#  Composition
# --------------------------------------------------------------------------- #
def compose_cover(artwork_path, title, subtitle, author,
                  cover_style="", cover_mood="",
                  out_path=None, author_script="auto", verbose=True):
    """
    Compose la couverture et écrit un NOUVEAU fichier (non destructif).
    Retourne un dict : {cover_path, family, script_author, zones:[...]}.
    """
    artwork_path = os.path.abspath(artwork_path)
    if not os.path.exists(artwork_path):
        raise FileNotFoundError(f"Artwork introuvable : {artwork_path}")

    if out_path is None:
        d = os.path.dirname(artwork_path)
        base = os.path.splitext(os.path.basename(artwork_path))[0]
        out_path = os.path.join(d, f"{base}_composed.jpg")

    family, scores = score_family(cover_style, cover_mood, verbose=verbose)
    if author_script == "auto":
        script_author = use_script_author(cover_mood)
    else:
        script_author = bool(author_script)

    img = Image.open(artwork_path).convert("RGBA")
    rgb_src = img.convert("RGB")          # source pristine pour la luminance
    W, H = img.size
    draw = ImageDraw.Draw(img)

    margin_x = int(W * 0.08)
    max_w = W - 2 * margin_x
    cx = W // 2

    title_w = TITLE_WEIGHT.get(family, DEFAULT_TITLE_WEIGHT)

    # --- TITRE (haut) ---
    t_font, t_lines = _fit(draw, title, family, title_w,
                           target=int(W * 0.088), min_size=int(W * 0.050),
                           max_w=max_w, max_lines=4)
    t_top = int(H * 0.055)
    t_bbox, t_lh = _block_bbox(draw, t_lines, t_font, cx, t_top)

    # --- SOUS-TITRE (sous le titre) — sauté si vide/None ---
    has_subtitle = bool(subtitle and subtitle.strip())
    if has_subtitle:
        s_font, s_lines = _fit(draw, subtitle, family, SUBTITLE_WEIGHT,
                               target=int(W * 0.040), min_size=int(W * 0.028),
                               max_w=int(max_w * 0.92), max_lines=4)
        s_top = t_bbox[3] + int(H * 0.018)
        s_bbox, s_lh = _block_bbox(draw, s_lines, s_font, cx, s_top)

    # --- AUTEUR (bas) ---
    if script_author:
        a_family, a_weight, a_target = "DancingScript", SCRIPT_AUTHOR_WEIGHT, int(W * 0.062)
    else:
        a_family, a_weight, a_target = family, AUTHOR_WEIGHT, int(W * 0.048)
    a_font, a_lines = _fit(draw, author, a_family, a_weight,
                           target=a_target, min_size=int(W * 0.032),
                           max_w=max_w, max_lines=2)
    _, a_lh = _block_bbox(draw, a_lines, a_font, cx, 0)
    a_height = a_lh * len(a_lines)
    a_top = int(H * 0.95) - a_height
    a_bbox, a_lh = _block_bbox(draw, a_lines, a_font, cx, a_top)

    # --- Couleur + halo par bloc (pixels réels) ---
    blocks = [("titre", t_lines, t_font, t_top, t_lh, t_bbox)]
    if has_subtitle:
        blocks.append(("sous-titre", s_lines, s_font, s_top, s_lh, s_bbox))
    blocks.append(("auteur", a_lines, a_font, a_top, a_lh, a_bbox))
    zones = []
    for name, lines, font, top, lh, bbox in blocks:
        color, halo, use_halo, lum, std = _decide_color(rgb_src, bbox)
        kind = "foncé" if color[0] < 128 else "clair"
        if verbose:
            log(f"  [{name:11s}] lum={lum:.2f} std={std:.2f} "
                f"-> texte {kind}{' + halo' if use_halo else ''}")
        _draw_block(img, lines, font, cx, top, lh, color, halo, use_halo)
        zones.append({
            "zone": name,
            "luminance": round(lum, 3),
            "std": round(std, 3),
            "text": kind,
            "halo": bool(use_halo),
        })

    final = img.convert("RGB")
    final.save(out_path, "JPEG", quality=95, subsampling=0)
    if verbose:
        log(f"  Police retenue : {family}"
            f"{' + auteur DancingScript' if script_author else ''}")
        log(f"  Ecrit : {out_path}")
    return {
        "cover_path": out_path,
        "family": family,
        "script_author": script_author,
        "zones": zones,
    }


# --------------------------------------------------------------------------- #
#  CLI (même convention que extract_for_validation.py : JSON unique sur stdout,
#  logs sur stderr, status ok/error, exit 0/1)
# --------------------------------------------------------------------------- #
# Valeurs du livre de démonstration (--demo).
_DEMO = {
    "artwork": r"C:/Users/luken/.n8n-files/cover_10 Minutes Pour Retrouver Le Calme.jpg",
    "title": "10 Minutes Pour Retrouver Le Calme",
    "subtitle": "La méthode simple pour réduire ton stress au quotidien, sans thérapie ni jargon",
    "author": "Claire Dumas",
    "style": "Illustration minimaliste et figurative sur fond uni dégradé doux",
    "mood": "épuré, apaisant, chaleureux et rassurant",
}


def main():
    parser = argparse.ArgumentParser(
        description="Compose titre/sous-titre/auteur sur un artwork nu (Pillow)."
    )
    parser.add_argument("--config", help="Chemin vers un JSON contenant les mêmes clés que les flags")
    parser.add_argument("--artwork", help="Chemin de l'artwork nu (jpg/png)")
    parser.add_argument("--title", help="Titre du livre")
    parser.add_argument("--subtitle", default=None, help="Sous-titre (optionnel)")
    parser.add_argument("--author", help="Nom de l'auteur")
    parser.add_argument("--style", default=None, help="Champ Notion 'Cover Style'")
    parser.add_argument("--mood", default=None, help="Champ Notion 'Cover Visual Mood'")
    parser.add_argument("--out", default=None, help="Chemin de sortie (défaut: <artwork>_composed.jpg)")
    parser.add_argument("--author-script", default=None, choices=["auto", "on", "off"],
                        help="Auteur en script Dancing Script (auto=selon le mood)")
    parser.add_argument("--demo", action="store_true", help="Exécute le livre de démonstration")
    args = parser.parse_args()

    # Priorité : --config < flags < --demo (le plus explicite gagne pour chaque clé).
    cfg = {}
    if args.config:
        try:
            with open(args.config, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception as e:
            print(json.dumps({"status": "error", "message": f"Config illisible: {e}"},
                             ensure_ascii=False))
            sys.exit(1)

    def pick(key, arg_val, default=""):
        if args.demo:
            return _DEMO.get(key, default)
        if arg_val is not None:
            return arg_val
        if key in cfg and cfg[key] is not None:
            return cfg[key]
        return default

    artwork = pick("artwork", args.artwork, "")
    title = pick("title", args.title, "")
    subtitle = pick("subtitle", args.subtitle, "")
    author = pick("author", args.author, "")
    style = pick("style", args.style, "")
    mood = pick("mood", args.mood, "")
    out = pick("out", args.out, None) or None
    author_script = pick("author_script", args.author_script, "auto")
    if author_script == "on":
        author_script = True
    elif author_script == "off":
        author_script = False

    missing = [k for k, v in {"artwork": artwork, "title": title, "author": author}.items() if not v]
    if missing:
        print(json.dumps({"status": "error",
                          "message": f"Argument(s) obligatoire(s) manquant(s): {', '.join(missing)}"},
                         ensure_ascii=False))
        sys.exit(1)

    try:
        result = compose_cover(
            artwork_path=artwork, title=title, subtitle=subtitle, author=author,
            cover_style=style, cover_mood=mood, out_path=out,
            author_script=author_script, verbose=True,
        )
        output = {"status": "ok", **result}
        print(json.dumps(output, ensure_ascii=False))
        sys.exit(0)
    except Exception as e:
        log(f"Erreur: {e}")
        print(json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
