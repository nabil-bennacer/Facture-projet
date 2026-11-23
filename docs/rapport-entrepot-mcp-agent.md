# Rapport — Entrepôt, serveur MCP, agent et industrialisation

*Brouillon des sections 6 à 9, à intégrer au rapport commun. Les passages entre crochets
[…] sont à compléter après les essais avec de vrais LLM.*

## 1. Entrepôt de données (étape 6)

**Choix : DuckDB + fichiers Parquet, l'entrepôt ne contenant que des vues.**

- Les couches silver (Parquet) et bronze (JSON) sont les seules copies des données. Le fichier
  `data/warehouse.duckdb` ne contient que des définitions de vues (`silver.*`, `bronze.*`,
  `gold.*`) : le **stockage** (fichiers) est séparé du **calcul** (moteur DuckDB), et
  reconstruire l'entrepôt est instantané (`python -m facture warehouse`).
- DuckDB est embarqué (pas de serveur à administrer), lit Parquet nativement, conserve les types
  `DECIMAL` des montants et `DATE`, et parle un SQL standard accessible depuis Python comme depuis
  DBeaver.
- **Accès concurrents** : tous les lecteurs (serveur MCP, agent, DBeaver) ouvrent l'entrepôt en
  **lecture seule**, ce que DuckDB autorise à plusieurs processus simultanément. La reconstruction
  écrit un fichier temporaire puis le remplace de façon atomique : un lecteur ne voit jamais un
  entrepôt à moitié construit. Limite : sous Windows, le remplacement échoue si un programme
  garde le fichier ouvert (fermer DBeaver avant de reconstruire).
- **Portabilité des références** : les vues utilisent des chemins relatifs à la racine du projet,
  résolus via `file_search_path`. Vérifié : un entrepôt construit dans un conteneur Linux
  (`/app/data`) s'interroge tel quel depuis Windows.
- **Couche gold (bonus)** : trois vues d'indicateurs calculées à la volée sur silver — dépenses
  mensuelles, encours et retards par fournisseur, dépenses par catégorie.

## 2. Serveur MCP (étape 7)

**Choix : SDK Python officiel `mcp` (2.x), transport stdio**, compatible avec Claude Code,
Claude Desktop et notre propre agent.

| Outil | Rôle |
|---|---|
| `list_invoices` | liste filtrée (fournisseur, client, statut, catégorie, période), paginée |
| `invoice_totals` | nombre et totaux HT / TVA / TTC, ventilables (fournisseur, mois, catégorie…) |
| `get_invoice` | détail d'une facture et de ses lignes ; signale un document en quarantaine |
| `describe_schema` | tables, colonnes, types, valeurs possibles des statuts, plage de dates |
| `business_indicators` | indicateurs gold |
| `run_sql` | SQL libre en lecture seule |

**Conception des outils pour un LLM.**
- Le LLM choisit ses outils d'après leur nom et leur description. Les descriptions disent
  explicitement quand utiliser l'outil (« à utiliser pour toute question de comptage ou de
  total ») et quoi appeler avant (`describe_schema` avant `run_sql`).
- Les totaux sont calculés par la base (`invoice_totals`) pour que le LLM n'additionne jamais
  lui-même.
- Les filtres sont tolérants : insensibles à la casse et aux accents, donc « payee » trouve
  « Payée ».
- Les montants sont renvoyés en chaînes décimales exactes, pas en flottants.
- **Erreurs exploitables** : un outil en échec renvoie un message qui dit comment corriger
  (format de date attendu, outil à utiliser à la place, SQL tenté), pour que le LLM se corrige
  seul.
- **Transparence** : chaque réponse contient `sql_executed`, les requêtes réellement exécutées
  et leurs paramètres.

**Sécurité (les arguments viennent d'un LLM, donc d'une source non fiable).** Plusieurs défenses
superposées :
1. Requêtes paramétrées pour toutes les valeurs. Les noms de colonnes (`group_by`, indicateurs)
   ne viennent que de listes blanches.
