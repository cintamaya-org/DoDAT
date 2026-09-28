# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: CC BY-ND 4.0

# CintaFactory

**CintaFactory** est une plateforme Django destinée à centraliser la gestion des dossiers d'architecture technique, appelés **DAT**. Le projet couvre le cycle de vie complet d'un DAT : création, structuration du contenu, affectation des responsables, validation par workflow, suivi des décisions, exports sécurisés et visualisation de diagrammes.

L'objectif est de fournir un espace commun aux équipes métier, architecture et validation pour préparer, relire et tracer les dossiers d'architecture d'une application.

## Quick start

1. Clone the repository.
2. Populate `.env` like `.env.exemple`

3. Start Docker:

   ```bash
   docker compose -f docker-compose.dev.yml up -d --build
   ```

4. Apply DB migrations:

   ```bash
   docker compose -f docker-compose.dev.yml exec -T web python manage.py migrate
   docker compose -f docker-compose.dev.yml exec -T web python manage.py makemigrations
   ```

## Ce que permet le projet

- Gérer les **applications** et leurs rattachements aux directions métier.
- Créer et suivre des **DAT** avec statut, propriétaire, participants et historique.
- Organiser chaque DAT en **sections et sous-sections** configurables.
- Affecter des **rôles** et des responsables selon les directions techniques et métier.
- Piloter les validations via un **workflow DAT** : nouvelle demande, en cours, en attente de revue, réserve, validation ou refus.
- Suivre les tâches, notifications et changements d'état depuis des vues de travail.
- Produire des **exports PDF et JSON** des DAT avec contrôle d'accès renforcé.
- Intégrer des diagrammes **draw.io** et **LikeC4** pour documenter l'architecture.
- Exposer des endpoints de santé, métriques et tableaux de bord d'observabilité.

## Modules principaux

| Module | Rôle |
| --- | --- |
| `dat` | Gestion des DAT, applications, sections, participants, historique, exports et import. |
| `workflows` | Définition et synchronisation des étapes de validation, tableaux de suivi et notifications. |
| `users` | Utilisateurs, rôles, directions techniques, directions métier et groupes. |
| `diagrams` | Édition, import, export et rendu de diagrammes draw.io et LikeC4. |
| `configuration` | Écrans et paramètres de configuration applicative. |
| `cintafactory` | Projet Django principal, API, santé, métriques, middleware et tâches asynchrones. |

## Parcours fonctionnel

1. Une application est déclarée avec sa direction métier.
2. Un DAT est créé pour cette application.
3. Les participants et responsables sont associés au dossier.
4. Les sections du DAT sont complétées avec textes, pièces jointes et diagrammes.
5. Le DAT avance dans le workflow de validation.
6. Les décisions, réserves et modifications sont historisées.
7. Le dossier peut être exporté en PDF ou JSON selon les règles d'accès.

## Stack technique

| Composant | Technologie |
| --- | --- |
| Langage | Python 3.12+ |
| Framework web | Django 5.2 |
| API | Django REST Framework, drf-spectacular |
| Base de données | PostgreSQL |
| Authentification | Django auth, OAuth Toolkit |
| Workflow et UI | django-viewflow, django-material |
| Exports | WeasyPrint, JSON |
| Diagrammes | draw.io, LikeC4 |
| Déploiement local | Docker Compose |
| Observabilité | Prometheus, Grafana, Loki, Promtail, cAdvisor |

## Documentation utile

- [`README_old.md`](./README_old.md) : ancien guide général, installation et commandes principales.
- [`README_dev.md`](./README_dev.md) : guide développeur par packs Docker Compose.
- [`README_MONITORING.md`](./README_MONITORING.md) : supervision, métriques, logs et dashboards.
- [`README_LOG.md`](./README_LOG.md) : informations liées aux logs.
- [`deploy/`](./deploy) : scripts et fichiers de déploiement.
- [`params_dev/`](./params_dev) : runbooks et notes techniques de développement.

## Points d'entrée applicatifs

Les routes principales sont servies par le projet Django :

- `/accounts/login/` : connexion.
- `/dat/` : gestion des DAT.
- `/workflows/` : tableaux de validation et tâches.
- `/diagrams/` : diagrammes draw.io et LikeC4.
- `/api/docs/` : documentation Swagger de l'API.
- `/health/live` et `/health/ready` : santé applicative.
- `/metrics` : métriques Prometheus.

## Licence

Le projet est distribué sous licence **AGPL-3.0**. Voir [`LICENSE`](./LICENSE) pour le texte complet.
