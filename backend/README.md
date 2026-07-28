# Floetik — backend

Le corpus de Florentz Charles : poèmes, romans, histoires, actualités, opinions.
Django 5 + Django Ninja + PostgreSQL.

## Principes

**Un texte est écrit dans une langue et publié tel quel.** Pas de traduction, pas
de version alternative. Le corpus est bilingue (kreyòl / français), un texte ne
l'est jamais. La langue est un filtre de navigation, pas un sélecteur de version.

**La poésie n'est pas de la prose.** Les poèmes et citations sont stockés en texte
brut, retours à la ligne préservés à l'identique — les strophes *sont* le poème.
Les romans, histoires, actualités et opinions sont en Markdown. Le format est
déduit du type, et surchargeable.

**Les épisodes protègent la surprise côté serveur.** Un texte programmé pour dans
deux jours existe en base et apparaît au sommaire de sa série, mais l'API ne sert
jamais son corps, et son titre reste masqué sauf réglage contraire.

**Le corpus doit pouvoir sortir.** `export_corpus` rend l'intégralité des textes en
JSON et en Markdown, lisibles sans Floetik, sans Django et sans base de données.

## Démarrage

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env        # renseigner DATABASE_URL

createdb floetik            # ou : psql -U postgres -c "CREATE DATABASE floetik"
python manage.py migrate    # crée unaccent + pg_trgm, amorce les 8 thèmes
python manage.py createsuperuser
python manage.py runserver
```

| Adresse | Contenu |
|---|---|
| `/admin/` | L'espace de publication de Florentz, en français |
| `/api/v1/docs` | Documentation OpenAPI interactive |

## API de lecture

Aucune écriture : Florentz publie par l'admin, le public lit.

| Route | Rôle |
|---|---|
| `GET /api/v1/tex` | Lister — filtres `lang`, `kind`, `theme`, `series` |
| `GET /api/v1/tex/{slug}` | Lire un texte, avec épisode précédent / suivant |
| `GET /api/v1/tex/{slug}/vwazen` | Textes voisins par thème |
| `GET /api/v1/aza` | Un texte au hasard |
| `GET /api/v1/cheche?q=` | Recherche sans accents, tolérante aux fautes |
| `GET /api/v1/tem` | Les thèmes |
| `GET /api/v1/seri` | Les séries |
| `GET /api/v1/seri/{slug}` | Sommaire, épisodes à venir compris |

## Conventions de saisie

Dans un texte brut, un mot entouré d'astérisques s'affiche en or :

```
Mwen te *renmen* w
jan lapli renmen tè a
```

La position compte : si le mot revient plus loin, seule l'occurrence marquée est
dorée. C'est pourquoi le marqueur est dans le texte et non dans une liste à part.

## Sauvegarde

```bash
python manage.py export_corpus
python manage.py export_corpus --out D:\sauvegardes\floetik --published-only
```

Produit un `corpus.json` et un fichier Markdown par texte, brouillons compris.