2. Connexion DuckDB en lecture seule.
3. Accès fichiers limité aux dossiers bronze/silver (`allowed_directories`,
   `enable_external_access = false`) et configuration verrouillée (`lock_configuration`) : la
   requête ne peut pas réactiver ces options.
4. `run_sql` n'accepte qu'**une seule instruction, de type SELECT**, vérifiée par le parseur de
   DuckDB (pas par des expressions régulières).

Constat pendant les tests : avec les seules protections 2 et 3, DuckDB acceptait encore
`COPY (…) TO 'fichier'` et écrivait un fichier dans un dossier autorisé. La défense 4 est donc
indispensable.

**Tests** : `tests/test_mcp_server.py` lance le vrai serveur et vérifie chaque outil contre des
requêtes DuckDB directes (37 vérifications). Il tente aussi 11 types d'écriture (INSERT, DELETE,
DROP, UPDATE, CREATE, COPY TO, ATTACH, INSTALL, SET, instructions multiples, lecture d'un fichier
hors entrepôt) : toutes sont refusées et les données restent intactes.

[Capture : conversation dans Claude Code utilisant le serveur MCP.]

## 3. Agent (étape 8)

**Architecture.**
- Une boucle indépendante du fournisseur (`facture/agent/agent.py`) : question → LLM → appels
  d'outils MCP → résultats → LLM → … → réponse.
- Une interface `ChatProvider` avec deux implémentations :
  - `anthropic` : SDK officiel, modèles Claude ;
  - `openai` : toute API compatible OpenAI (Ollama en local, Mistral, OpenAI, un modèle servi par
    vLLM…).
- Le choix se fait **uniquement par configuration** (`LLM_PROVIDER`, `LLM_MODEL`,
  `LLM_BASE_URL`, `LLM_API_KEY` dans `.env`, ou options `--provider/--model/--base-url`).

**Accès aux données** : l'agent lance notre serveur MCP et n'a aucun autre accès aux données.

**Visibilité** : pour chaque étape, l'utilisateur voit l'outil appelé et ses arguments, le SQL
exécuté (lu dans `sql_executed`) et un aperçu du résultat, puis la réponse et sa durée.

**Robustesse.**
- L'historique est conservé entre les questions (`/reset` pour l'effacer).
- Au-delà de `AGENT_MAX_TOOL_STEPS` appels d'outils pour une même question, l'agent s'arrête
  avec un message explicite.
- Les erreurs du LLM (clé invalide, serveur injoignable) sont affichées sans faire planter la
  session.
- Les arguments JSON invalides ou un outil inconnu sont renvoyés au modèle comme erreurs à
  corriger.
- Sortie propre par `/quit` ou Ctrl+C.

**Empêcher l'agent d'inventer des chiffres.**
1. Le prompt système exige que chaque nombre provienne d'un résultat d'outil de la conversation,
   et qu'il dise « je ne sais pas » sinon.
2. Les calculs sont faits par la base, pas par le LLM.
3. Le SQL exécuté est affiché, donc vérifiable.
4. Les chiffres annoncés sont contrôlés par une requête SQL indépendante (voir tableau).

**Tests** : `tests/test_agent.py` vérifie la boucle complète pour les deux fournisseurs, avec un
faux serveur LLM local et le vrai serveur MCP, sans clé ni coût.

**Comparaison de deux LLM** — même question, sans changer le code :

| Question | LLM | Outils appelés | Réponse exacte ? (vérif. SQL) | Durée | Observations |
|---|---|---|---|---|---|
| Montant TTC des factures en retard en 2025 | [hébergé] | […] | […] | […] | […] |
| idem | [local, ex. qwen3:8b] | […] | […] | […] | […] |
| Fournisseur avec le plus gros encours | [hébergé] | […] | […] | […] | […] |
| idem | [local] | […] | […] | […] | […] |

