from datetime import timedelta
from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
)
environ.Env.read_env(BASE_DIR / ".env")

# Pas de valeur par défaut : une clé écrite dans le dépôt est une clé publique,
# et toute session ou signature du site devient falsifiable. Mieux vaut refuser
# de démarrer que démarrer en apparence sûr.
SECRET_KEY = env("SECRET_KEY", default="")
if not SECRET_KEY:
    raise ImproperlyConfigured(
        "SECRET_KEY est absente. Définissez la variable d'environnement "
        "SECRET_KEY (ou la ligne SECRET_KEY= du fichier .env) avec une valeur "
        "longue et aléatoire, propre à cette installation. Aucune valeur par "
        "défaut n'est fournie : elle serait publique."
    )
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",  # unaccent + pg_trgm pour la recherche kreyòl
    "axes",  # limite les tentatives de connexion à l'admin
    "corpus",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # En dernier : transforme le refus levé par django-axes en réponse lisible.
    "axes.middleware.AxesMiddleware",
]

# django-axes doit voir passer les tentatives avant que Django ne vérifie le
# mot de passe. Il n'authentifie personne lui-même : il compte et il bloque.
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]

# Le site n'a qu'un seul compte, celui de Florentz. Cinq essais suffisent
# largement à quelqu'un qui connaît son mot de passe ; au-delà, c'est quelqu'un
# qui devine. La porte se rouvre seule au bout d'une heure : pas besoin
# d'appeler à l'aide si l'auteur s'est trompé cinq fois de suite.
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = timedelta(hours=1)
AXES_LOCKOUT_PARAMETERS = ["ip_address"]
AXES_RESET_ON_SUCCESS = True
AXES_VERBOSE = True
# Par défaut axes masque le compte visé et l'IP dans ses journaux. Un journal
# qui dit « quelqu'un a échoué quelque part » ne sert à rien : on veut savoir
# quel compte est visé et d'où, c'est tout l'intérêt de la trace.
AXES_SENSITIVE_PARAMETERS = []

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DATABASES = {
    "default": env.db(
        "DATABASE_URL", default="postgres://postgres:postgres@localhost:5432/floetik"
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": f"django.contrib.auth.password_validation.{v}"}
    for v in (
        "UserAttributeSimilarityValidator",
        "MinimumLengthValidator",
        "CommonPasswordValidator",
        "NumericPasswordValidator",
    )
]

# Back-office en français : Florentz publie lui-même.
LANGUAGE_CODE = "fr"
# Les épisodes sont programmés à l'heure d'Haïti, pas à celle du serveur.
# Se tromper ici, c'est publier la suite d'un feuilleton au milieu de la nuit.
TIME_ZONE = "America/Port-au-Prince"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Sans ce plafond, django-ninja accepte ?limit=2147483647 : une seule requête
# anonyme fait alors remonter tout le corpus, corps compris. 100 lignes par page
# suffisent à toutes les vues du site et bornent le coût d'un appel public.
NINJA_PAGINATION_MAX_LIMIT = 100
# --------------------------------------------------------------------------
# HTTPS et cookies
# --------------------------------------------------------------------------
# Un seul interrupteur : DEBUG. En développement le site tourne en http sur la
# machine de Florentz, forcer HTTPS le rendrait injoignable. En production tout
# se resserre d'un coup. Le lecteur n'a qu'une ligne à regarder pour savoir
# dans quel monde il est.
EN_PRODUCTION = not DEBUG

# Derrière un terminateur TLS (Railway, Cloudflare…), la requête arrive en clair
# jusqu'à Django : sans cet en-tête il croit que tout le trafic est en http, la
# redirection HTTPS boucle et les cookies « secure » ne partent jamais.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

SECURE_SSL_REDIRECT = EN_PRODUCTION
# Un an, sous-domaines inclus et éligible au preload : la valeur attendue par
# les navigateurs. À zéro en développement, sinon le navigateur mémorise
# localhost en HTTPS pour un an et le site local devient inaccessible.
SECURE_HSTS_SECONDS = 31536000 if EN_PRODUCTION else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = EN_PRODUCTION
SECURE_HSTS_PRELOAD = EN_PRODUCTION

SESSION_COOKIE_SECURE = EN_PRODUCTION
CSRF_COOKIE_SECURE = EN_PRODUCTION

# Django >= 4 refuse les POST de l'admin dont l'Origin n'est pas listé ici dès
# qu'on est derrière un proxy : sans cette ligne, la connexion échoue sans
# raison visible. Format attendu : schéma + hôte, séparés par des virgules.
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

# --------------------------------------------------------------------------
# Journaux
# --------------------------------------------------------------------------
# Sans configuration, Django met sa console derrière `require_debug_true` et son
# envoi de courriel derrière un ADMINS non vide : avec DEBUG=False et ADMINS
# vide, une erreur 500 n'est écrite nulle part. Ici le fichier est branché sans
# condition — c'est le seul endroit qu'on pourra relire le lendemain.
LOG_DIR = Path(env("LOG_DIR", default=str(BASE_DIR / "logs")))
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Destinataires des rapports d'erreur ; format « Nom <adresse> », séparés par
# des virgules. Vide en développement, renseigné en production.
def _lire_admin(entree):
    """« Florentz <f@exemple.ht> » ou « f@exemple.ht » -> (nom, adresse)."""
    nom, crochet, adresse = entree.partition("<")
    if not crochet:
        return ("", entree.strip())
    return (nom.strip(), adresse.rstrip(">").strip())


ADMINS = [_lire_admin(e) for e in env.list("ADMINS", default=[]) if e.strip()]
MANAGERS = ADMINS

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "horodate": {
            "format": "{asctime} {levelname} {name} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "horodate",
        },
        "fichier": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(LOG_DIR / "floetik.log"),
            # ~10 Mo par fichier, cinq rotations : de quoi remonter loin sans
            # remplir le disque d'un petit serveur.
            "maxBytes": 10 * 1024 * 1024,
            "backupCount": 5,
            "encoding": "utf-8",
            "formatter": "horodate",
        },
        "courriel_admins": {
            "class": "django.utils.log.AdminEmailHandler",
            "level": "ERROR",
            "include_html": False,
        },
    },
    "root": {"handlers": ["console", "fichier"], "level": "INFO"},
    "loggers": {
        "django": {
            "handlers": ["console", "fichier"],
            "level": "INFO",
            "propagate": False,
        },
        # Les 500 passent par ici : on veut le fichier, et un courriel si
        # Florentz a renseigné une adresse.
        "django.request": {
            "handlers": ["console", "fichier"] + (["courriel_admins"] if ADMINS else []),
            "level": "ERROR",
            "propagate": False,
        },
        # Les tentatives de connexion refusées : la trace de qui frappe à la porte.
        "axes": {
            "handlers": ["console", "fichier"],
            "level": "INFO",
            "propagate": False,
        },
        "corpus": {
            "handlers": ["console", "fichier"],
            "level": "INFO",
            "propagate": False,
        },
    },
}

# Emplacement des sauvegardes du corpus (commande `export_corpus`).
CORPUS_EXPORT_DIR = env("CORPUS_EXPORT_DIR", default=str(BASE_DIR / "backups"))
