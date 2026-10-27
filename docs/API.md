<!--
SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
SPDX-License-Identifier: CC-BY-ND-4.0
-->

# API CintaFactory

**Public visé :** développeurs d'intégration et mainteneurs backend.  
**Objectif :** expliquer conventions, authentification, ressources et permissions de l'API REST.  
**Sources de vérité :** schéma OpenAPI généré, `cintafactory/api/urls.py`, `users/api.py`, `dat/api.py` et `cintafactory/api/jobs.py`.  
**Dernière vérification :** 27 octobre 2026.

## Points d'entrée

| URL | Usage |
| --- | --- |
| `/api/` | Racine du routeur REST. |
| `/api/schema/` | Schéma OpenAPI brut. |
| `/api/docs/` | Swagger UI. |
| `/api/redoc/` | ReDoc. |
| `/api/auth/login/` | Connexion de session proposée par Django REST Framework. |
| `/oauth/` | Endpoints OAuth2 fournis par `django-oauth-toolkit`. |

Utiliser le schéma généré comme contrat de référence pour la liste des champs et endpoints. Cette page documente surtout les règles que le schéma exprime mal : portée des permissions, filtrage, effets de bord et conventions. Les limites connues ci-dessous doivent toutefois être prises en compte ; en cas de divergence, le comportement de l'implémentation actuelle l'emporte.

Limites connues du schéma actuel :

- les paramètres `{id}` des endpoints jobs sont décrits comme chaînes génériques alors que les identifiants sont des UUID ;
- OAuth2 est configuré à l'exécution, mais les opérations du schéma généré n'annoncent actuellement que l'authentification par cookie et le flux OAuth2 y est incomplet.

## Authentification

Deux mécanismes sont configurés :

### Session Django

Adaptée au navigateur et à Swagger UI pour les superutilisateurs auxquels l'accès API est accordé. Les requêtes modifiant des données exigent un token CSRF valide.

### OAuth2

Adapté aux clients externes. Envoyer le jeton :

```http
Authorization: Bearer <access-token>
```

#### Enregistrer un client et générer un jeton

Les routes `django-oauth-toolkit` sont montées sous `/oauth/`. Tout utilisateur Django authentifié peut enregistrer et gérer ses propres applications OAuth via les vues intégrées : formulaire d'inscription `/oauth/applications/register/`, liste `/oauth/applications/`. Le projet n'ajoute pas de restriction `staff` ou superutilisateur à ces vues ; chaque compte ne gère que ses propres applications. Enregistrer un client avec son type, le flux `Authorization code` et une URI de redirection exacte. Identifiants et flux sont stockés en base, pas dans les paramètres statiques du dépôt.

1. Rediriger l'utilisateur vers `/oauth/authorize/` avec `response_type=code`, `client_id`, `redirect_uri` et une valeur aléatoire `state`. L'utilisateur se connecte puis autorise le client. Vérifier `state` au retour et récupérer le code d'autorisation depuis l'URI de redirection.
2. Échanger le code contre un jeton en envoyant une requête `application/x-www-form-urlencoded` à `/oauth/token/`. Pour un client confidentiel, authentifier la requête avec `client_id` et `client_secret` (HTTP Basic dans cet exemple) :

```bash
curl -X POST https://cintafactory.example/oauth/token/ \
  -u "$OAUTH_CLIENT_ID:$OAUTH_CLIENT_SECRET" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=authorization_code" \
  -d "code=$OAUTH_AUTHORIZATION_CODE" \
  -d "redirect_uri=$OAUTH_REDIRECT_URI"
```

La réponse OAuth contient `access_token` et `token_type` (`Bearer`), ainsi que sa durée de validité. Réutiliser `access_token` dans l'en-tête présenté ci-dessus. Si le client est public, suivre sa configuration OAuth et le flux PKCE au lieu d'envoyer un secret. Un jeton de renouvellement n'est disponible que si le flux et la configuration en émettent un.

Les types de client, flux autorisés, URI de redirection et éventuelles portées sont configurés par application OAuth dans l'environnement cible. Le dépôt ne déclare pas de client ni de secrets OAuth fixes. Les identifiants OAuth Google, Microsoft, Amazon ou Okta servent à la connexion fédérée des utilisateurs ; ce ne sont pas des identifiants clients pour cette API. L'API n'utilise pas de clé API générale.

En plus d'une authentification valide (session Django ou OAuth2), le compte doit avoir `can_use_api=True`. Cette permission est désactivée par défaut et seul un superadministrateur peut la modifier dans la gestion des utilisateurs. La vérification s'applique à chaque requête API ; désactiver l'accès bloque donc les jetons OAuth déjà émis. Les routes UI normales restent disponibles.

