<!--
SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
SPDX-License-Identifier: CC-BY-ND-4.0
-->

# Personnaliser les approbations des DAT

Le circuit est un graphe d'approbation défini en JSON, versionné en base. Chaque DAT utilise une version épinglée. Les étapes peuvent avancer en parallèle ; l'état du DAT est une synthèse, pas le moteur du circuit.

Le circuit livré par défaut est `cintafactory/workflows/config/dat-validation.json`. Il suit le parcours besoin → prérequis → préparation → urbanisation et cybersécurité en parallèle → première version du DAT → validation SSI → hébergement/exploitation → validation finale. Le jalon « Première version du DAT » se termine automatiquement après les deux approbations parallèles. Dans ce circuit, un refus clôture le DAT. Une demande de correction aux prérequis renvoie au besoin, et une demande de correction SSI renvoie à la préparation.

La variante à huit étapes `dat-validation-refusal-routes.json` applique les règles de refus suivantes.

| Étape | Effet du refus |
| --- | --- |
| Nouveau besoin | Soumission uniquement. |
| Contrôle des prérequis | Retour au nouveau besoin. |
| Préparation du dossier | Étape bloquée jusqu’à sa reprise par l’architecte technique affecté. |
| Urbanisation, volet cybersécurité ou validation SSI | Retour à la préparation, puis reprise des deux revues parallèles. |
| Hébergement, exploitation et capacité | Retour à la validation SSI. Les deux approbations parallèles restent valides. |
| Validation du DAT complet | Clôture du DAT au statut Refusé. |

Dans le circuit **Revue parallèle architecture et sécurité**, un refus à la validation finale renvoie au besoin. Les rôles hébergement et validation finale du circuit par défaut sont respectivement `infra-exploitation` et `comite-validation`.

## Édition et approbation

- Les affectations de responsable et de participant de section continuent à décider qui peut modifier son contenu.
- Une affectation de rôle sur le DAT décide qui peut approuver les étapes demandant ce rôle. Un approbateur peut ne disposer d'aucun droit d'édition de la section.
- Modifier le circuit ou remplacer un approbateur ne modifie jamais les affectations d'édition.
- Un éditeur affecté à une section conserve la visibilité de son DAT, même si son rôle d'approbation est réaffecté.
- Un administrateur ne remplace pas implicitement un approbateur. Il doit être affecté au rôle requis sur le DAT.
- Le modèle actuel conserve une personne par rôle et un rôle par personne sur un DAT. Ce même rôle peut approuver plusieurs étapes.
- La clôture continue à empêcher les modifications des sections. Les droits existants d'administration des métadonnées du DAT sont conservés ; une modification d'un contenu déjà approuvé réouvre les approbations qui le couvrent.
- Les anciens champs de suivi `statut` et `statut_responsable` ne donnent aucune approbation et ne font plus avancer le circuit.

## Bibliothèque et sélection du workflow actif

La bibliothèque contient un fichier par workflow et un sélecteur `workflows.json` dans le même dossier :

```text
workflows/config/
├── workflows.json          # Sélection active
├── dat-validation.json     # Circuit actif par défaut, neuf étapes
├── dat-validation-refusal-routes.json  # Variante à huit étapes avec retours sur refus
└── parallel-review.json    # Besoin → architecture et sécurité en parallèle → validation finale
```

Pour sélectionner la revue parallèle, modifier `workflows.json` :

```json
{
  "active_workflow": "parallel-review.json"
}
```

Pour ajouter un workflow, copier un fichier existant dans ce dossier, adapter son `name`, ses étapes, ses rôles et ses cibles, puis renseigner son nom de fichier dans `active_workflow`. Chaque fichier conserve le code applicatif `dat-validation` : les fichiers représentent les circuits possibles pour les DAT, tandis que ce code maintient leur lien avec les versions et les historiques en base. Les fichiers non sélectionnés ne sont ni chargés ni publiés.

Pour conserver une bibliothèque locale hors des fichiers livrés, depuis `cintafactory/` :

```bash
mkdir -p conf
cp -n workflows/config/*.json conf/
```

