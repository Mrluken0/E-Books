# Référence catégories KDP (nodeId) — fr_FR, ebook

Vérifié en live le 2026-07-06 sur `kdp.amazon.com/fr_FR` (formulaire /details).

Le format de `config["categories"]` utilise des **nodeId** (identifiants Amazon,
indépendants de la langue et de l'orthographe) :

```json
"categories": [
  { "rubrique": "156563011", "classement": "202437600011",
    "libelle": "Développement personnel > Success" }
]
```
- `rubrique`   = nodeId de la rubrique de **niveau 0** (voir table ci-dessous)
- `classement` = nodeId de la **feuille** (case à cocher ; libellé en anglais)
- `libelle`    = facultatif, purement informatif (logs)
- Maximum **3** entrées. Pas de préfixe « Kindle Store / Kindle eBooks ».

---

## Rubriques niveau 0 (nom FR → nodeId)

| Rubrique | nodeId |
|---|---|
| Adolescents | 3511261011 |
| Arts et photographie | 154607011 |
| Bandes dessinées | 156104011 |
| Biographies et mémoires | 154754011 |
| Cuisine et vins | 156154011 |
| Développement personnel | 156563011 |
| Droit | 156915011 |
| Ebooks en langues étrangères | 7735160011 |
| Éducation des enfants et relations | 157584011 |
| Éducation et références | 158125011 |
| Entreprise et Bourse | 154821011 |
| Histoire | 156576011 |
| Humour | 156279011 |
| Informatique et Internet | 156116011 |
| Ingénierie et transport | 157626011 |
| LGBTQ2S+ | 156424011 |
| Littérature | 157028011 |
| Livres pour enfants | 155009011 |
| Loisirs créatifs, décoration et bricolage | 156699011 |
| Médecine | 157119011 |
| Ouvrages documentaires | 157325011 |
| Policier et Suspense | 157305011 |
| Politique et sciences sociales | 305951011 |
| Référence | 9154158011 |
| Religions et Spiritualités | 158280011 |
| Romance | 158566011 |
| Santé et Bien-être | 156430011 |
| Science et mathématiques | 158597011 |
| Science-fiction et Fantasy | 668010011 |
| Sports et extérieurs | 159818011 |
| Tourisme et Voyages | 159936011 |
| Inclassable | NON_CLASSIFIABLE |

---

## Feuilles de classement (partiel) — Développement personnel (156563011)

Libellés **en anglais** dans le sélecteur KDP.

| Classement | nodeId |
|---|---|
| Green Lifestyle | 202437602011 |
| Self-Management | 202437601011 |
| Success | 202437600011 |
| Communication & Social Skills | 202437599011 |
| Time Management | 202437598011 |
| Aging | 202437597011 |
| Fashion & Style | 202437596011 |
| Self-Hypnosis | 202437595011 |
| Neuro-Linguistic Programming (NLP) | 202437594011 |
| Anger Management | 202437593011 |
| Anxieties & Phobias | 202437592011 |
| Affirmations | 202437591011 |

> Liste non exhaustive (29 feuilles au total sous cette rubrique). Pour extraire
> la liste complète d'une rubrique, voir le snippet ci-dessous.

---

## Snippet console — extraire les feuilles d'une rubrique

Sur la page /details, contenu adulte répondu, modal des rubriques ouverte :
sélectionne la rubrique voulue dans le 1er menu déroulant, puis colle ceci
(F12 → Console) pour obtenir `nom → nodeId` de toutes ses feuilles :

```js
JSON.stringify(
  [...document.querySelectorAll('input[type=checkbox]')]
    .filter(c => c.offsetParent !== null)
    .map(c => {
      const cls = [...c.classList].find(x => x.startsWith('checkbox-')) || '';
      return [ (c.closest('label')?.innerText || '').trim(), cls.replace('checkbox-','') ];
    })
);
```