[Observations attendues à confirmer : un petit modèle local est plus lent sur CPU, choisit
parfois `run_sql` au lieu d'`invoice_totals`, se trompe plus souvent de filtre ; le grand modèle
hébergé enchaîne mieux les outils et se corrige après une erreur.]

## 4. Industrialisation (étape 9)

Le système tourne sur un poste. Pour des milliers de factures par mois et plusieurs
utilisateurs, voici nos priorités, par ordre d'importance.

1. **Fiabilité et automatisation du traitement.**
   - Déclencher l'ingestion à l'arrivée d'un PDF (notification d'événement S3 → file de
     messages) plutôt qu'à la main.
   - Orchestrer fetch → bronze → silver → warehouse avec un orchestrateur (Dagster, Prefect ou
     Airflow) : reprises automatiques, historique des exécutions.
   - Les bases sont déjà là : traitement incrémental par sha256, erreurs OCR tracées et
     retraitées.
2. **Montée en charge de l'OCR**, le poste le plus lent et le plus coûteux.
   - Paralléliser les appels avec une concurrence bornée, des reprises avec temporisation
     croissante et une limite de débit.
   - Mettre en cache par sha256 : on ne paie jamais deux fois la même page.
   - Mettre l'auto-scaling GPU du modèle derrière la file.
   - Suivre le coût et la latence par page.
3. **Stockage partagé et multi-utilisateurs.**
   - Déplacer bronze/silver de `data/` vers un stockage objet (S3), dans un format de table
     transactionnel (Iceberg ou Delta) pour des écritures concurrentes sûres.
   - Interroger avec un moteur partagé (DuckDB/MotherDuck, Trino) : même principe de séparation
     stockage/calcul qu'aujourd'hui, à l'échelle.
4. **Sécurité des accès et des secrets.**
   - Secrets dans un coffre (Vault, AWS Secrets Manager) plutôt qu'un `.env`.
   - Une clé par personne et par usage, au moindre privilège. Nous avons constaté l'intérêt des
     clés à portée limitée : une clé restreinte à `factures/bronze` ne pouvait pas lire les PDF.
   - Serveur MCP exposé en HTTP derrière une authentification (OAuth), avec un journal d'audit
     des requêtes SQL exécutées par utilisateur.
5. **Supervision** : journaux structurés, métriques (latence et taux d'erreur OCR, taux de
   quarantaine, factures traitées par jour), alertes quand le taux de quarantaine dépasse un
   seuil (signe d'un nouveau format de facture).
6. **Qualité et tests** : tests automatisés à chaque push, contrat de schéma silver vérifié en
   CI, évaluation OCR relancée sur la vérité terrain à chaque changement de modèle ou de
   résolution.

**Déjà implémenté** :
- image Docker unique (`docker run … facture <commande>`), secrets passés par `--env-file`,
  données dans un volume ;
- intégration continue GitHub Actions : tests du serveur MCP et de l'agent sans clé ni donnée
  réelle, puis construction de l'image.

## 5. Difficultés et limites

- **Versions récentes des bibliothèques** : `mcp` 2.x (API renommée par rapport à la 1.x) et
  DuckDB 2.x ; versions minimales fixées dans `requirements.txt`.
- **Encodage sous Windows** : des fichiers JSON écrits sans `encoding="utf-8"` étaient illisibles
  par DuckDB et incompatibles entre systèmes. Corrigé.
- **Chemins relatifs et verrouillage des accès** : le serveur MCP échouait lorsqu'un client le
  lançait depuis un autre dossier que la racine. Corrigé, et couvert par les tests.
- **Limites actuelles** :
  - le parseur silver repose sur des expressions régulières, sensibles à un nouveau gabarit de
    facture ;
  - les doublons sont détectés par numéro de facture seul ;
  - l'agent ne garde pas l'historique entre deux sessions.
