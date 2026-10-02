<!--
SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
SPDX-License-Identifier: CC-BY-ND-4.0
-->

# Configuration

**Public visé :** développeurs et exploitants.  
**Objectif :** décrire chargement, variables essentielles et fichiers de configuration runtime.  
**Sources de vérité :** `cintafactory/settings.py`, `.env.exemple`, fichiers Compose et modules `cintafactory/*config*.py`.  
**Dernière vérification :** 20 août 2026.

## Chargement

Django charge, sans écrasement des variables déjà présentes :

1. `cintafactory/.env` ;
2. `.env` à la racine du dépôt.

L'environnement du processus garde priorité. Docker Compose lit aussi `.env` pour substituer ses `${VARIABLES}` avant lancement des conteneurs.

Copier `.env.exemple` vers `.env`, puis remplacer toutes les valeurs factices. Ne jamais versionner `.env`, jetons, mots de passe ou clés JWT.

Booléens Django acceptés comme vrais : `1`, `true`, `yes`, `on`, sans distinction de casse.

## Configuration minimale

| Variable | Obligatoire | Défaut | Usage |
| --- | --- | --- | --- |
| `DJANGO_SECRET_KEY` | Oui | Aucun | Signature Django. Démarrage échoue si absent. |
| `DJANGO_DEBUG` | Non | `False` | Debug Django. Toujours faux en production. |
| `DJANGO_ALLOWED_HOSTS` | Production | `localhost` hors debug | Hôtes HTTP autorisés, séparés par virgules. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Selon exposition | Dérivé des hôtes | Origines avec schéma, séparées par virgules. |
| `DJANGO_SETTINGS_MODULE` | Conteneur | `cintafactory.settings` dans Compose | Module de réglages. |
| `RUN_MIGRATIONS` | Conteneur | Selon Compose | Lance migrations dans entrypoint. |
| `COLLECT_STATIC` | Conteneur | Selon Compose | Lance collecte statique dans entrypoint. |

## Base de données

Le mode de connexion est explicite :

- `DB_MODE=local` (défaut) conserve le conteneur PostgreSQL fourni par Compose ;
- `DB_MODE=external` connecte Django directement à un serveur PostgreSQL joignable depuis le réseau des conteneurs. En mode scaling, PgBouncer local est alors désactivé et Django se connecte directement à PostgreSQL.

| Variable | Défaut | Usage |
| --- | --- | --- |
| `DB_MODE` | `local` | `local` ou `external`. Toute autre valeur bloque le démarrage. |
| `DATABASE_HOST` | `db` en local | Nom ou adresse du serveur PostgreSQL. Obligatoire en mode externe. |
| `DATABASE_PORT` | `5432` | Port PostgreSQL, compris entre `1` et `65535`. |
| `DATABASE_NAME` | `POSTGRES_DB`, sinon vide | Base de données. Obligatoire en mode externe. |
| `DATABASE_USER` | `POSTGRES_USER`, sinon vide | Utilisateur PostgreSQL. Obligatoire en mode externe. |
| `DATABASE_PASSWORD` | `POSTGRES_PASSWORD`, sinon vide | Mot de passe. Peut être vide si l'authentification PostgreSQL le permet. |
| `DATABASE_SSLMODE` | `prefer` en local, `require` en externe | `disable`, `allow`, `prefer`, `require`, `verify-ca` ou `verify-full`. |
| `DATABASE_WAIT_TIMEOUT` | `120` secondes | Durée maximale d'attente d'une connexion DB au démarrage. |

En mode local, les variables `POSTGRES_DB`, `POSTGRES_USER` et `POSTGRES_PASSWORD` servent à initialiser le conteneur PostgreSQL et restent les valeurs de repli de Django. En mode externe, le serveur, la base, l'utilisateur, les permissions et les sauvegardes doivent être préparés hors de Compose. `DATABASE_URL` n'est pas une interface supportée.

### Exemple PostgreSQL externe

Dans `.env`, remplacer les valeurs d'exemple par celles fournies par l'administrateur PostgreSQL :

```dotenv
DB_MODE=external
DATABASE_HOST=postgres.example.internal
DATABASE_PORT=5432
DATABASE_NAME=cintafactory
DATABASE_USER=cintafactory_app
DATABASE_PASSWORD=replace-with-db-password
DATABASE_SSLMODE=require
DATABASE_WAIT_TIMEOUT=120
```

