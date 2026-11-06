## License
This project is under **CC-BY-NC-ND-4.0** license.
For more details, please check the LICENSE file or visit https://spdx.org

*Version 1.0
Date : 11/09/2026*

---
# Welcome to DoDat!
First off, thank you for considering contributing to DoDat, it's people like you that make open source such a great ecosystem to build in !

Following these guidelines helps communicate that you respect the time of the developers maintaining this project. In return, they should reciprocate that respect in addressing your issues, assessing your changes, and helping you finalize your pull requests.

### What kinds of contributions we are looking for
- Reporting bugs and technical anomalies with issues.
- Proposing new features or architectural improvements.
- Submitting Pull Requests (needs validation).
- Improving documentation, examples, guides, and tutorials.
- Helping answer questions from other community members.

### Contributions we are NOT looking for
- General user support questions : Please do not use GitHub Issues for personal setup or generic troubleshooting. Use our community discussion forums instead.
- Uncoordinated breaking changes : Avoid opening large, unsolicited pull requests without first discussing the architectural impact via an issue.
---
# Responsibilities & Code of Conduct
We want DoDat to be a welcoming, friendly, and safe environment for everyone, regardless of background, identity, or current level of coding experience.
- **People first, code second**: Always be kind, patient, and empathetic. We critique ideas and implementations, never the people behind them.

- **Assume positive intent**: Text communication can sometimes sound blunt. Give each other the benefit of the doubt and foster collaborative discussions.

- **Zero tolerance for harassment**: Hostility, personal attacks, or discriminatory remarks will not be tolerated.

- **Need help with an incident?** If you experience or witness inappropriate behavior, contact us in total confidence at **`coc@cintamaya.com`**. We take every report seriously and handle them discreetly and impartially.
---
# Your Very First Contribution?
Never contributed to open source before? You are in the right place, and we are happy to guide you through it!
A great way to get started is by filtering our issues:
- **`good first issue`**: Quick wins, small fixes, or simple tasks designed specifically for newcomers.
- **`help wanted`**: Clear, well-scoped tasks ready for community collaboration.

If Git or PR workflows feel intimidating:
-   Take a look at [How to Contribute to an Open Source Project on GitHub](https://egghead.io/series/how-to-contribute-to-an-open-source-project-on-github) or [First Timers Only](http://www.firsttimersonly.com/).
-   **Don't hesitate to ask questions** directly inside your PR or issue. Everyone was a beginner once, and nobody expects perfection on your first push!
---
# Getting Started
For any contribution that involves code or documentation updates:

1. **Fork** the repository to your GitHub account.
2. **Clone** your fork locally:
```bash
git clone https://github.com/<your-username>/<repo-name>.git
cd <repo-name>
```
4. **Create a dedicated branch** off `dev`:
```bash
git checkout -b feat/my-new-feature
# or for a bugfix:
git checkout -b fix/issue-123-bug-name
```
4. **Implement your changes** and add corresponding unit/integration tests.
5. **Run the local test suite and linters** to verify that everything builds and passes.
6. **Commit and push** your changes, then open a Pull Request on `dev`.
---
# How to Report a Bug
### Security Vulnerabilities (Confidential)
> ⚠️ **CRITICAL: If you find a security vulnerability, do NOT open a public GitHub issue. Email us directly instead to security@cintamaya.com.**

In your report, please include:
- A description of the vulnerability and its potential impact.
- Affected versions and components.
- Step-by-step instructions or a minimal Proof of Concept (PoC) to reproduce the exploit.
- Any proposed fix or mitigation (if available).

We will acknowledge receipt within a few days and work with you through a coordinated disclosure process before any public release.

### Filing a Standard Bug Report
Bugs are tracked directly on GitHub Issues. Before submitting:
- Verify the issue persists on the latest stable version or `main` branch.
- Search existing open and closed issues to avoid duplicates.

When opening an issue, provide:

- **A clear and descriptive title**.
- **Environment details**: Operating system, runtime version, framework version, dependencies.
- **Steps to reproduce**: Numbered, step-by-step instructions.
- **Expected behavior vs. Actual behavior**.
- **Logs & Stack traces**: Formatted inside Markdown code blocks (make sure to sanitize private tokens/passwords).
---
# Naming Conventions
These standards aim to: 
- Ensure readability, consistency, and maintainability of the source code
- Facilitate code understanding and reduce misinterpretations or misuse.

## General conventions
1. All identifiers (variables, functions, classes, constants, modules, etc.) must follow the same style throughout the project. 
2. All identifiers must be named in **English**, except in cases where using English **does not make sense** for the application's purpose or the business domain. - For well-established business or technical acronyms or abbreviations, the name **must be in uppercase** and clearly documented. - Example: `DAT` for "Dossier d’Architecture Technique" (Technical Architecture Document). 
3. Avoid non-explicit abbreviations or acronyms, unless they are very well known within the context. 
4. Each name must clearly reflect its role and nature: what it is or what it does.

## Casing Styles
### Variables / Attributes
- Use **snake_case**: all lowercase words, separated by underscores (`_`).
```python 
user_name = "Baptiste" 
need_energy_drink = True
```
- Examples to avoid: `u_cnt`, `x`, `uw_u` — too generic or not explicit.

 ### Functions / Methods
- Also use **snake_case** for functions: `do_action`, `calculate_total`, `fetch_user_list`
- A function performs **a single, well-defined action** (Single Responsibility Principle).
 
### Classes / Types / Modules
- **Classes**: names are in **CamelCase** (capital letter at the start of each word) to distinguish them clearly from variables and functions. Example: `UserAccount`, `InvoiceProcessor`
- **Modules or files**: use _snake_case_ or follow the conventions of the target language. Example: `user_account.py`, `power_handler.py`

### Constants
-   Use **SCREAMING_SNAKE_CASE** (all uppercase with underscores) for constants: `CINTA_VERSION`, `DEFAULT_TIMEOUT`

## Global Examples

| Identifier type       | Compliant Example                                  |
| --------------------- | -------------------------------------------------- |
| Variable              | `total_price`, `daily_coffee_count`                |
| Function              | `calculate_expense_report()`, `cast_fireball()`    |
| Class                 | `PaymentProcessor`, `UserProfile`                  |
| Module/file           | `payment_processor.py`, `forms.py`                 |
| Constant              | `DEFAULT_PAGE_SIZE = 42`, `MAX_LOGIN_ATTEMPTS = 5` |
---
Django Naming conventions