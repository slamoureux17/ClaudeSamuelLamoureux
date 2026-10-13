-- Requête modèle pour l'importation SQL (python -m scorecard import-sql).
-- Adaptez les noms de tables/colonnes à votre base de données ou à votre réplique Serti.
-- Colonnes attendues en sortie: code_client, nom_client, annee, departement, revenus, marge
-- (optionnel: scenario = 'reel' | 'projete').
SELECT
    f.code_client              AS code_client,
    c.nom                      AS nom_client,
    EXTRACT(YEAR FROM f.date_facture) AS annee,   -- SQL Server: YEAR(f.date_facture)
    f.departement              AS departement,    -- ex.: 'Pièces', 'Service', 'SAAQ', 'Alignement'
    SUM(f.montant_vente)       AS revenus,
    SUM(f.montant_vente - f.montant_cout) AS marge
FROM factures f
JOIN clients c ON c.code_client = f.code_client
WHERE f.date_facture >= DATE '2024-01-01'
GROUP BY f.code_client, c.nom, EXTRACT(YEAR FROM f.date_facture), f.departement;