L'utilisateur doit disposer uniquement des permissions nécessaires à l'application. Le serveur PostgreSQL doit autoriser le réseau des conteneurs dans son pare-feu et les identifiants doivent rester dans un gestionnaire de secrets. L'exploitant externe reste responsable du provisionnement, des migrations, des sauvegardes/restaurations, de la supervision, des mises à jour et des règles réseau.

### Commandes Compose

Développement avec PostgreSQL fourni par Compose :

```bash
docker compose -f docker-compose.dev.yml up -d --build
```

Développement avec PostgreSQL externe :

```bash
docker compose -f docker-compose.dev.yml -f docker-compose.dev.external.yml up -d --build
```

Scaling avec la base locale et PgBouncer :

```bash
docker compose -f docker-compose.scaling.dev.yml up -d --build
```

Scaling avec PostgreSQL externe, sans conteneur `db` ni PgBouncer :

```bash
docker compose -f docker-compose.scaling.dev.yml -f docker-compose.scaling.dev.external.yml up -d --build
```

Les fichiers externes utilisent la fusion Compose `!override` pour retirer uniquement les dépendances liées à la base. Ils nécessitent une version récente de Docker Compose v2. Le fichier de déploiement utilise déjà une base PostgreSQL externe : renseigner les mêmes variables `DB_MODE` et `DATABASE_*` dans `deploy/env/prod.env` ou `deploy/env/test.env`.

### TLS PostgreSQL

Utiliser au minimum `DATABASE_SSLMODE=require` pour une base externe sur un réseau non totalement isolé. `require` chiffre la connexion, mais ne vérifie pas l'autorité de certification ni le nom d'hôte. Préférer `verify-full` lorsque le certificat et l'autorité de certification sont disponibles dans l'image ; `verify-ca` vérifie l'autorité sans vérifier le nom d'hôte. Réserver `disable` à un réseau maîtrisé et isolé. Ne pas utiliser `prefer` comme choix explicite pour une base externe si le chiffrement est obligatoire.

Le démarrage appelle `wait_for_database` et n'inscrit pas les mots de passe dans les logs. Avec `RUN_MIGRATIONS=1`, les migrations sont exécutées après que la connexion est disponible ; leur responsabilité et leur fenêtre d'exécution restent à définir par l'exploitant de la base externe.

## Sécurité HTTP et secrets

| Variable | Défaut | Recommandation production |
| --- | --- | --- |
| `DJANGO_ENFORCE_STRICT_SECRETS` | `0` | `1` |
| `DJANGO_ENFORCE_STRICT_HTTP` | `0` | `1` |
| `SECURE_HSTS_SECONDS` | `31536000` en mode strict, sinon `0` | Conserver après validation HTTPS. |
| `SECURE_HSTS_INCLUDE_SUBDOMAINS` | Activé en mode strict | Vérifier tous sous-domaines. |
| `SECURE_HSTS_PRELOAD` | Activé en mode strict | Activer seulement si domaine prêt. |
| `SECURE_REFERRER_POLICY` | `strict-origin-when-cross-origin` | Conserver sauf besoin documenté. |
| `SECURE_CROSS_ORIGIN_OPENER_POLICY` | `same-origin` | Conserver sauf intégration testée. |
| `SESSION_COOKIE_SECURE` | Suit mode strict | `1` |
| `SESSION_COOKIE_HTTPONLY` | `1` | `1` |
| `SESSION_COOKIE_SAMESITE` | `Lax` | Adapter seulement pour flux cross-site maîtrisé. |
| `CSRF_COOKIE_SECURE` | Suit mode strict | `1` |
| `CSRF_COOKIE_HTTPONLY` | `1` | `1` |
| `CSRF_COOKIE_SAMESITE` | `Lax` | Conserver selon auth. |
| `CSRF_USE_SESSIONS` | `1` | `1` |

Mode strict refuse notamment wildcard dans `ALLOWED_HOSTS`, origine CSRF HTTP, cookies non sécurisés et secrets SeaweedFS trop courts. Clés JWT SeaweedFS doivent contenir au moins 32 caractères.

