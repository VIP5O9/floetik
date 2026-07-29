# Passation — état du backend au 29 juillet 2026

Ce document complète `ROADMAP.md`. Il dit **où en est le code**, **ce qui reste**, et
**ce qui a été vérifié plutôt que supposé**. À lire avant de reprendre le travail.

---

## Où on en est

`main` porte maintenant l'intégralité du jalon 0. **76 tests, tous verts**, contre un
PostgreSQL 16 réel — c'était 0 test il y a deux jours.

Le jalon 0 est **terminé et vérifié**. Le chemin critique n'est plus le code.

### Ce qui a été corrigé et prouvé

Chaque ligne ci-dessous a été reproduite en panne, puis reproduite en marche.

| Domaine | Avant | Après |
|---|---|---|
| Pagination | Chaque liste chargeait tout le corpus | `LIMIT` réel en SQL, corps du texte plus rapatrié |
| Recherche | Un `?q=` de 500 000 caractères occupait la base **503 secondes** | Plafond à 200 caractères, refus en 422 |
| Séries non publiées | Titre, description et couverture publics dès la création | Absentes de `/seri`, 404 en détail |
| Ordre des épisodes | Contournable par script ; trous, date nulle, épisode 0 | Vérifié à chaque `save()`, trois brèches fermées |
| Enregistrement | 11 erreurs 500 sur 12 en cas d'écriture concurrente | Reprise sur collision, slug tronqué, coût constant |
| Connexion admin | 25 mots de passe faux acceptés sans blocage | Verrouillage après 5, tentatives journalisées |
| `SECRET_KEY` | Valeur par défaut versionnée, démarrage silencieux | Absente ⇒ le démarrage échoue |
| Journalisation | Une erreur 500 n'atteignait rien ni personne | Journaux configurés |
| Documentation API | `/api/v1/docs` public | Réservée au personnel |
| Extraits | Une citation d'une ligne était servie **en entier** en liste | Plafonnée, et rafraîchie quand le texte change |
| `publier_maintenant` | Écrasait la date de parution d'un texte déjà en ligne | Ignore les textes déjà publiés |
| Sauvegarde | Front-matter cassé par un `:` dans un titre | Échappé, horodatage avec fuseau |

---

## Ce qui reste — par ordre de priorité

### 1. Mettre en ligne (jalon 1) — c'est la vraie urgence

**Le corpus est vide.** Florentz n'a pas encore saisi un seul texte, et la saisie de
plusieurs années d'écrits est la tâche la plus longue du projet. Elle ne peut pas
commencer tant que le back-office n'est pas hébergé.

Chaque jour passé à polir le backend est un jour où il ne saisit pas.

À faire : héberger le backend et la base, stockage des médias, sous-domaine
d'administration en HTTPS, **sauvegarde quotidienne automatique hors serveur**, puis
une prise en main avec lui.

Deux pièges connus, à traiter avant le déploiement :

- La migration `0002` exige un rôle **superutilisateur** pour créer les extensions
  `unaccent` et `pg_trgm`. Beaucoup d'hébergeurs gérés le refusent. À tester tôt.
- `settings.py` lit `.env` au chargement et `DEBUG` n'a pas de valeur de repli : sans
  fichier `.env`, rien ne démarre. La CI et l'hébergeur doivent en fournir un.

### 2. Quatre correctifs sans implémentation

Tickets ouverts, personne n'a encore écrit de code :

- **#9** — un export `--published-only` peut contenir un brouillon laissé par un export
  précédent lancé dans la même minute. C'est le drapeau qu'on utiliserait pour confier
  une copie à un tiers.
- **#10** — l'export n'est pas ré-importable : libellés d'affichage au lieu des valeurs
  de base, références ambiguës, sept champs jamais exportés, aucune commande d'import.
  La promesse « le corpus peut sortir » n'est tenue qu'à moitié.
- **#11** — la recherche floue ne trouve pas les variantes kreyòl (`lammou` ne trouve pas
  `Lanmou`), et **les index GIN de la migration `0003` ne servent jamais** : le prédicat
  porte sur `unaccent(titre)`, une expression, alors que les index sont sur les colonnes
  nues. Mesuré : `idx_scan = 0` pour les deux, avant comme après des recherches.
- **#12** — `CORPUS_EXPORT_DIR` est relatif au répertoire courant dans `.env.example` ;
  une tâche planifiée écrira les sauvegardes ailleurs que prévu.

### 3. Le site de lecture (jalon 2)

Rien n'existe. C'est le produit.

---

## Décision en attente — #13

**Que doit-il se passer quand un épisode déjà publié est dépublié alors qu'un épisode
suivant est en ligne ?**

Reproduit : avec les épisodes 1, 2 et 3 en ligne, repasser le 2 en brouillon est accepté
sans erreur et le sommaire public affiche **[1, 3]**. Le lecteur passe du chapitre 1 au
chapitre 3.

Trois options — (a) cascade automatique, (b) blocage de la dépublication, (c) statu quo
documenté avec un message d'erreur plus clair. Aucune n'est meilleure dans l'absolu :
cela dépend de la fréquence réelle à laquelle Florentz dépublie un épisode après coup.
**C'est une question à lui poser.** Ce n'est volontairement pas corrigé.

À noter : le correctif de `save()` rend le symptôme secondaire — un échec incompréhensible
en modifiant un épisode ultérieur — nettement plus fréquent qu'avant.

---

## Environnement de développement

```bash
# Base de test
docker run -d --name floetik-db -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_USER=postgres -e POSTGRES_DB=floetik -p 5433:5432 postgres:16-alpine

# Python — 3.13 obligatoire, Django 5.2.7 ne supporte pas 3.14
uv venv --python 3.13 backend/.venv
uv pip install --python backend/.venv/bin/python -r backend/requirements.txt

cp backend/.env.example backend/.env   # puis renseigner DATABASE_URL
cd backend && .venv/bin/python manage.py migrate
.venv/bin/python manage.py test corpus   # 76 tests
```

---

## Deux avertissements pour la suite

**Les branches de correctifs ne se combinent pas naïvement.** Pendant l'intégration, deux
pièges sont apparus, invisibles branche par branche : les tests de schéma OpenAPI d'un
correctif échouaient parce qu'un autre venait de rendre la documentation privée ; et la
résolution évidente d'un conflit dans `save()` aurait **silencieusement désactivé** la
reprise sur collision de slug. Toujours faire tourner la suite complète après fusion,
jamais seulement les tests de la branche.

**Une suite verte ne prouve pas qu'un défaut est mort.** Plusieurs vérifications de ce
travail ont donné de faux résultats parce que leur filtre ne trouvait rien et concluait
au succès. Pour un correctif qui compte : reproduire la panne d'origine, appliquer le
correctif, reproduire à nouveau — et retirer le correctif pour vérifier que le test
redevient rouge.
