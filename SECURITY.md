# Politique de sécurité

Ce document explique comment signaler une vulnérabilité dans DoDat et comment l'équipe la traite.

## Signaler une vulnérabilité

**N'ouvrez pas d'issue publique pour signaler une vulnérabilité.** Utilisez l'un de ces canaux privés :

1. **Signalement privé GitHub (recommandé)** : onglet [Security](https://github.com/cintamaya-org/DoDAT/security) du dépôt, puis « Report a vulnerability ».
2. **Email** : [security@cintamaya.com](mailto:security@cintamaya.com)

Indiquez dans votre signalement :

- une description de la vulnérabilité et de son impact ;
- la version ou le commit concerné, et le composant touché (application Django, service LikeC4, configuration de déploiement…) ;
- les étapes pour la reproduire, ou une preuve de concept minimale ;
- si possible, une correction ou un contournement ;
- comment vous recontacter, et si vous souhaitez être cité.

Les signalements peuvent être rédigés en français ou en anglais.

## Nos engagements

| Étape | Délai |
| --- | --- |
| Accusé de réception | 3 jours ouvrés |
| Première évaluation : confirmation et niveau de gravité | 10 jours ouvrés |
| Correction d'une vulnérabilité critique | 14 jours après confirmation |
| Correction d'une vulnérabilité de gravité haute | 30 jours après confirmation |
| Correction d'une vulnérabilité de gravité moyenne ou faible | 90 jours après confirmation, ou version suivante |

La gravité est évaluée avec le score CVSS. Nous vous tenons informé à chaque étape. Si un délai ne peut pas être tenu, nous vous expliquons pourquoi et proposons une nouvelle échéance.

## Versions supportées

Le projet est en version 0.x. Seule la dernière version publiée reçoit des correctifs de sécurité.

| Version | Correctifs de sécurité |
| --- | --- |
| 0.1.x, dernière version publiée uniquement | Oui |
| Versions antérieures | Non |

Les correctifs sont livrés dans une nouvelle version de correction (par exemple 0.1.4). Déployez toujours la dernière version publiée.

## Divulgation coordonnée

- Ne divulguez pas la vulnérabilité avant la publication du correctif, ou au plus tard 90 jours après votre signalement, sauf accord différent avec nous.
- Une fois la vulnérabilité corrigée, nous publions un avis de sécurité sur GitHub (GitHub Security Advisory), avec un identifiant CVE lorsque c'est pertinent.
- Avec votre accord, nous vous citons dans l'avis.
- Nous ne proposons pas de récompense financière.

## Périmètre

**Dans le périmètre :**

- le code de ce dépôt : application Django (`cintafactory/`) et service LikeC4 (`likec4/`) ;
- les Dockerfiles, fichiers Docker Compose et scripts de déploiement fournis (`deploy/`) ;
- la configuration par défaut livrée avec le projet.

**Hors périmètre :**

- les vulnérabilités des composants tiers (Django, draw.io, LikeC4, SeaweedFS, ClamAV, PostgreSQL…) : signalez-les à leurs mainteneurs. Une mauvaise intégration de ces composants dans DoDat reste dans le périmètre ;
- les instances de DoDat exploitées par d'autres organisations : contactez leur exploitant ;
- les tests de déni de service ou de charge, l'ingénierie sociale et les attaques physiques ;
- les résultats d'outils automatiques sans démonstration d'un impact réel ;
- les limites déjà documentées dans le [modèle de sécurité](docs/Security-model.md) (sections « Limite API connue » et « Limite de couverture connue »), sauf si vous démontrez un impact plus large que celui décrit.

## Règles pour les tests

Testez sur une instance locale (voir le [guide de démarrage](QUICKSTART.md)). N'effectuez aucun test sur l'instance de démonstration ni sur une instance exploitée par un tiers sans autorisation écrite.

Nous considérons comme menée de bonne foi toute recherche qui :

- se limite au strict nécessaire pour démontrer la vulnérabilité ;
- n'accède pas aux données d'autres personnes, ne les modifie pas et ne les conserve pas ;
- ne dégrade pas la disponibilité d'un service ;
- respecte la confidentialité jusqu'à la publication du correctif.

Nous n'engagerons pas de poursuites contre une personne qui respecte ces règles.

Vous pouvez aussi signaler une vulnérabilité à l'[ANSSI](https://cyber.gouv.fr/). L'article L. 2321-4 du code de la défense protège les personnes de bonne foi qui lui transmettent une telle information.

## Documentation de sécurité

- [Modèle de sécurité](docs/Security-model.md) : actifs protégés, frontières de confiance, mesures en place et limites connues.
- [Modèle de permissions](docs/Permissions.md) : rôles, droits et règles d'accès aux DAT.
