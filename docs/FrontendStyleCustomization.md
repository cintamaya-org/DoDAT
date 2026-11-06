<!--
SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
SPDX-License-Identifier: CC-BY-ND-4.0
-->

# Personnaliser le style frontend

**Public visé :** développeurs et intégrateurs frontend.  
**Objectif :** expliquer où et comment modifier les styles CSS, les logos et les autres images de l'application.  
**Sources de vérité :** `cintafactory/static/` et `cintafactory/templates/`.  
**Dernière vérification :** 21 août 2026.

## Organisation des fichiers

Le frontend utilise les fichiers statiques de Django, sans étape de compilation CSS ou JavaScript.

| Fichier ou répertoire | Rôle |
| --- | --- |
| `cintafactory/static/css/global.css` | Variables CSS, mise en page et composants partagés par l'application. |
| `cintafactory/static/css/login.css` | Styles propres aux pages de connexion et de déconnexion. Chargé après `global.css`. |
| `cintafactory/static/css/dat_workflow_card.css` | Styles de la carte de workflow affichée dans une section DAT. |
| `cintafactory/static/imgs/` | Logos, favicon, icônes OAuth et images utilisées par les templates. |
| `cintafactory/templates/material/frontend/base.html` | Template principal : charge Material CSS, `global.css`, le favicon et les logos communs. |
| `cintafactory/templates/registration/` | Templates de connexion et de déconnexion. |

## Modifier les couleurs, la police et les dimensions globales

Commencer par les propriétés personnalisées déclarées dans `:root`, au début de `global.css`. Elles centralisent notamment la police, la hauteur de la barre supérieure, les couleurs d'état et les couleurs de la page de connexion.

```css
:root {
    --cinta-font-family-base: "DM Sans", sans-serif;
    --cinta-topbar-height: 64px;
    --cinta-color-success: #2e7d32;
    --cinta-color-danger: #c62828;
    --cinta-color-login-form-bg: #0b2239;
}
```

Réutiliser une variable existante dans les nouveaux styles au lieu de recopier sa valeur :

```css
.my-component {
    color: var(--cinta-color-success);
    font-family: var(--cinta-font-family-base);
}
```

Une variable ne modifie que les règles qui l'utilisent. Certaines couleurs sont encore écrites directement dans `global.css` ; rechercher leur valeur ou le sélecteur du composant si une modification de variable ne suffit pas.

La police DM Sans est chargée depuis Google Fonts dans `base.html`. Pour changer de police, mettre à jour à la fois le lien de chargement et `--cinta-font-family-base`. Pour une police locale, placer les fichiers dans un sous-répertoire de `cintafactory/static/`, déclarer une règle `@font-face`, puis utiliser ce nom dans la variable.

Conserver les media queries existantes et tester au minimum les largeurs suivantes :

- mobile, jusqu'à 600 px ;
- tablette, autour de 992 px ;
- desktop, au-delà de 1024 px.

## Ajouter une feuille de style propre à une page

Créer le fichier dans `cintafactory/static/css/`, puis le charger depuis le template Django. Le template doit conserver `{{ block.super }}` pour ne pas supprimer Material CSS et `global.css`.

```django
{% extends "material/frontend/base.html" %}
{% load static %}

{% block css %}
    {{ block.super }}
    <link rel="stylesheet" href="{% static 'css/my_page.css' %}">
{% endblock css %}
```

Préfixer les classes par le composant ou le module concerné, par exemple `.account-profile__avatar`, pour limiter les collisions. Éviter les styles en ligne et `!important` sauf lorsqu'une surcharge d'une bibliothèque existante l'exige et que sa raison est documentée.

Les pages de connexion chargent déjà `login.css` après `global.css`. Placer leurs adaptations spécifiques dans `login.css` ; à spécificité égale, ses règles prennent priorité.

## Remplacer ou ajouter une image

Placer la nouvelle image dans `cintafactory/static/imgs/`. Dans un template, charger le tag `static`, puis référencer le chemin relatif :

