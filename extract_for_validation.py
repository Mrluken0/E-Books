import sys
import json
import argparse
import subprocess

# --- GESTION DES DÉPENDANCES ---
try:
    import docx
except ImportError:
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "python-docx", "--break-system-packages", "-q"])
        import docx
    except Exception as e:
        sys.stderr.write(f"Erreur d'installation de python-docx: {str(e)}\n")
        print(json.dumps({"status": "error", "message": f"Dependency error: {str(e)}"}, ensure_ascii=False))
        sys.exit(1)

# --- CONFIGURATION ENCODAGE ---
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')


def normal_texts_after(paragraphs, start_index, count):
    """Retourne le texte des `count` premiers paragraphes Normal non vides après start_index."""
    extraits = []
    for p in paragraphs[start_index + 1:]:
        if p["style"] == "Normal" and p["text"]:
            extraits.append(p["text"])
            if len(extraits) >= count:
                break
    return "\n".join(extraits)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=False, help='Chemin vers book_config.json')
    parser.add_argument('--docx', required=False, help='Chemin direct vers le .docx')
    args = parser.parse_args()

    if args.docx:
        docx_path = args.docx
    elif args.config:
        with open(args.config, 'r', encoding='utf-8') as f:
            config = json.load(f)
        docx_path = config['output_path']
    else:
        print(json.dumps({"status": "error", "message": "Fournir --config ou --docx"}, ensure_ascii=False))
        sys.exit(1)

    try:
        # 1. Lecture du document
        doc = docx.Document(docx_path)

        # Indexation des paragraphes (style + texte)
        paragraphs = []
        for p in doc.paragraphs:
            paragraphs.append({"style": p.style.name, "text": p.text.strip()})

        # 2. Extraction structurée
        titres_h1 = [p["text"] for p in paragraphs if p["style"] == "Heading 1" and p["text"]]
        titres_h2 = [p["text"] for p in paragraphs if p["style"] == "Heading 2" and p["text"]]

        # Positions des H1 dans la liste complète
        h1_positions = [i for i, p in enumerate(paragraphs) if p["style"] == "Heading 1" and p["text"]]

        # extrait_debut : 3 premiers paragraphes Normal après le premier H1
        extrait_debut = ""
        if h1_positions:
            extrait_debut = normal_texts_after(paragraphs, h1_positions[0], 3)

        # extrait_milieu : 3 premiers paragraphes Normal après le H1 du milieu
        extrait_milieu = ""
        if h1_positions:
            mid_h1 = h1_positions[len(h1_positions) // 2]
            extrait_milieu = normal_texts_after(paragraphs, mid_h1, 3)

        # extrait_fin : 3 derniers paragraphes Normal non vides avant la fin
        normal_texts = [p["text"] for p in paragraphs if p["style"] == "Normal" and p["text"]]
        extrait_fin = "\n".join(normal_texts[-3:]) if normal_texts else ""

        # 3. Détection des anomalies brutes dans TOUT le texte Normal
        all_normal = "\n".join(normal_texts)
        patterns = ["**", "<br>", "|---|", "##"]
        anomalies_detectees = [pat for pat in patterns if pat in all_normal]

        # Output JSON unique
        result = {
            "status": "ok",
            "nb_headings_h1": len(titres_h1),
            "titres_h1": titres_h1,
            "titres_h2": titres_h2,
            "extrait_debut": extrait_debut,
            "extrait_milieu": extrait_milieu,
            "extrait_fin": extrait_fin,
            "anomalies_detectees": anomalies_detectees,
        }
        print(json.dumps(result, ensure_ascii=False))
        sys.exit(0)

    except Exception as e:
        sys.stderr.write(f"Erreur: {str(e)}\n")
        print(json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