## Stockage SeaweedFS

| Variable | Défaut | Sens |
| --- | --- | --- |
| `SEAWEEDFS_FILER_URL` | `http://seaweedfs:8888` | URL interne serveur. |
| `SEAWEEDFS_PUBLIC_URL` | URL interne | URL utilisée pour liens publics signés. |
| `SEAWEEDFS_PUBLIC_URL_PP` | `http://localhost:8888` | URL publique notamment utilisée pour images/profils et CSP. |
| `SEAWEEDFS_BASE_DIR` | `media` | Préfixe de stockage. |
| `SEAWEEDFS_TIMEOUT` | `30` | Timeout HTTP, secondes. |
| `SEAWEEDFS_JWT_WRITE_KEY` | Vide | Clé opérations écriture/suppression. |
| `SEAWEEDFS_JWT_READ_KEY` | Vide | Clé opérations lecture. |
| `SEAWEEDFS_JWT_TTL_SECONDS` | `60` | Durée JWT service. |
| `SEAWEEDFS_PUBLIC_JWT_TTL_SECONDS` | `300` | Durée URL publique signée. |

Compose configure aussi clés volume `SEAWEEDFS_VOLUME_JWT_*`, ports et origines CORS. Utiliser clés distinctes lecture/écriture et filer/volume.

## Uploads et antivirus

| Variable | Défaut | Sens |
| --- | --- | --- |
| `CLAMAV_HOST` | `clamav` | Hôte scanner. |
| `CLAMAV_PORT` | `3310` | Port clamd. |
| `CLAMAV_TIMEOUT` | `30` | Timeout d'une commande. |
| `CLAMAV_RETRY_COUNT` | `5` | Nouvelles tentatives après échec. |
| `CLAMAV_RETRY_DELAY` | `1.0` | Pause entre tentatives. |
| `CLAMAV_SCAN_DIR` | `/clamav_scan` | Répertoire partagé utilisé par commande `SCAN`. |

Deux limites existent :

- `conf/upload.json:max_file_size_mb`, défaut 200 MB : limite générique par fichier du handler Django ;
- limite pièces jointes DAT, défaut 25 MB dans `dat/attachments.py`.

Réglages Django `DAT_ATTACHMENT_MAX_SIZE_BYTES`, `ATTACHMENT_QUARANTINE_ENABLED` et `ATTACHMENT_QUARANTINE_MAX_BYTES` sont lus par code mais ne sont pas actuellement reliés à des variables d'environnement dans `settings.py`. Ajouter mapping explicite avant de compter sur une variable `.env` homonyme.

## draw.io

| Variable | Défaut | Sens |
| --- | --- | --- |
| `DRAWIO_BASE_URL` | `http://drawio:8080` | URL interne. |
| `DRAWIO_PUBLIC_URL` | URL interne | URL navigateur ou proxy Django. |
| `DRAWIO_LIBS` | `general` | Bibliothèques intégrées. |
| `DRAWIO_CLIBS` | Vide | URLs de bibliothèques XML, séparées par virgules. |
| `DRAWIO_EXPORT_DELETE_OLD` | `1` | Nettoyage anciens exports. |

`DRAWIO_EXPORT_URL` est construit en code vers `http://drawio-export:8000/export`; aucune variable directe homonyme n'est lue dans `settings.py`.

## LikeC4

### Django

| Variable | Défaut |
| --- | --- |
| `LIKEC4_EDITOR_URL` | `http://likec4:4173` |
| `LIKEC4_EXPORT_URL` | Vide |
| `LIKEC4_EXPORT_TIMEOUT` | `60` secondes |
| `LIKEC4_EXPORT_ENABLED` | `1` |
| `LIKEC4_EXPORT_DELETE_OLD` | `1` |
| `LIKEC4_METADATA_TOKEN` | Valeur dev uniquement si debug, sinon vide |
| `LIKEC4_API_TOKEN` | Valeur dev uniquement si debug, sinon vide |

### Services Node