```django
{% load static %}
<img src="{% static 'imgs/my_logo.svg' %}" alt="Nom de l'organisation">
```

Dans un fichier CSS statique, les tags Django ne sont pas interprétés. Utiliser un chemin relatif au fichier CSS :

```css
.my-banner {
    background-image: url("../imgs/my_banner.webp");
}
```

Préférer SVG pour les logos et icônes vectoriels, et WebP ou PNG pour les images matricielles. Fournir un texte `alt` utile pour une image porteuse d'information et `alt=""` pour une image purement décorative.

### Images de marque actuelles

Tous les visuels Cintamaya utilisent désormais le nom de base `logo` afin de simplifier leur remplacement. Choisir le format attendu par l'emplacement sans changer le nom du fichier.

| Emplacement visible | Fichier statique | Référence à modifier |
| --- | --- | --- |
| Favicon | `imgs/logo.ico` | Bloc `favicon` de `base.html`. |
| Logo de la barre supérieure | `imgs/logo.svg` | `.brand-logo` dans `base.html`. |
| Logo du pied de navigation | `imgs/cintamata_logo_typogramme_signature.png` | `.sidenav-footer` dans `base.html`. |
| Illustration connexion/déconnexion | `imgs/cintamata_logo_typogramme_signature.png` | Templates `registration/login.html` et `registration/logged_out.html`. |
| Indicateur de chargement | `imgs/logo.png` | Composants `_loading_spinner.html` et configuration du lecteur draw.io. |
| Icônes OAuth | `imgs/google_logo.svg`, `microsoft_logo.svg`, `amazon_logo.svg`, `okta_logo.svg`, `logo.svg` | Tables `icon_map` de `users/oauth_views.py` et `account/views.py`. |
| Variante JPEG disponible | `imgs/logo.jpg` | Aucun usage actif dans les templates. |
| Logo LikeC4 | `likec4/logo.png` et `likec4/logo.svg` | Interface et serveur statique LikeC4. |

Remplacer un fichier en conservant son nom change toutes ses utilisations. Pour limiter le changement à un emplacement, ajouter un nouveau fichier et modifier seulement la référence du template concerné. Conserver un rapport largeur/hauteur compatible avec le conteneur existant, puis vérifier l'affichage mobile.

Le contenu de `logo.ico` doit être une véritable image ICO. Renommer simplement un fichier PNG en `.ico` crée un décalage entre le type MIME déclaré et son contenu, que les navigateurs appliquant `X-Content-Type-Options: nosniff` peuvent refuser.

## Thèmes runtime

Au démarrage, `cintafactory/cintafactory/theming.py` crée `cintafactory/conf/theming/active.json` et `cintafactory/conf/theming/base/tokens.json`. Dans l'état actuel, ces fichiers sont initialisés mais leurs valeurs ne sont pas injectées dans les templates ou dans les variables CSS. Les modifier ne change donc pas l'interface.

Utiliser `static/css/global.css` et les templates comme source de personnalisation tant qu'un chargeur de thème n'a pas été ajouté.

## Voir et déployer les changements

En développement, recharger la page après modification. Si le navigateur conserve une ancienne ressource, effectuer un rechargement forcé et contrôler dans les outils réseau que l'URL sous `/static/` répond correctement.

Avant livraison :

1. Vérifier les pages communes, la connexion, la déconnexion et la page directement concernée.
2. Tester mobile et desktop, les états focus clavier, les contrastes et les textes alternatifs.
3. Depuis le répertoire `cintafactory/`, valider Django :

   ```bash
   python manage.py check
   ```

4. Collecter les ressources dans l'environnement de déploiement :

   ```bash
   python manage.py collectstatic --noinput
   ```

La production sert les fichiers collectés avec WhiteNoise et un manifeste de ressources. Exécuter `collectstatic` après chaque changement de CSS ou d'image ; l'entrypoint Docker le fait lorsque `COLLECT_STATIC=1`.
