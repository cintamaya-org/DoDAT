# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: CC BY-ND 4.0

# API CintaFactory

**Public visé :** développeurs d'intégration et mainteneurs backend.  
**Objectif :** expliquer conventions, authentification, ressources et permissions de l'API REST.  
**Sources de vérité :** schéma OpenAPI généré, `cintafactory/api/urls.py`, `users/api.py`, `dat/api.py` et `cintafactory/api/jobs.py`.  
**Dernière vérification :** 20 août 2026.

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

Adaptée au navigateur et à Swagger UI. Les requêtes modifiant des données exigent un token CSRF valide.

### OAuth2

Adapté aux clients externes. Envoyer le jeton :

```http
Authorization: Bearer <access-token>
```

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

Les ressources modifiables utilisent les permissions Django par action :

| Action | Permission |
| --- | --- |
| Liste/détail | `<app>.view_<model>` |
| Création | `<app>.add_<model>` |
| Remplacement/modification | `<app>.change_<model>` |
| Suppression | `<app>.delete_<model>` |

Utilisateur doit être authentifié et posséder toutes les permissions demandées.

Attention : les endpoints DAT et applications exposent actuellement leur queryset complet aux utilisateurs possédant la permission de modèle. Ils n'appliquent pas le filtre de visibilité métier utilisé par « Mes DAT ». Voir [Permissions.md](Permissions.md).

Pour les actions portant sur un objet, `GranularModelPermissions` appelle `user.has_perms(..., obj)`. Le backend Django standard ne fournit pas de permissions objet. Sans backend dédié, une permission globale de modèle peut donc suffire à la liste mais pas au détail, à la modification ou à la suppression. Vérifier ce comportement dans l'environnement cible avant publier l'API.

## Ressources

### Utilisateurs — `/api/users/`

Méthodes : `GET`, `POST`, `PUT`, `PATCH`, `DELETE`.

Champs :

- `id`, `username`, `email`, `first_name`, `last_name` ;
- `is_active`, `is_staff`, `is_superuser` ;
- `role`, `business_group`, `profile_picture` ;
- `last_login`, `date_joined` en lecture seule ;
- `password` facultatif en écriture seule.

Créer ou modifier un mot de passe utilise `set_password()` ; valeur jamais renvoyée. Les champs privilégiés exigent une attention particulière côté clients d'administration.

`is_staff` et `is_superuser` sont actuellement modifiables avec la seule permission `users.change_user`. Ne pas accorder cette permission à un administrateur limité tant qu'une règle de champ empêche explicitement l'élévation de privilèges.

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

`id`, `business_direction`, `created_at` et `updated_at` sont en lecture seule. À la création, si `owner` est omis ou explicitement `null`, utilisateur authentifié devient propriétaire. La direction métier est recalculée depuis l'application lors de l'enregistrement.

Attention : tous les champs d'export listés ci-dessus sont actuellement modifiables par `POST`, `PUT` ou `PATCH`, notamment `pdf_export_path`, `pdf_export_requested_by` et `secure_export_requires_dual_admin_approval`. La seule garde API est la permission DAT correspondant à l'action, complétée par le contrôle objet décrit plus haut. Considérer `dat.add_dat` et `dat.change_dat` comme permissions administratives tant que ces champs ne sont pas placés en lecture seule ou protégés par une autorisation spécifique.

L'API CRUD DAT ne réalise pas les transitions détaillées de l'interface de validation. Un client modifiant directement `status` doit être considéré comme client administratif et maintenir les invariants décrits dans [Workflow.md](Workflow.md).

### Jobs — `/api/jobs/`

Lecture seule pour utilisateur authentifié :

- `GET /api/jobs/` ;
- `GET /api/jobs/{job_id}/` ;
- filtre facultatif `?resource_ref=<valeur>`.

Visibilité :

- utilisateur normal : uniquement ses jobs ;
- staff ou superutilisateur : tous les jobs.

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

### Lister les DAT avec OAuth2

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
