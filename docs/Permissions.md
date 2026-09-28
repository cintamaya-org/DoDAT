# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: CC BY-ND 4.0

# Modèle de permissions

**Public visé :** développeurs, administrateurs fonctionnels et auditeurs.  
**Objectif :** décrire les contrôles d'accès actuels sans supposer qu'un rôle d'interface suffit.  
**Sources de vérité :** `dat/models.py`, `dat/permissions.py`, `dat/views.py`, `dat/workflow.py`, `dat/export_access.py`, `workflows/models.py`, `workflows/services.py`, `workflows/notifications.py`, `cintafactory/api/permissions.py`, `cintafactory/api/jobs.py`, `users/api.py`, `dat/api.py` et `diagrams/views.py`.  
**Dernière vérification :** 24 août 2026.

## Règle générale

Autorisation évaluée côté serveur. Masquer un bouton ne constitue jamais un contrôle d'accès.

Le système combine quatre couches :

1. authentification Django ou OAuth2 ;
2. visibilité du DAT ;
3. rôle ou affectation métier ;
4. permission Django par modèle pour l'API REST.

## Identités privilégiées

### Administrateur global

`user_is_dat_admin()` retourne vrai pour :

- `is_superuser=True` ;
- `is_staff=True` ;
- rôle de slug `admin`.

Cet utilisateur contourne le filtre de visibilité DAT. Cela ne signifie pas que chaque vue utilise automatiquement ce helper : chaque opération conserve son contrôle propre.

### Administrateur d'un DAT

`user_is_dat_admin_for_dat()` accepte :

- administrateur global ;
- propriétaire du DAT ;
- utilisateur présent dans `DATAdmin` pour ce DAT.

Cette qualité permet notamment de gérer responsables de section et administrateurs DAT.

### Administrateur DAT explicite

Certaines fonctions sensibles vérifient directement la présence dans `DATAdmin`. L'accès sécurisé aux exports utilise cette définition stricte. Propriétaire, staff ou superutilisateur non inscrit explicitement ne satisfait donc pas nécessairement cette condition.

La présence dans `DATAdmin` n'est toutefois pas une règle de visibilité dans `filter_dat_queryset_for_user()`. Un administrateur DAT explicite qui n'est ni administrateur global, ni propriétaire, ni participant, ni responsable de groupe d'un participant ne voit pas le DAT par ce seul enregistrement et ne peut pas atteindre ses endpoints filtrés.

## Visibilité d'un DAT

Pour un utilisateur non administrateur global, `filter_dat_queryset_for_user()` conserve un DAT si l'une des conditions est vraie :

- utilisateur propriétaire ;
- utilisateur participant du DAT ;
- utilisateur responsable du groupe métier d'un participant.

Utilisateur absent, anonyme ou schéma inattendu : aucun DAT retourné. Ce comportement est volontairement fermé par défaut.

## Matrice fonctionnelle

| Action | Contrôle actuel |
| --- | --- |
| Voir la liste « Mes DAT » | DAT visible selon règles ci-dessus. |
| Créer application/DAT depuis interface métier | Utilisateur authentifié avec rôle `porteur-demande`. |
| Consulter liste/détail du CRUD DAT | Utilisateur authentifié ; queryset filtré par visibilité DAT. |
| Consulter liste/détail du CRUD application | Utilisateur authentifié ; toutes les applications sont actuellement exposées par ce CRUD. |
| Modifier ou supprimer via CRUD de gestion DAT/application | Superutilisateur, staff ou rôle `admin`. La création reste réservée au rôle `porteur-demande`. |
| Utiliser liste administrative DAT, import et tableau de bord | Superutilisateur, staff ou rôle `admin`. |
| Modifier responsables de section | Administrateur du DAT. |
| Modifier participant d'une section | Administrateur du DAT, ou responsable actuel de cette section pour sa ligne éditable. |
| Ajouter/retirer administrateur DAT | Administrateur du DAT ; promotion limitée au propriétaire, aux participants et aux responsables de section proposés par l'éditeur ; propriétaire non retirable. |
| Éditer une section ou sous-section | Responsable ou participant explicitement affecté à cette section ; DAT non final. La section `validation` n'est pas éditable par ce mécanisme. |
| Modifier statut de section | Même contrôle que l'édition de section ; DAT non final. |
| Valider côté responsable | Section explicitement éditable par l'utilisateur, section déjà validée et DAT non final. Le code ne distingue pas ici responsable et participant : l'une ou l'autre affectation explicite suffit. |
| Décider validation/refus/réserve | Administrateur global, architecte référent affecté, ou comité de validation affecté. |
| Déposer/supprimer une pièce jointe | DAT visible, section explicitement éditable, DAT non final. Le dépôt exige aussi que les pièces jointes soient activées pour cette section. |
| Télécharger une pièce jointe | DAT visible et utilisateur authentifié. |
| Lire notifications | Historique des DAT visibles + notifications ciblant cet utilisateur. |

## Affectation de section

`DATSection.can_user_edit()` appelle `user_is_assigned_to_section()`.

Utilisateur considéré affecté uniquement s'il est :

- `DATSectionResponsible.user` ; ou
- `DATSectionParticipant.user`.

Être propriétaire, administrateur global, responsable de groupe ou participant général du DAT ne suffit pas à cette fonction. Un administrateur doit d'abord affecter la personne à la section si l'opération passe par `can_user_edit()`.

La liste `DATSection.allowed_roles` limite les candidats proposés pour les affectations de section et intervient dans le helper de responsabilité de groupe. Elle ne donne pas directement le droit d'édition.

`DATSubSection.allowed_roles` est actuellement synchronisée et exportée, mais `DATSubSection.can_user_edit()` ne la consulte pas : l'autorisation d'une sous-section hérite uniquement de l'affectation explicite de sa section parente. Ne pas considérer cette liste comme un contrôle d'accès effectif.

