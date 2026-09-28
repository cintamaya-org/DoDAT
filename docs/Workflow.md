# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: CC BY-ND 4.0

# Personnaliser le workflow

Guide développeur pour modifier `dat-validation` sans casser les DAT existants.

## Fichiers utiles

| Besoin | Fichier |
| --- | --- |
| États, transitions, capacités, permissions, diagramme | `cintafactory/workflows/definitions.py` |
| Gardes et actions DAT | `cintafactory/dat/workflow.py` |
| API d'exécution | `cintafactory/workflows/services.py` |
| Validation avant publication | `cintafactory/workflows/validation.py` |

## Règles du système

- Chaque objet possède un `WorkflowInstance` épinglé à une version immuable.
- Publier une définition ne modifie pas les instances existantes.
- `WorkflowInstance.current_state` est autoritatif ; `DAT.status` est une projection héritée.
- Toute progression passe par `transition_workflow()`.
- Toute transition réussie crée un `WorkflowTransitionEvent`.
- Les vues doivent tester des capacités (`terminal`, `reviewable`, etc.), pas des codes d'états.
- Un code de workflow reste lié à son modèle d'origine. Pour cibler un autre modèle, créer un nouveau code au lieu de modifier `model`.

## État et transition

État :

```python
WorkflowStepDefinition(
    key="en-attente-revue",
    name="En attente de revue",
    status="en_attente_de_revue",
    order=30,
    lane="in_progress",  # initial | in_progress | completed
    capabilities=("editable", "reviewable"),
    permissions=(
        StepPermissionDefinition(
            permission="write",
            roles=("architecte-referent", "comite-validation"),
        ),
        StepPermissionDefinition(permission="read", roles=ALL_DAT_ROLES),
    ),
)
```

Transition :

```python
WorkflowTransitionDefinition(
    event="approve",
    sources=("en_attente_de_revue",),
    target="valider",
    guard="always",
    action="",
    roles=("architecte-referent", "comite-validation"),
    order=10,
)
```

`guard` vérifie une condition. `action` exécute un effet métier. Ces noms doivent être implémentés par l'adaptateur.

Pour un même couple `(event, source)`, chaque transition doit avoir un `order` différent. Deux routes de même priorité sont ambiguës et la publication est rejetée.

## Exemple 1 — Ajouter une revue sécurité

Ajouter l'état dans `steps` :

```python
WorkflowStepDefinition(
    key="revue-securite",
    name="Revue sécurité",
    status="revue_securite",
    order=25,
    lane="in_progress",
    capabilities=("editable", "security_reviewable"),
    permissions=(
        StepPermissionDefinition(
            permission="write",
            roles=("rssi", "analyste-secu"),
        ),
        StepPermissionDefinition(permission="read", roles=ALL_DAT_ROLES),
    ),
)
```

Remplacer le passage direct `en_cours → en_attente_de_revue` par :

```python
WorkflowTransitionDefinition(
    event="sections_changed",
    sources=("en_cours",),
    target="revue_securite",
    guard="all_sections_validated",
    automatic=True,
),
WorkflowTransitionDefinition(
    event="security_approved",
    sources=("revue_securite",),
    target="en_attente_de_revue",
    roles=("rssi", "analyste-secu"),
),
```

Déclencher depuis une vue :

```python
if workflow_can(dat, "security_approved", request.user):
    transition_workflow(dat, "security_approved", request.user)
```

Attention : retirer `en_cours` des sources de l'ancienne transition directe, sinon deux transitions concurrentes répondront à `sections_changed`.

## Exemple 2 — Ajouter une garde

Dans la définition :

```python
WorkflowTransitionDefinition(
    event="approve",
    sources=("en_attente_de_revue",),
    target="valider",
    guard="security_review_complete",
    roles=("comite-validation",),
)
```

Dans `DATWorkflowAdapter` :

```python
guards = frozenset(
    {
        # Gardes existantes...
        "security_review_complete",
    }
)

def evaluate_guard(self, name, obj, *, actor=None, context=None):
    if name == "security_review_complete":
        return obj.security_reviews.filter(is_approved=True).exists()
    # Conserver le traitement des autres gardes.
```

Une garde lit des faits et renvoie un booléen. Aucun effet de bord.

## Exemple 3 — Ajouter une action

Dans une transition :

```python
WorkflowTransitionDefinition(
    event="start_security_review",
    sources=("en_cours",),
    target="revue_securite",
    action="prepare_security_review",
    roles=("rssi", "analyste-secu"),
)
```

