# DoDat

## La plateforme qui simplifie vos dossiers d’architecture

DoDat centralise la gestion des **Dossiers d’Architecture Technique (DAT)**.

Elle réunit les équipes métier, architecture et projet dans un espace commun pour transformer des informations dispersées en décisions claires, documentées et traçables.

## Démarrage

Consultez le [guide de démarrage](./QUICKSTART.md) pour installer et lancer la plateforme.

Pour les équipes techniques :

- [Guide développeur](./README_dev.md)
- [Licence AGPL-3.0](./LICENSE)

## Compatibilité du renommage

DoDat est le nouveau nom de CintaFactory. Le nom affiché est défini par
`APPLICATION_NAME` dans les réglages Django.

Les répertoires et modules Python `cintafactory`, les labels Django, les migrations,
les noms de base de données, les volumes Docker et les identifiants de supervision
conservent leur nom technique historique pour préserver les installations existantes.
Conservez notamment `APP_NAME`, `COMPOSE_PROJECT_NAME` et les paramètres de base de
données lors d'une mise à jour : changer ces valeurs peut sélectionner de nouveaux
conteneurs, volumes ou bases de données.