## Workflow

Les permissions `WorkflowStepPermission` associent une étape à un type `read`/`write` et à au moins un rôle ou utilisateur. Le schéma autorise techniquement les deux champs sur une même ligne, même si la synchronisation normale crée des lignes séparées.

La synchronisation normale aligne ces lignes sur la définition publiée ; le tableau général les affiche. Le tableau « Mes tâches » lit les permissions de la spécification de workflow épinglée à l'instance, pas directement ces lignes. Les transitions métier utilisent encore séparément les rôles, gardes et actions de cette même spécification via `transition_workflow()`. Ne pas utiliser une ligne `WorkflowStepPermission` comme preuve unique d'autorisation.

Voir [Workflow.md](Workflow.md).

## API REST

L'API utilise `GranularModelPermissions` :

| Méthode/action | Permission Django attendue |
| --- | --- |
| `GET` liste/détail utilisateur | `users.view_user` |
| création utilisateur | `users.add_user` |
| modification utilisateur | `users.change_user` |
| suppression utilisateur | `users.delete_user` |
| groupes | Permissions équivalentes sur `users.businessgroup`. |
| applications | Permissions équivalentes sur `dat.application`. |
| DAT | Permissions équivalentes sur `dat.dat`. |

Les endpoints jobs exigent seulement une authentification pour lecture, puis filtrent :

- staff/superutilisateur : tous les jobs ;
- autre utilisateur : jobs dont il est `requested_by` ;
- `cancel`, `requeue`, `ignore` : staff ou superutilisateur.

Contrôle objet des autres ViewSets appelle `user.has_perms(required, obj)`. Backend Django standard ne gère pas les permissions objet ; sans backend complémentaire, détail, modification et suppression peuvent être refusés malgré permission globale. Liste et création utilisent contrôle global sans objet.

### Différence importante entre UI et API

Les querysets REST `applications` et `dats` ne passent actuellement pas par `filter_dat_queryset_for_user()`. Sur les actions de liste, `dat.view_application` expose donc toutes les applications et `dat.view_dat` tous les DAT. Toute évolution API doit choisir explicitement entre permission globale de modèle et visibilité métier par DAT.

`UserSerializer` expose `is_staff` et `is_superuser` en écriture. La garde actuelle est `users.change_user`, sans contrôle de champ distinct. Accorder cette permission à un rôle non superutilisateur peut permettre une élévation de privilèges.

`DATSerializer` laisse également modifiables plusieurs champs internes d'export, notamment `pdf_export_path`, `pdf_export_requested_by` et `secure_export_requires_dual_admin_approval`. La garde REST est `dat.add_dat` globale à la création ou `dat.change_dat` avec le contrôle objet décrit ci-dessus à la modification ; ces permissions doivent donc rester administratives.

## Export sécurisé

Quand `secure_export_requires_dual_admin_approval=True` :

1. un utilisateur auquel le DAT est visible et qui est administrateur DAT explicite crée une demande ;
2. sa propre approbation est enregistrée automatiquement ;
3. un second administrateur DAT explicite auquel le DAT est visible, différent du demandeur, approuve ;
4. les deux approbateurs obtiennent l'accès pendant une heure ;
5. demande en attente expire après cinq minutes ;
6. téléchargements du PDF mis en cache ou du JSON, ainsi que les expirations, sont historisés.

Une personne non approbatrice ne peut pas télécharger le PDF mis en cache ou le JSON, même si la demande a reçu deux approbations. Quand le contrôle est désactivé pour le DAT, ces téléchargements sont autorisés à tout utilisateur ayant déjà accès à la vue d'export.

Limite actuelle importante : `DatGeneratePDFExportView`, exposée par `/export/pdf/generate/` et par l'alias historique `/export/pdf/`, génère et renvoie directement un PDF sans appeler `can_download_export()` et sans historiser le téléchargement. Tout utilisateur authentifié auquel le DAT est visible peut donc contourner le double accord par ces routes. Le déclenchement asynchrone de génération est lui aussi ouvert à tout utilisateur auquel le DAT est visible, mais ne renvoie pas directement le fichier.

## Diagrammes

Les vues draw.io principales filtrent les diagrammes par `owner=self.request.user`. Dans ces vues, chaque utilisateur voit et modifie ses propres diagrammes.

Limite actuelle : `parse_schema_diagram()` accepte des `diagram_ids` puis charge les objets `DrawIODiagram` sans filtre sur `owner`. Un utilisateur autorisé à éditer la section architecture d'un DAT peut donc faire analyser le XML d'un diagramme appartenant à un autre utilisateur s'il connaît son UUID. Cette intégration DAT ne respecte pas l'isolation par propriétaire des vues draw.io principales.

Les proxies draw.io/LikeC4 et callbacks utilisent des contrôles spécifiques : authentification, validation de chemin, allowlist d'hôte ou jeton partagé selon endpoint. Ces règles sont détaillées dans [Security-model.md](Security-model.md).

## Règles pour nouveaux développements

- Filtrer le queryset avant `get_object_or_404()`.
- Vérifier droit objet, pas seulement authentification.
- Refuser par défaut en cas d'utilisateur, relation ou schéma inattendu.
- Ne pas répliquer les règles : réutiliser helpers de `dat.permissions`.
- Ajouter contrôle équivalent aux variantes HTML, AJAX et API.
- Ne jamais accepter rôle, propriétaire ou ID de groupe envoyé par client comme preuve.
- Traiter UUID et slugs comme identifiants, pas comme autorisations.
- Vérifier absence de fuite via recherche, exports, notifications et messages d'erreur.
