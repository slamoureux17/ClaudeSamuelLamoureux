# Scorecard clients – Camions BL

Outil web pour suivre la relation d'affaires avec chaque client, construit à partir du
gabarit **Scorecard Client One-Pager** (`exemples/Scorecard_Client_OnePager.xlsx`).

## Ce que fait l'outil

| Section du gabarit | Dans l'outil |
|---|---|
| 1. Données historiques | Revenus, marge brute, % marge service / pièces, % marge global calculé, colonnes N-2, N-1, N projeté, N actuel et différence (projeté − actuel). Grille des 10 services utilisés (Pièces, Excelerator, Service, SAAQ, Unité mobile, Alignement, Jockey, Location BL, Ventes camions, Académie). |
| 2. Potentiel global | Potentiel annuel estimé, revenus actuels (saisis ou, par défaut, ceux de l'année précédente), % de pénétration et gap à combler. |
| 3. Analyse qualitative | Topgrading (A–D) et satisfaction (/5) par année, points forts, enjeux, potentiel de croissance (conservés par année). |
| 4. Plan d'action | Actions illimitées avec responsable, échéance, suivi et statut; retards signalés. |
| 5. Suivi de performance | Résultats et notes par trimestre (T1–T4). |

En plus :

- **Tableau de bord du portefeuille** : potentiel total, pénétration, gap, atteinte des objectifs,
  actions en retard, tri par gap / pénétration / revenus, prochaines échéances de tous les clients.
- **Navigation par année** (◀ 2025 · 2026 · 2027 ▶) : l'historique est conservé.
- **Export Excel** de chaque fiche au format du gabarit (formules incluses), réimportable.
- **Importation** des scorecards Excel existants (une feuille = un client).

## Sources de données

### Serti (Keyloop)
Serti n'offre pas d'API publique standard; l'outil importe donc un **export CSV** d'un rapport de
ventes par client (menu *Importer* ou `python -m scorecard import-ventes fichier.csv`).
Une ligne par client / année (ou date de facture) / département :

```
code_client;nom_client;annee;departement;revenus;marge
TRAJMF;JMF Transport;2025;Pièces;120 500,00;25 305,00
TRAJMF;JMF Transport;2025;Service;22 400,00;17 248,00
TRAJMF;JMF Transport;2025;SAAQ;4 999,00;3 849,00
```

- Le `code_client` correspond au **num. d'adresse Serti** et sert de clé de jumelage.
- Les noms de colonnes sont tolérants (`Client`, `Ventes`, `Coût`, `Date facture`, `Dept`…);
  une colonne `cout` remplace `marge` (marge = revenus − coût).
- Départements *Pièces* et *Service* → % de marge; *SAAQ*, *Alignement*, *Jockey*, *Location*,
  *Unité mobile*, etc. → cochent automatiquement les services utilisés.
- Une colonne `scenario` = `projete` permet de charger les objectifs annuels.

### Votre base de données (ou une réplique Serti)
Configurez `SOURCE_DB_URL` dans `.env` et adaptez `requete_ventes.sql` pour qu'elle retourne les
mêmes colonnes que le CSV. L'importation se lance depuis *Importer* ou
`python -m scorecard import-sql`. SQL Server/ODBC (`pyodbc`) et PostgreSQL (`psycopg`) sont
supportés via SQLAlchemy.

Les valeurs importées restent modifiables dans la fiche; la source (excel / serti / sql / manuel)
de chaque année est affichée.

## Pipedrive

1. Récupérez votre jeton : Pipedrive › Paramètres personnels › API.
2. Dans `.env` : `PIPEDRIVE_API_TOKEN=...` et `PIPEDRIVE_DOMAIN=<sous-domaine>`.
3. Sur la fiche d'un client : **Rechercher l'organisation** puis **Lier** (ou lier par ID).
4. **Synchroniser** (par client, pour tout le portefeuille, ou `python -m scorecard sync-pipedrive`) :
   - chaque action ouverte du plan d'action devient une **activité (tâche)** sur l'organisation,
     assignée à l'utilisateur Pipedrive dont le nom correspond au responsable (« Maxime » → « Maxime Tremblay »);
     les modifications (échéance, statut) sont répercutées;
   - une activité marquée **faite** dans Pipedrive ferme l'action dans le scorecard;
   - les suivis trimestriels deviennent des **notes** sur l'organisation;
   - les **affaires** ouvertes / gagnées dans l'année et les dates de dernière / prochaine activité
     sont affichées dans la fiche;
   - optionnel : le code Serti, le potentiel, le % de pénétration, le Topgrading et la satisfaction
     alimentent des **champs personnalisés** d'organisation (`PIPEDRIVE_FIELD_*` dans `.env`).

## Installation

Python 3.11+ requis.

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (Linux/Mac : source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env          # puis complétez les valeurs
python -m scorecard import-excel exemples/Scorecard_Client_OnePager.xlsx   # optionnel : client exemple
python -m scorecard serve       # http://localhost:8000
```

Pour un accès par l'équipe : `python -m scorecard serve --host 0.0.0.0` sur un poste ou serveur interne,
et `DATABASE_URL` vers une base partagée (PostgreSQL ou SQL Server) si désiré.

### Automatisation
Les commandes `import-ventes`, `import-sql` et `sync-pipedrive` peuvent être planifiées
(Planificateur de tâches Windows ou cron), par exemple chaque nuit.

## Développement

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest
```

Structure : `scorecard/models.py` (données), `metrics.py` (calculs), `importers/` (Excel, CSV, SQL),
`pipedrive.py` (CRM), `exporter.py` (Excel), `app.py` + `templates/` (interface web).