Principales variables : `LIKEC4_EDITOR_PORT`, `LIKEC4_C4_FILE`, `LIKEC4_PREVIEW_DIR`, `LIKEC4_METADATA_URL`, `LIKEC4_BIN`, `LIKEC4_RENDER_TIMEOUT_MS`, `LIKEC4_EXPORT_HOST`, `LIKEC4_EXPORT_PORT`, `LIKEC4_EXPORT_MAX_BODY_BYTES`, `LIKEC4_EXPORT_TMP`, `LIKEC4_EXPORT_LOCAL_DIR`, `LIKEC4_EXPORT_FORMAT`, `LIKEC4_EXPORT_VIEW`, `LIKEC4_EXPORT_MAX_RETRIES`.

Tokens `LIKEC4_METADATA_TOKEN` et `LIKEC4_API_TOKEN` doivent être identiques dans Django, éditeur et exporter. Valeurs de développement interdites en production.

## Configuration OAuth

### État de validation

Google est actuellement le seul fournisseur OAuth testé de bout en bout. Les configurations Microsoft, Amazon et Okta sont présentes, mais doivent être considérées comme expérimentales tant que leur parcours complet n'a pas été validé dans l'environnement cible.

Un autre fournisseur peut être ajouté sans créer un nouveau parcours de connexion s'il respecte le contrat OAuth 2.0/OIDC utilisé par l'application :

- flux Authorization Code ;
- endpoints d'autorisation, de jeton et de profil accessibles en HTTPS ;
- échange du code par requête `POST` de formulaire ;
- réponse JSON contenant un `access_token` ;
- endpoint de profil acceptant `Authorization: Bearer <token>` et renvoyant du JSON ;
- identifiant utilisateur stable disponible dans le profil.

OAuth 1.0, SAML ou un fournisseur imposant un échange particulier — PKCE obligatoire, `client_assertion`, authentification HTTP Basic du client, réponse non JSON ou profil fortement imbriqué — nécessitent une adaptation du code.

### Variables communes

- `OAUTH_HTTP_TIMEOUT`, défaut 10 secondes ;
- `OAUTH_ALLOW_EMAIL_LINKING`, défaut activé ;
- `ENDPOINT_RATE_LIMIT_PER_IP_PER_MINUTE`, défaut 30 pour les endpoints sensibles.

Fournisseurs préconfigurés :

| Fournisseur | Variables | Validation |
| --- | --- | --- |
| Google | `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET` | Testé |
| Microsoft | `MICROSOFT_OAUTH_TENANT_ID`, `MICROSOFT_OAUTH_CLIENT_ID`, `MICROSOFT_OAUTH_CLIENT_SECRET` | À valider |
| Amazon | `AMAZON_OAUTH_CLIENT_ID`, `AMAZON_OAUTH_CLIENT_SECRET` | À valider |
| Okta | `OKTA_OAUTH_DOMAIN`, `OKTA_OAUTH_CLIENT_ID`, `OKTA_OAUTH_CLIENT_SECRET` | À valider |

Un fournisseur sans identifiant et secret complets reste désactivé.

### Ajouter un fournisseur OAuth2/OIDC

1. Créer une application auprès du fournisseur et déclarer exactement l'URL de callback publique :

   ```text
   https://<hôte>/accounts/oauth/<slug>/callback/
   ```

   Le `<slug>` doit être identique à la clé ajoutée dans `OAUTH_PROVIDERS`.

2. Ajouter l'identifiant et le secret aux variables sécurisées du déploiement. Ne jamais enregistrer de vrai secret dans Git. Seuls des noms de variables et des exemples vides doivent apparaître dans `.env.exemple`.

3. Ajouter une entrée dans `OAUTH_PROVIDERS`, dans `cintafactory/cintafactory/settings.py` :

   ```python
   "example": {
       "label": "Mon fournisseur",
       "client_id": os.getenv("EXAMPLE_OAUTH_CLIENT_ID", ""),
       "client_secret": os.getenv("EXAMPLE_OAUTH_CLIENT_SECRET", ""),
       "authorize_url": "https://idp.example.com/oauth/authorize",
       "token_url": "https://idp.example.com/oauth/token",
       "userinfo_url": "https://idp.example.com/oauth/userinfo",
       "scopes": ("openid", "email", "profile"),
       "extra_authorize_params": {},
       "userinfo_mapping": {
           "user_id": "sub",
           "email": "email",
           "email_verified": "email_verified",
           "first_name": "given_name",
           "last_name": "family_name",
           "full_name": "name",
       },
   }
   ```

   Les URL et scopes ci-dessus sont des exemples : utiliser ceux documentés par le fournisseur.