Modifier ensuite `conf/workflows.json` et les fichiers de workflow de ce dossier. Le chargeur utilise, dans cet ordre, un chemin explicite `--json`, l'ancienne variable `DAT_WORKFLOW_JSON`, le sélecteur indiqué par `DAT_WORKFLOW_CONFIG`, `conf/workflows.json` s'il existe, puis le sélecteur livré `workflows/config/workflows.json`. Laisser `DAT_WORKFLOW_JSON` vide pour utiliser la bibliothèque. `DAT_WORKFLOW_CONFIG=/chemin/bibliotheque/workflows.json` permet de choisir un autre dossier ; les chemins de workflow restent relatifs au dossier du sélecteur, quel que soit le répertoire courant du processus.

Le sélecteur n'accepte que `active_workflow`, contenant un nom de fichier JSON sans chemin. Un fichier absent, invalide, contenant des clés dupliquées ou sortant du dossier via un lien symbolique produit une erreur ; aucun autre circuit ne le remplace silencieusement. La lecture et `--check` ne créent aucun fichier de configuration.

Après modification, exécuter `python manage.py sync_workflows --check` pour connaître la version cible et le nombre de DAT ouverts à redémarrer. Exécuter ensuite `python manage.py sync_workflows` pour publier. Une seule version est active pour les nouveaux DAT. Les DAT ouverts sur une autre version reprennent au début, avec de nouvelles approbations. Les anciennes décisions restent consultables dans leur historique. Les DAT clôturés conservent leur circuit et leur statut. Si une modification autorisée du contenu approuvé rouvre ensuite un DAT sur un ancien circuit, il redémarre sur la version active. Un retour à un fichier déjà publié réutilise sa version. Répéter la commande avec la même sélection conserve les approbations en cours sur cette version.

## Contrat JSON

La racine accepte `schema_version: 2`, `engine_version: 1`, `code`, `name`, `policies`, `nodes` et `complete_when`. Le code applicatif est `dat-validation` ; personnaliser le nom et les étapes sous ce code afin de conserver le lien avec les DAT et leurs historiques.

Une étape possède un `id` stable, un `name` facultatif, un `type`, un `target`, une liste `after` facultative et, pour une approbation, des `approvers`.

| Type | Comportement |
| --- | --- |
| `submission` | Un éditeur autorisé soumet le contenu. Aucun droit d'édition supplémentaire n'est créé. |
| `approval` | Les personnes affectées aux rôles requis prennent une décision. |
| `milestone` | Enregistre automatiquement une version lorsque toutes les dépendances sont terminées. Ce jalon historique n'est pas une approbation de modifications futures. |

Cibles utilisables :

```json
{"dat": true}
{"section": "architecture"}
{"section": "architecture", "subsection": "flux"}
{"section": "architecture", "subsection": "flux", "part": "flux"}
```

Les identifiants sont ceux des blueprints, pas les UUID d'un DAT particulier. Les cibles sont résolues dans le DAT courant. La section technique `validation` ne peut pas être une cible. Les autres sections, notamment `besoins` et `informations-generales`, peuvent être approuvées.

Exemple d'approbations parallèles sur la même section :

```json
{
  "schema_version": 2,
  "engine_version": 1,
  "code": "dat-validation",
  "name": "Double revue",
  "nodes": [
    {
      "id": "review",
      "name": "Revue architecture et sécurité",
      "type": "approval",
      "target": {"section": "architecture"},
      "approvers": {"all_roles": ["architecte-referent", "rssi"]}
    },
    {
      "id": "final",
      "type": "approval",
      "target": {"dat": true},
      "after": ["review"],
      "approvers": {"any_role": ["comite-validation", "urbaniste"]},
      "on_request_changes": {"restart_at": "review"}
    }
  ],
  "complete_when": {"all": ["final"]}
}
```

`all_roles` exige une décision positive de chaque rôle. `any_role` exige une décision positive d'un des rôles disponibles. Les rôles doivent exister lors de la publication. Un rôle requis sans personne active affectée au DAT bloque l'étape ; avec `any_role`, au moins un des rôles doit avoir une personne active.

`"approvers": {"section_referent": true}` utilise la personne affectée dans **Responsables des sections**. Cette option cible une section configurable, une de ses sous-sections ou un de ses champs. La variable globale `DAT_WORKFLOW_REQUIRE_SECTION_REFERENTS` vaut `0` par défaut. Le circuit ignore alors ces étapes dès que leurs dépendances sont satisfaites. Avec `1`, la section doit avoir un référent actif et ce référent doit approuver l'étape. La règle s'applique aux DAT actifs dont la version contient un tel jalon. Une réaffectation renouvelle la revue et ses étapes dépendantes. Les approbations `all_roles` et `any_role` suivent leurs propres exigences.

