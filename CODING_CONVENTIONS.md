# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: CC-BY-ND-4.0

# Norme de Nommage - CintaFactory

**Version 1.1**  
Date : 21/10/2025

---

## Objectif
Ces normes visent à :  
- garantir la lisibilité, la cohérence et la maintenabilité du code source ;  
- faciliter la compréhension du code et réduire les erreurs de compréhension ou de mauvais usage.

---

## Conventions générales  
1. Tous les identifiants (variables, fonctions, classes, constantes, modules…) doivent suivre le même style au sein du projet.  
2. Tous les identifiants doivent être nommés en **anglais**, sauf dans les cas où l’usage de l’anglais **n’a pas de sens** pour l’objectif de l’application ou le domaine métier.  
  - Pour les acronymes ou abréviations métier ou techniques bien établis, le nom **doit être en majuscules** et clairement documenté.  
  - Exemple : `DAT` pour « Dossier d’Architecture Technique ».  
3. On évite les abréviations ou acronymes non explicites, sauf si elles sont très bien connues dans le contexte.  
4. Chaque nom doit refléter clairement son rôle et sa nature : ce qu’il est ou ce qu’il fait.

---

## Style d’écriture  
### Variables / attributs  
- Utiliser la convention **snake_case** : tous les mots en minuscules, séparés par des underscores (`_`).  

```python
  user_name = "Baptiste"
  need_energy_drink = True
```

- Exemple à éviter : `u_cnt`, `x`, `uw_u` — trop générique ou non explicite.


### Fonctions / Méthodes

- Utiliser **snake_case** également pour les fonctions : `do_action`, `calculate_total`, `fetch_user_list`
        
- Une fonction réalise **une seule action bien définie** (principe de responsabilité unique).

### Classes / Types / Modules

- **Classes** : le nom est en **CamelCase** (majuscule au début de chaque mot), pour bien les distinguer des variables/fonctions. Exemple : `UserAccount`, `InvoiceProcessor`
    
- **Modules ou fichiers** : utiliser _snake_case_ ou selon les conventions du langage. Exemple : `user_account.py`, `power_handler.py`

### Constantes

- Utiliser **SCREAMING_SNAKE_CASE** (tous en majuscules avec des underscores) pour les constantes : `CINTA_VERSION`, `DEFAULT_TIMEOUT`
    

---

## Exemples globaux

| Type d’identificateur | Exemple conforme                                   |
| --------------------- | -------------------------------------------------- |
| Variable              | `total_price`, `daily_coffee_count`                |
| Fonction              | `calculate_expense_report()`, `cast_fireball()`    |
| Classe                | `PaymentProcessor`, `UserProfile`                  |
| Module/fichier        | `payment_processor.py`, `forms.py`                 |
| Constante             | `DEFAULT_PAGE_SIZE = 42`, `MAX_LOGIN_ATTEMPTS = 5` |

---
