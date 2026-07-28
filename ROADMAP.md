# Floetik — ce qui reste à faire

> État au 27 juillet 2026. Le backend existe et fonctionne en local ; rien n'est
> en ligne, le corpus est vide, le frontend n'existe pas.

---

## Où on en est

**Acquis, testé :** modèles du corpus, back-office français, API de lecture
(8 routes), recherche kreyòl, verrouillage des épisodes programmés, garde-fou
d'ordre des séries, export/sauvegarde du corpus.

**Acquis, non testé automatiquement :** tout le reste. Les vérifications ont été
faites à la main, par des scripts jetables qui ne sont pas dans le dépôt.

**Dette identifiée et mesurée :** voir le jalon 0 — elle est réelle, elle est
chiffrée, et elle n'est pas bloquante pour la saisie du corpus.

---

## Le principe qui commande l'ordre

**Le corpus est le chemin critique, pas le code.** Florentz doit ressaisir des
années de textes aujourd'hui prisonniers d'images Instagram. C'est la tâche la
plus longue du projet et la seule que le développement ne peut pas accélérer.

→ Elle doit démarrer **le plus tôt possible**, en parallèle du reste. Le
back-office fonctionne déjà : il suffit de le mettre en ligne. C'est le jalon 1,
avant même le frontend.

---

## Jalon 0 — Solidité du backend

*Environ 1 jour. À faire avant de déployer, pas après.*

| Tâche | Pourquoi |
|---|---|
| Pagination réellement poussée en SQL | Aujourd'hui chaque liste charge tout le corpus, romans compris — mesuré, confirmé |
| Index GIN trigrammes sur titre et corps | `pg_trgm` est activé sans index : la recherche floue scanne tout |
| `full_clean()` forcé au `save()` | Le garde-fou d'ordre des séries ne protège que l'admin ; un script le contourne |
| **Suite de tests** | Le point le plus important. Verrouillage des épisodes, garde-fou d'ordre, fidélité du texte, filtres, export |
| `view_count` : anti-rafale et filtrage des robots | Sinon les statistiques de Florentz sont fausses dès le premier crawler |
| `/aza` sans `ORDER BY RANDOM()` | Scan complet à chaque appel, et cible facile |
| Brancher CORS ou retirer le réglage | `CORS_ORIGINS` est déclaré et jamais utilisé |
| Corriger le N+1 sur la liste des séries | Un `COUNT` par série |

---

## Jalon 1 — Florentz commence à saisir

*Environ 1 jour. Débloque la tâche la plus longue du projet.*

- Héberger le backend (Railway ou petit VPS) et la base (Neon PostgreSQL)
- Stockage des médias (Cloudflare R2)
- Sous-domaine d'administration, HTTPS, mot de passe fort
- **Sauvegarde automatique quotidienne** — `export_corpus` planifié, copie hors serveur
- Compte de Florentz, et une prise en main avec lui

À partir de là, il saisit pendant qu'on développe. Il faut décider **qui saisit**
et **combien de textes** — ce chiffre n'est pas encore connu et il change tout.

---

## Jalon 2 — Le site de lecture

*Environ 4 à 6 jours. C'est le produit.*

- Projet Next.js, appels API côté serveur
- **Système de design noir + or** : palette, polices auto-hébergées et sous-ensemblées
  (Cormorant / Playfair + Bebas), test de validation — un écran doit pouvoir être
  posté tel quel sur son Instagram
- **La page de lecture** — typographie, strophes préservées, mots en or, respiration.
  Tout le reste du site existe pour amener ici.
- Accueil, navigation par thème, filtre de langue (Tout / Kreyòl / Français)
- Sommaire de série avec **compte à rebours** sur l'épisode à venir
- Blog (actualités et opinions) — mêmes modèles, vue filtrée
- Recherche
- Navigation épisode précédent / suivant, textes voisins, texte au hasard
- **PWA / lecture hors connexion** — en Haïti, ce n'est pas un gadget

---

## Jalon 3 — Diffusion