`after` est une jointure ET : toutes les étapes listées doivent être terminées dans leur tentative courante. Plusieurs étapes ayant la même dépendance deviennent disponibles ensemble. `complete_when.all` déclare les étapes nécessaires à la clôture. Toutes les branches doivent contribuer à cette clôture ; aucune approbation indépendante n'est ignorée.

`on_request_changes.restart_at` désigne l'étape courante ou une de ses ancêtres. La demande crée de nouvelles tentatives pour cette étape et ses descendants. Sans route, elle reprend à l'étape courante. Un commentaire est obligatoire pour une demande de corrections ou un refus.

### Comportement après refus

| `on_reject` d'une étape d'approbation | Effet |
| --- | --- |
| `{"mode": "return", "restart_at": "need"}` | Reprend à une étape antérieure dont dépend la revue. Un lien de retour est ajouté à la revue après le refus et reste visible dans le circuit. |
| `{"mode": "block", "retry_roles": ["architecte-technique"]}` | Bloque l'étape et ses dépendants. Une personne active affectée sur ce DAT à l'un des rôles listés doit lancer la reprise. |
| `{"mode": "close"}` ou clé omise | Clôt le DAT au statut Refusé et annule les étapes restantes. |

Un retour ou une reprise renouvelle les tentatives du périmètre concerné. Les branches indépendantes et les décisions historiques sont conservées. Un blocage explicite persiste après une modification du contenu ou des affectations. Les rôles de reprise doivent exister lors de la publication.

## Conditions et blocages

`ready_when` compose les opérateurs `all`, `any` et `not` avec les faits autorisés :

```json
{
  "all": [
    {"required_content_complete": true},
    {"no_open_reserves": true}
  ]
}
```

`required_content_complete` exige une valeur non vide pour les champs obligatoires de la cible ; `false` et `0` sont des valeurs renseignées. `no_open_reserves` lit les réserves de section persistées. Ces conditions n'exécutent jamais de code provenant du JSON.

Les blocages sont structurés : `dependency_pending`, `missing_assignee`, `missing_section_referent`, `target_missing`, `readiness_failed`, `snapshot_unavailable`, `refusal_blocked` et `workflow_rejected`. Les rôles restant à approuver sont également exposés. L'interface affiche les personnes, les étapes précédentes et les commentaires.

Les politiques actuelles sont `missing_assignee: "block"`, `on_content_change: "invalidate_affected"` et `request_changes_requires_comment: true`. Les valeurs non prises en charge et les clés inconnues sont rejetées. Le circuit ne configure pas les permissions d'édition.

## Modifications et preuves

Chaque activation possède un numéro de tentative et une copie immuable du contenu revu. Les décisions indiquent l'acteur, son affectation de rôle, le commentaire et cette copie. Une modification du contenu ou une réaffectation de rôle invalide les tentatives concernées et leurs descendants. Une modification d'un autre champ n'invalide pas une approbation portant sur un champ précis.

Les pièces jointes et sources de diagrammes couvertes par une revue sont copiées sous `workflow-snapshots/<DAT>/<sha256>` dans le stockage existant. La copie reste consultable après suppression ou remplacement de l'original ; les contrôles de visibilité du DAT s'appliquent aussi à ces téléchargements. Les sauvegardes du stockage doivent inclure ce préfixe.

Les approbations d'une section couvrent ses champs, pièces jointes et diagrammes. Une cible de sous-section ou de champ couvre ses valeurs ; les fichiers attachés à la section sont examinés par une cible de section ou de DAT.

Les limites sont de 100 étapes, 100 identifiants par liste, 12 niveaux d'expressions, 1 Mio de configuration, 128 fichiers par copie et 64 Mio de fichiers par copie (`WORKFLOW_SNAPSHOT_MAX_BYTES` pour adapter cette dernière limite). La préparation de copies possède un budget de 15 secondes par réconciliation et des appels de stockage limités à 5 secondes. Si une source manque ou ne peut pas être conservée, l'étape attend ; elle n'est jamais approuvée implicitement.

