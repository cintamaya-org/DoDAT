# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: CC-BY-ND-4.0

# CintaFactory

**CintaFactory** est une plateforme Django destinée à centraliser la gestion des dossiers d'architecture technique, appelés **DAT**. Le projet couvre le cycle de vie complet d'un DAT : création, structuration du contenu, affectation des responsables, validation par workflow, suivi des décisions, exports sécurisés et visualisation de diagrammes.

L'objectif est de fournir un espace commun aux équipes métier, architecture et validation pour préparer, relire et tracer les dossiers d'architecture d'une application.

## Démarrage rapide

1. Clonez le dépôt.
2. Créez un fichier `.env` à la racine du dépôt avec ces variables minimales :

   ```dotenv
   DJANGO_SECRET_KEY=local-demo-django-secret-change-me
   SEAWEEDFS_JWT_WRITE_KEY=local-demo-seaweed-filer-write-key-123456
   SEAWEEDFS_JWT_READ_KEY=local-demo-seaweed-filer-read-key-123456
   SEAWEEDFS_VOLUME_JWT_WRITE_KEY=local-demo-seaweed-volume-write-key-123456
   SEAWEEDFS_VOLUME_JWT_READ_KEY=local-demo-seaweed-volume-read-key-123456
   ```

   Ces valeurs sont prévues pour un lancement local. Les autres variables disponibles sont décrites dans [`.env.exemple`](./.env.exemple) pour personnaliser davantage la configuration.

3. Démarrez les services Docker :

   ```bash
   docker compose -f docker-compose.dev.yml up -d --build
   ```

4. Appliquez les migrations de la base de données :

   ```bash
   docker compose -f docker-compose.dev.yml exec -T web python manage.py migrate
   ```

5. Accédez à l’application dans votre navigateur : <http://localhost:8101>. La page de connexion est aussi accessible à <http://localhost:8101/accounts/login/>.

### Comptes par défaut

Sur une base de données vierge, la migration initiale crée ces comptes :

| Profil | Identifiant |
| --- | --- |
| Administrateur | `super_admin` |
| Porteur de la demande | `porteur_demande_user` |
| Architecte référent | `architecte_referent_user` |
| Architecte technique | `architecte_technique_user` |
| Urbaniste | `urbaniste_user` |
| Analyste sécurité | `analyste_secu_user` |
| RSSI | `rssi_user` |
| Comité de validation | `comite_validation_user` |
| Infrastructure / Exploitation | `infra_exploitation_user` |

Mot de passe par défaut unique, commun à tous les comptes : `123+Aze`. Réservez ces identifiants à l’environnement local; changez-les avant toute exposition de l’application.

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