*Environ 0,5 jour. **Aucune génération d'images** : décision du 27 juillet 2026.*

Instagram reste le domaine de Florentz. Il fabrique ses carrousels lui-même, comme
aujourd'hui — c'est son métier, et une card générée automatiquement serait un
confort, pas un gain de qualité. Le site et Instagram restent deux canaux
distincts, alimentés par la même source de textes.

- Métadonnées Open Graph — pour que l'aperçu WhatsApp affiche titre et extrait
- **Une seule image de partage, statique** (logo QF sur fond noir), pas une image
  par texte
- `sitemap.xml` et flux RSS, en kreyòl

**Conséquence sur le document de marque :** la règle d'or n°4 (« conserver le
format carrousel + Glise → ») concerne désormais son travail Instagram, pas la
plateforme. Elle sort du périmètre logiciel.

---

## Jalon 4 — Audio

*Environ 1 jour. Modèle déjà en place.*

- Téléversement, durée calculée automatiquement, forme d'onde
- Lecteur intégré à la page de lecture
- Publication à date distincte du texte
- Un roman devient un audiolivre chapitre par chapitre

---

## Jalon 5 — Communauté

*Environ 3 à 4 jours. C'est ce qui fait le « mouvement ».*

- **Newsletter** : double opt-in, export, fournisseur d'envoi à choisir
- **Abonnement par série** et rappel automatique à la sortie d'un épisode —
  c'est ce qui ramène le lecteur le jour dit
- Réactions anonymes, limitées par IP
- Témoignages modérés
- Reprise de lecture dans une série, sans compte

---

## Jalon 6 — Kado

*Environ 1 jour.*

- Lien WhatsApp prérempli depuis un texte, vers `+509 34 75 1563`
- Journalisation des intentions de commande
- Tableau de bord admin : textes les plus lus, les plus partagés, croissance de
  la newsletter, conversions

---

## Jalon 7 — Mise en ligne publique

*Environ 1 à 2 jours.*

- Frontend sur Cloudflare Pages (usage commercial autorisé, CDN, gratuit)
- Nom de domaine, HTTPS, redirections
- Vérification des aperçus WhatsApp, Facebook, Instagram sur de vrais partages
- Mesure d'audience respectueuse de la vie privée
- Test réel sur connexion lente et téléphone d'entrée de gamme

---

## Ordre de grandeur

Environ **2 semaines et demie de développement** pour une personne seule, hors
saisie du corpus — l'abandon de la génération d'images retire 2 jours du jalon 3.
La saisie, elle, dépend du volume de textes et de la disponibilité de Florentz :
c'est le vrai facteur limitant, et elle commence au jalon 1.

Un site lisible en public est atteignable aux **jalons 0 + 1 + 2**, soit environ
une semaine, avec le corpus déjà en cours de remplissage.

---

## Décisions encore en attente

1. **Combien de textes** au départ, et **qui les saisit** — jamais chiffré, et
   c'est le paramètre le plus structurant du calendrier
2. **Langue de l'interface** : kreyòl par défaut ? bascule vers le français ?
3. **Nom de domaine**
4. **Fournisseur d'envoi d'emails** pour la newsletter
5. **Stand-up et théâtre** : entrent-ils dans la plateforme, ou v1 sur l'écrit seul ?

---

## À corriger dans la documentation

`SAVOIR-METIER-MARQUE.md` date d'avant plusieurs décisions et devient trompeur :

- Il affirme l'écriture **exclusivement en kreyòl** ; Florentz écrit aussi en
  français. Le document ferait supprimer la moitié du corpus s'il était appliqué
  à la lettre dans deux ans.
- Il ne mentionne ni les **épisodes**, ni l'**audio**, ni le **blog**, ni les
  types roman / histoire / actualité / opinion.
- Il décrit un site vitrine adossé à une boutique ; le produit est une
  **plateforme de lecture**.

C'est censé être le document qui survit à une reconstruction. Il doit être mis à
jour avant d'être oublié.
