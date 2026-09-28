# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: CC BY-ND 4.0

# Tests de charge

La commande Django `load_test` génère des données synthétiques isolées et lance des charges directes PostgreSQL ou HTTP. Elle n'ajoute ni dépendance ni table dédiée.

## Sécurité

Les actions qui écrivent ou suppriment des données exigent :

```bash
export LOAD_TEST_ENABLED=1
```

Quand `DJANGO_DEBUG` vaut `0`, ajouter `--allow-non-debug`. Cette option confirme seulement l'environnement ; elle ne désactive jamais l'isolation par `run-id`.

Chaque campagne utilise le préfixe `loadtest-<run-id>-`. Le nettoyage accepte uniquement cet identifiant et exige `--confirm`.

## Profils

| Profil | Utilisateurs | Applications | DAT | Valeurs remplies | Durée | Concurrence |
|---|---:|---:|---:|---:|---:|---:|
| `small` | 100 | 25 | 250 | 25 % | 30 s | 5 |
| `medium` | 1 000 | 250 | 5 000 | 50 % | 120 s | 20 |
| `large` | 10 000 | 2 500 | 50 000 | 75 % | 600 s | 50 |

Les options `--users`, `--applications`, `--dats`, `--fill-ratio`, `--duration` et `--concurrency` surchargent le profil. `--seed` rend les choix aléatoires reproductibles. `--batch-size` contrôle la mémoire utilisée pendant les insertions bulk.

## Exemples

Prévisualiser sans accès BDD :

```bash
python manage.py load_test seed --profile small --run-id demo --dry-run
```

Créer un jeu personnalisé :

```bash
LOAD_TEST_ENABLED=1 python manage.py load_test seed \
  --run-id demo --profile small --dats 1000 --fill-ratio 0.4 --batch-size 250
```

Charger PostgreSQL pendant 60 secondes :

```bash
LOAD_TEST_ENABLED=1 python manage.py load_test db \
  --run-id demo --mode mixed --read-ratio 0.8 \
  --duration 60 --concurrency 20 \
  --max-p95-ms 250 --max-error-rate 0.01 --min-throughput 100
```

Charger plusieurs routes HTTP :

```bash
python manage.py load_test http \
  --base-url http://127.0.0.1:8101 \
  --path /accounts/login/ --path /health/ready \
  --requests 1000 --concurrency 20 --scenario web
```

Pour une API OAuth, placer le jeton dans une variable d'environnement. La commande ne l'affiche jamais :

```bash
export CINTA_LOAD_TOKEN='...'
python manage.py load_test http \
  --base-url http://127.0.0.1:8101 --path /api/dats/ \
  --requests 500 --oauth-token-env CINTA_LOAD_TOKEN
```

Lancer campagne complète :

```bash
LOAD_TEST_ENABLED=1 python manage.py load_test suite \
  --run-id campaign-01 --profile small --base-url http://127.0.0.1:8101
```

Prévisualiser puis nettoyer :

```bash
python manage.py load_test cleanup --run-id campaign-01 --dry-run
LOAD_TEST_ENABLED=1 python manage.py load_test cleanup --run-id campaign-01 --confirm
```

Ajouter `--json-output` pour sortie exploitable en CI et `--allow-fail` pour conserver code retour zéro lorsque seuls seuils/SLO échouent.

## Mesures et limites

Résultats incluent débit, erreurs, moyenne, p50, p95, p99 et maximum. Charge HTTP conserve SLO existants des scénarios `web`, `proxy`, `drawio_export` et `likec4_export`.

Le seed bulk reconstruit explicitement graphe relationnel DAT, mais contourne volontairement méthodes `save()` et signaux. Charge HTTP teste chemin applicatif réel. Pièces jointes SeaweedFS, e-mails et création de jetons OAuth restent hors périmètre.

Pendant campagne Docker scaling, suivre Prometheus, Grafana et Loki selon `README_MONITORING.md`.