Sans cet accès, la navigation dans un navigateur vers toute URL `/api/`, y compris la racine, le schéma, Swagger, ReDoc et les pages d'authentification DRF, renvoie la page 404. Les appels non navigateur continuent d'être refusés par les permissions API. L'interface normale de compte et de gestion des utilisateurs reste accessible selon ses permissions UI.

L'authentification par jeton ne contourne pas les permissions API : chaque ressource reprend les règles d'accès de son interface correspondante, avec filtrage objet pour les DAT. Voir [Permissions](#permissions).

La description OAuth2 incomplète du schéma peut empêcher Swagger UI de proposer une authentification OAuth2 directement utilisable ; cela ne désactive pas le mécanisme à l'exécution.

Ne jamais placer jeton dans URL, logs ou message d'erreur.

## Format

- JSON est le format principal des requêtes et réponses REST.
- Les requêtes d'écriture acceptent aussi `application/x-www-form-urlencoded` et `multipart/form-data` avec la configuration DRF actuelle. Utiliser multipart pour envoyer `profile_picture`.
- L'API navigable DRF peut renvoyer du HTML selon l'en-tête `Accept`. Envoyer `Accept: application/json` pour forcer une réponse JSON.
- UUID utilisés comme identifiants des ressources actuelles.
- Dates/heures sérialisées au format ISO 8601 par Django REST Framework.
- Slash final attendu par le routeur, par exemple `/api/dats/`.
- Aucune pagination globale n'est configurée actuellement.
- Aucun préfixe de version n'est présent dans URL.

Le `VERSION = 1.0.0` du schéma OpenAPI décrit le document ; il ne met pas en place une politique de compatibilité automatique.

## Permissions

Seuls les superutilisateurs (`is_superuser=True`) avec `can_use_api=True` peuvent consulter `/api/schema/`, `/api/docs/` et `/api/redoc/`. Toutes les requêtes vers les ressources REST exigent une session ou un jeton OAuth valide et `can_use_api=True`, y compris pour les superutilisateurs. Ce contrôle précède les permissions par ressource ci-dessous. La page de connexion DRF `/api/auth/login/` est également cachée par 404 au navigateur si l'accès API est désactivé ; la page normale `/accounts/login/` reste accessible.

| Ressource | Lecture | Création | Modification | Suppression |
| --- | --- | --- | --- | --- |
| Utilisateurs et groupes | `can_use_api=True` et superutilisateur | `can_use_api=True` et superutilisateur | `can_use_api=True` et superutilisateur | `can_use_api=True` et superutilisateur |
| Applications | Utilisateur authentifié avec `can_use_api=True` ; liste complète, comme le CRUD de gestion | `can_use_api=True` et rôle `porteur-demande` | Superutilisateur, staff ou rôle `admin` | Superutilisateur, staff ou rôle `admin` |
| DAT | Utilisateur authentifié avec `can_use_api=True`, limité aux DAT visibles dans l'application | `can_use_api=True` et rôle `porteur-demande` | Responsable global (`superuser`, staff ou rôle `admin`) ou propriétaire si le workflow autorise `owner_editable` | Responsable global (`superuser`, staff ou rôle `admin`) |
| Jobs | Compte avec `can_use_api=True` et demandeur du job ; staff et superutilisateur voient tous les jobs | Sans objet | Actions opérateur : staff ou superutilisateur | Sans objet |

Les opérations DAT appliquent `filter_dat_queryset_for_user()` avant toute lecture ou recherche par identifiant. Un DAT est visible par son propriétaire, ses participants, le responsable du groupe métier d'un participant ou un administrateur DAT explicite. Les administrateurs globaux (superutilisateur, staff, rôle `admin`) voient tous les DAT.

## Ressources

### Utilisateurs — `/api/users/`

Méthodes : `GET`, `POST`, `PUT`, `PATCH`, `DELETE`.

Champs :

- `id`, `username`, `email`, `first_name`, `last_name` ;
- `is_active`, `is_staff`, `is_superuser`, `can_use_api` ;
- `role`, `business_group`, `profile_picture` ;
- `last_login`, `date_joined` en lecture seule ;
- `password` facultatif en écriture seule.

Créer ou modifier un mot de passe utilise `set_password()` ; valeur jamais renvoyée. Les champs privilégiés exigent une attention particulière côté clients d'administration.

Tous les endpoints utilisateurs et groupes, y compris `OPTIONS`, sont réservés aux superutilisateurs qui ont aussi `can_use_api=True`. Seul un superadministrateur peut modifier `can_use_api` ; il peut le faire dans la gestion des utilisateurs, ou via `/api/users/` si son propre accès API est activé. Les gardes de sérialiseur restent appliquées en défense en profondeur.

### Groupes métier — `/api/groups/`

Méthodes : `GET`, `POST`, `PUT`, `PATCH`, `DELETE`.

Champs : `id`, `name`, `direction`, `responsible`, `is_default`, `business_direction`.

Les contraintes organisationnelles du modèle restent appliquées. Voir [TechnicalDepartment.md](TechnicalDepartment.md).

### Applications — `/api/applications/`

Méthodes : `GET`, `POST`, `PUT`, `PATCH`, `DELETE`.

Champs : `id`, `code`, `name`, `description`, `business_direction`, `created_at`, `updated_at`. Identifiant et horodatages sont en lecture seule.

### DAT — `/api/dats/`

Méthodes : `GET`, `POST`, `PUT`, `PATCH`, `DELETE`.

Champs principaux :

- identité : `id`, `reference`, `title`, `description` ;
- relations : `application`, `owner`, `business_direction` ;
- workflow : `status` ;
- export : état, demandeur, chemin, type MIME, taille et contrôle de double approbation ;
- audit : `created_at`, `updated_at`.

`id`, `owner`, `status`, `business_direction`, `created_at` et `updated_at` sont en lecture seule. À la création, l'utilisateur authentifié devient propriétaire. La direction métier est recalculée depuis l'application lors de l'enregistrement.

Les champs d'export sont en lecture seule. Les données de workflow ne peuvent pas être modifiées directement : l'API CRUD ne réalise pas les transitions détaillées de l'interface de validation. Utiliser les actions métier de l'application pour faire progresser un DAT.

Un propriétaire ne peut modifier un DAT via l'API que lorsque l'état du workflow autorise `owner_editable`. Cette règle est la même que celle de l'interface CRUD.

### Jobs — `/api/jobs/`

Lecture limitée au demandeur du job ; staff et superutilisateurs peuvent lire tous les jobs :

- `GET /api/jobs/` ;
- `GET /api/jobs/{job_id}/` ;
- filtre facultatif `?resource_ref=<valeur>`.

Un utilisateur ne peut pas consulter le job d'un autre demandeur. Les actions `cancel`, `requeue` et `ignore` restent réservées au staff et aux superutilisateurs.

Champs retournés : `job_id`, type, file, statut, référence ressource, demandeur, dates, tentatives, dernière erreur et résultat.

États :

```text
queued → running → succeeded
                 ↘ failed / dead_lettered
queued|running → cancelled (marquage par cancel)
failed|dead_lettered|cancelled → queued → running (requeue)
tout état → cancelled (ignore)
```

`cancel` ne fournit pas une annulation coopérative du traitement. L'action marque la ligne `cancelled`, mais un worker déjà en cours ne revérifie pas cet état entre ses tentatives et peut ensuite réécrire `running`, `succeeded` ou `dead_lettered`.

`requeue` remet d'abord le job à `queued`, puis appelle actuellement le dispatcher directement dans la requête HTTP. L'appel peut donc rester bloqué pendant le traitement. La confirmation renvoie la valeur locale `queued`, mais l'état stocké peut déjà avoir évolué lorsque la réponse arrive.

Actions opérateur, réservées au staff/superutilisateur :

| Endpoint | Condition |
| --- | --- |
| `POST /api/jobs/{id}/cancel/` | Job `queued` ou `running` ; marqueur d'état, sans arrêt garanti d'un worker actif. |
| `POST /api/jobs/{id}/requeue/` | Job `failed`, `dead_lettered` ou `cancelled` ; dispatcher appelé dans la requête HTTP actuelle. |
| `POST /api/jobs/{id}/ignore/` | Tout état ; marque job `cancelled`, raison facultative. |

## Exemples

### Lister les DAT avec OAuth2 (accès API activé requis)

La réponse contient uniquement les DAT visibles pour l'utilisateur authentifié.

```bash
curl \
  -H "Authorization: Bearer $CINTA_ACCESS_TOKEN" \
  -H "Accept: application/json" \
  https://cintafactory.example/api/dats/
```

### Créer une application

```bash
curl \
  -X POST \
  -H "Authorization: Bearer $CINTA_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "code": "paiement-mobile",
    "name": "Paiement Mobile",
    "description": "Application de paiement",
    "business_direction": "00000000-0000-0000-0000-000000000000"
  }' \
  https://cintafactory.example/api/applications/
```

UUID nul ci-dessus est un emplacement d'exemple. Le remplacer par identifiant d'une direction métier existante ; sinon API renvoie une erreur de validation.

### Suivre un job

```bash
curl \
  -H "Authorization: Bearer $CINTA_ACCESS_TOKEN" \
  https://cintafactory.example/api/jobs/00000000-0000-0000-0000-000000000000/
```

Variable `CINTA_ACCESS_TOKEN` doit être injectée localement ; ne jamais écrire jeton réel dans dépôt ou historique shell partagé.

## Formats de réponse

Avec `Accept: application/json`, réponses REST utilisent `application/json`, sauf suppression réussie qui ne contient aucun corps. Sans cet en-tête, négociation de contenu DRF peut aussi sélectionner l'API navigable HTML.

### Collection

Endpoints de liste renvoient directement un tableau JSON. Aucune enveloppe `results` ni pagination globale actuellement.

```json
[
  {
    "id": "2d5d0741-b76c-4d27-bc22-b8c73fd7843f",
    "reference": "DAT-2026-0042",
    "title": "Paiement Mobile",
    "description": "Architecture cible",
    "application": "c7f704f5-457e-4348-b650-69a241b1be1f",
    "status": "en_cours",
    "owner": "65797a87-8121-490f-9291-40c525e55d40",
    "business_direction": "7120045d-29cc-4e68-ae35-aa6cdaba6784",
    "created_at": "2026-08-20T08:15:00Z",
    "updated_at": "2026-08-20T10:42:31Z",
    "pdf_export_in_progress": false,
    "pdf_export_requested_at": null,
    "pdf_export_requested_by": null,
    "pdf_export_requested_by_display": "",
    "pdf_export_path": "",
    "pdf_export_content_type": "application/pdf",
    "pdf_export_size": 0,
    "secure_export_requires_dual_admin_approval": true
  }
]
```

Tableau vide `[]` signifie aucune ressource disponible dans périmètre demandé.

### Ressource unique

Lecture, création et modification renvoient objet sérialisé complet de ressource concernée.

```json
{
  "id": "c7f704f5-457e-4348-b650-69a241b1be1f",
  "code": "paiement-mobile",
  "name": "Paiement Mobile",
  "description": "Application de paiement",
  "business_direction": "7120045d-29cc-4e68-ae35-aa6cdaba6784",
  "created_at": "2026-08-20T08:00:00Z",
  "updated_at": "2026-08-20T08:00:00Z"
}
```

Champs calculés ou en lecture seule apparaissent dans réponse même s'ils ne figurent pas dans requête. Exemple : `id`, dates, direction métier synchronisée ou propriétaire par défaut du DAT.

### Utilisateur

Mot de passe accepté uniquement en écriture. Il n'apparaît jamais dans réponse.

```json
{
  "id": "65797a87-8121-490f-9291-40c525e55d40",
  "username": "jdupont",
  "email": "j.dupont@example.com",
  "first_name": "Jean",
  "last_name": "Dupont",
  "is_active": true,
  "is_staff": false,
  "is_superuser": false,
  "can_use_api": false,
  "role": "df003a29-145f-4c7d-a39e-dcb59d45ee80",
  "business_group": "c3c13a8c-dfaa-46b6-bd7c-e205ade470a5",
  "profile_picture": null,
  "last_login": null,
  "date_joined": "2026-08-20T08:00:00Z"
}
```

### Job asynchrone

Réponse job expose état d'exécution et résultat disponible, sans payload interne ni clé d'idempotence.

```json
{
  "job_id": "04e6d4df-b3ca-4212-9623-cd5a16ce6644",
  "job_type": "exports.pdf",
  "queue_name": "exports.pdf",
  "status": "running",
  "resource_ref": "2d5d0741-b76c-4d27-bc22-b8c73fd7843f",
  "requested_by": "65797a87-8121-490f-9291-40c525e55d40",
  "created_at": "2026-08-20T10:45:00Z",
  "started_at": "2026-08-20T10:45:01Z",
  "finished_at": null,
  "attempt_count": 1,
  "max_attempts": 3,
  "last_error": "",
  "result_payload": {}
}
```

Client suit principalement `status` :

- `queued` : en attente ;
- `running` : traitement actif ;
- `succeeded` : résultat disponible dans `result_payload` ou ressource associée ;
- `failed` : échec ;
- `dead_lettered` : tentatives épuisées ;
- `cancelled` : annulé ou ignoré.

Actions `cancel`, `requeue` et `ignore` renvoient confirmation compacte :

```json
{
  "ok": true,
  "job_id": "04e6d4df-b3ca-4212-9623-cd5a16ce6644",
  "status": "cancelled"
}
```