Dans l'adaptateur :

```python
actions = frozenset({"reset_section_statuses", "prepare_security_review"})

def perform_action(self, name, obj, *, actor=None, context=None):
    if name == "prepare_security_review":
        obj.security_reviews.get_or_create(created_by=actor)
        return
    # Conserver le traitement des autres actions.
```

L'action s'exécute dans la transaction de transition. Une exception annule état, action et audit.

## Exemple 4 — Ajouter une capacité ou changer un rôle

Capacité `exportable` sur les états autorisés :

```python
capabilities=("terminal", "approved", "exportable")
```

Consommation côté application :

```python
show_export = workflow_has_capability(dat, "exportable")
```

Pour réserver l'approbation au comité :

```python
roles=("comite-validation",)
```

Le rôle doit exister en base. Les instances anciennes conservent les permissions de leur version épinglée.

## Exemple 5 — Renommer un état

Après remplacement de `reserve` par `corrections_requises`, publier la nouvelle version, puis simuler :

```bash
python manage.py migrate_workflow_instances dat-validation \
  --map reserve=corrections_requises
```

Migrer d'abord une instance pilote :

```bash
python manage.py migrate_workflow_instances dat-validation \
  --map reserve=corrections_requises \
  --object-id UUID_DU_DAT \
  --apply
```

Puis migrer le reste :

```bash
python manage.py migrate_workflow_instances dat-validation \
  --map reserve=corrections_requises \
  --apply
```

Sans `--apply`, commande effectue toujours une simulation avec rollback.

Avant une migration globale, vérifier les compteurs affichés par la simulation. Le pilote doit normalement indiquer `examined=1, migrated=1`. Ne pas utiliser `--apply` si `examined` ou `migrated` ne correspond pas au périmètre attendu.

## Exemple 6 — Modifier le diagramme

Le diagramme se trouve dans `WorkflowDefinition.visualization` et appartient à la version :

```python
{
    "id": "security-review",
    "title": "Revue sécurité",
    "variant": "mid",
    "row": 1,
    "col": 1,
    "links": ["validation"],
    "scope": "section",
    "section": "cybersecurite",
}
```

Contraintes : `id` unique, liens vers des nœuds existants, champ `section` obligatoire pour scope `section`.

## API à utiliser

```python
from workflows.services import (
    available_workflow_actions,
    transition_workflow,
    workflow_has_capability,
    workflow_state,
)

state = workflow_state(dat)
actions = available_workflow_actions(dat, request.user)

if workflow_has_capability(dat, "terminal"):
    raise PermissionDenied

transition_workflow(dat, "approve", request.user)
```

Un client REST ne doit jamais modifier `status` avec `POST`, `PUT` ou `PATCH`. Une modification directe désynchronise `DAT.status` de `WorkflowInstance.current_state` et ne crée aucun événement d'audit. Exposer une transition métier qui appelle `transition_workflow()` à la place.

Pour un recalcul automatique où l'absence de transition est acceptable :

```python
transition_workflow(
    dat,
    "sections_changed",
    request.user,
    context={"status_map": status_map},
    strict=False,
)
```

## Valider et publier

Les commandes suivantes supposent soit un terminal ouvert dans le dossier local `cintafactory/` contenant `manage.py`, soit le conteneur `web`, où le dossier de travail est `/app`. Depuis la racine du dépôt, utiliser par exemple `docker compose -f docker-compose.dev.yml exec -T web python manage.py ...`.

```bash
# Valider sans écrire
python manage.py sync_workflows --check

# Tester
python manage.py test workflows --keepdb --noinput

# Publier une nouvelle version
python manage.py sync_workflows
```

Checklist :

1. modifier définition et adaptateur ;
2. tester nouveaux états, rôles, gardes, actions et audit ;
3. exécuter `sync_workflows --check` ;
4. publier ;
5. vérifier une nouvelle instance ;
6. simuler toute migration d'instances avant `--apply`.

## À ne jamais faire

- Modifier une version publiée.
- Écrire directement `WorkflowInstance.current_state` ou `DAT.status`, y compris depuis l'API REST.
- Modifier manuellement `WorkflowStep` ou `WorkflowStepPermission`.
- Mettre des effets de bord dans une garde.
- Vérifier une autorisation uniquement côté client.
- Migrer automatiquement toutes les instances pendant une publication.