Les écritures applicatives des formulaires, imports, pièces jointes, affectations et diagrammes se sérialisent avec les décisions sur l'instance du circuit. Les écritures SQL directes et les modifications de fichiers hors des services applicatifs ne constituent pas des API de modification prises en charge. Ne pas utiliser `QuerySet.update`, `bulk_update` ou `bulk_create` pour modifier du contenu approuvé : utiliser les méthodes de modèles et services suivis.

## Validation, publication et bascule

Depuis `cintafactory/`, avec les dépendances et la base habituelles du projet :

```bash
python manage.py migrate
python manage.py sync_workflows --check
python manage.py test workflows dat.tests_workflow dat.tests_sections diagrams.tests_access_security --noinput
python manage.py sync_workflows
```

`--check` valide et simule la publication dans une transaction annulée. Les rôles et les cibles sont vérifiés avant publication. Les cycles, dépendances inconnues, routes de correction non valides, branches ignorées, conditions inconnues et instructions exécutables sont rejetés.

Après migration, la sélection sert uniquement à initialiser une installation sans version publiée. Un changement de sélection sur une installation existante prend effet lors de la publication explicite. Pour publier ponctuellement un fichier extérieur à la bibliothèque, utiliser `sync_workflows --json /chemin/dat-validation.json`, avec `--check` pour le valider sans conserver les écritures. Cette surcharge ne modifie pas le sélecteur ; la prochaine synchronisation sans `--json` utilisera à nouveau la sélection configurée. Modifier un fichier sans le publier ne modifie aucune instance.

La publication enregistre le changement de version et le redémarrage des DAT ouverts dans une même transaction. Chaque redémarrage conserve le contenu et les affectations, archive les tentatives précédentes et ajoute un événement `workflow-restarted` avec les versions de départ et d’arrivée.

## API de revue

- `GET /workflows/dat/<DAT>/review/` : circuit et historique. Ajouter `?format=json` pour obtenir notamment `refusal_links` et `can_retry` par étape. Chaque lien indique sa source, sa destination, la dernière décision de refus et si une reprise à la destination est attendue.
- `POST /workflows/dat/<DAT>/review/<node>/` : `outcome` (`submit`, `approve`, `request_changes`, `reject`, `retry`), `attempt_id`, `request_id` UUID et `comment`. Un commentaire est requis pour `request_changes` et `reject`.
- `GET /workflows/dat/<DAT>/snapshots/<snapshot>/` : contenu présenté ; ajouter `?format=json` pour les données.
- `GET /workflows/approbations/` : approbations des DAT auxquels l'utilisateur est affecté.

Les sessions HTML et les clients JSON utilisent les mêmes contrôles serveur et la protection CSRF habituelle. L'identité et le rôle de l'approbateur sont déterminés côté serveur. `attempt_id` protège contre une page devenue périmée ; une tentative remplacée renvoie HTTP 409. Réutiliser `request_id` pour réessayer exactement la même décision ne la compte pas deux fois. Le CRUD REST conserve `status` en lecture seule ; les anciens événements `approve` sans étape ne peuvent pas approuver le circuit.

## Fichiers du système

| Fichier | Responsabilité |
| --- | --- |
| `workflows/config/workflows.json` | Sélecteur livré de la bibliothèque |
| `conf/workflows.json` | Sélecteur local facultatif, prioritaire sur celui livré |
| `workflows/config/dat-validation.json` | Circuit complet livré |
| `workflows/config/dat-validation-refusal-routes.json` | Variante à huit étapes avec retours sur refus |
| `workflows/config/parallel-review.json` | Circuit de revue parallèle simplifié |
| `workflows/graph_schema.py` | Contrat, validation et dépendances |
| `workflows/graph_config.py` | Chargement, publication et références |
| `workflows/graph_engine.py` | Tentatives, décisions, corrections et synthèse |
| `workflows/graph_targets.py` | Résolution des cibles et faits de contenu |
| `workflows/graph_artifacts.py` | Conservation des versions de fichiers |
| `workflows/tracking.py` | Frontière des écritures applicatives |
| `workflows/graph_views.py` | Interface et API de revue |
| `workflows/tests_graph.py` | Acceptation et concurrence |
| `workflows/tests_library.py` | Sélection, publication et conservation des versions épinglées |

Les anciennes définitions Python et lignes `WorkflowStepPermission` ne définissent plus les approbateurs des nouveaux circuits.