4. Adapter `userinfo_mapping` aux clés réellement renvoyées par l'endpoint de profil. `user_id` est obligatoire. Les autres champs sont facultatifs, mais `email` et `email_verified` sont importants pour la liaison avec un compte existant.

5. Facultatif : ajouter une icône statique et sa correspondance dans `icon_map` de `cintafactory/users/oauth_views.py`. Sans icône, le fournisseur reste utilisable.

6. Redémarrer l'application, puis tester le parcours complet dans un environnement non productif : affichage du bouton, redirection, validation de `state`, callback, échange du code, lecture du profil, création ou liaison du compte, reconnexion et déconnexion.

### Liaison par adresse email

Quand `OAUTH_ALLOW_EMAIL_LINKING` est activé, un profil OAuth peut être lié à un compte local portant la même adresse. Avant d'activer cette option pour un nouveau fournisseur, vérifier qu'il renvoie une adresse fiable et un statut `email_verified` correctement mappé. En cas de doute, désactiver la liaison automatique jusqu'à validation.

Les jetons OAuth étant enregistrés par l'application, la base de données, les sauvegardes et les accès administratifs doivent être protégés en conséquence.

## Email et logs

SMTP : `EMAIL_BACKEND`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`, `EMAIL_USE_SSL`, `EMAIL_TIMEOUT`, `DEFAULT_FROM_EMAIL`, `SERVER_EMAIL`.

Logs : `DJANGO_LOG_LEVEL`, `DJANGO_LOG_DIR`, `DJANGO_LOG_MAX_BYTES`, `DJANGO_LOG_BACKUP_COUNT`, `DJANGO_LOG_TO_STDOUT`, `LOG_CRITICAL_WEBHOOK`.

Ne pas écrire secrets, jetons OAuth, contenu de pièces jointes ou PII non nécessaire dans logs/webhooks.

## Jobs asynchrones

Code reconnaît trois modes : `inline`, `thread`, `external`. Backoffs par défaut :

- LikeC4 : 5 s, 20 s ;
- draw.io : 5 s, 20 s ;
- PDF : 10 s, 30 s, 120 s.

`ASYNC_JOBS_RUNNER_MODE` et réglages `ASYNC_JOBS_*_BACKOFF_SECONDS` sont consultés comme réglages Django, mais ne sont pas déclarés dans `settings.py`. Une variable d'environnement seule — même présente dans Compose — ne crée pas ce réglage. État actuel : fallback `thread`, sauf surcharge Django explicite. Corriger ce mapping avant de compter sur mode `external` en production.

## Fichiers `conf/`

Créés automatiquement sous `cintafactory/conf/` si absents :

| Fichier | Rôle | Défaut notable |
| --- | --- | --- |
| `admin.json` | Segment URL admin | UUID statique par défaut ; à changer. |
| `limit.json` | Limites app/API | 500/IP/min app, 50/utilisateur/min app, 1000/IP/min API. |
| `upload.json` | Limite générique upload | 200 MB/fichier. |
| `external_notifications.json` | Backends notifications | Liste vide. |
| `dat_viewflow_template.json` | Disposition du graphe DAT | Modèle intégré. |
| `section_blueprints.json` | Structure des sections | Copie du blueprint versionné. |
| `theming/active.json` | Thème actif | `base`. |
| `theming/base/tokens.json` | Palette/typographie | Thème Cinta classique. |

Ces fichiers sont runtime. Monter volume persistant si modifications doivent survivre aux conteneurs. URL admin masquée n'est pas une barrière de sécurité : authentification et permissions restent obligatoires.

## Checklist production

- `DJANGO_DEBUG=0`.
- Modes stricts activés.
- Hôtes/origines exacts, HTTPS uniquement.
- Clés uniques, fortes, stockées hors dépôt.
- Secrets partagés cohérents entre services.
- DB non exposée publiquement ; compte applicatif minimal.
- URLs publiques différentes des noms Docker internes.
- `conf/` persistant, sauvegardé et permissions fichier minimales.
- Liveness, readiness, métriques, logs et alertes vérifiés.
- Valeurs factices `replace-*` absentes.
