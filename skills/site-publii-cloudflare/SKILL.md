---
name: site-publii-cloudflare
description: Met en ligne un petit site Publii (accueil, services, à propos, contact, politique de confidentialité, menu, logo) sur Cloudflare R2 avec votre propre domaine, en faisant tout ce qu'un agent peut faire sur votre ordinateur et dans votre compte Cloudflare, et en vous laissant les trois gestes qui vous reviennent (les clés dans Publii, le premier rendu, la première synchro). Utilise publii-agent-toolkit pour écrire dans le site sans le briser. Demande votre permission avant chaque installation, chaque écriture et chaque publication. Déclenche pour « mets mon site Publii en ligne sur Cloudflare », « héberge mon site sur R2 », « crée les pages de mon site Publii », « monte mon site avec mon domaine ». Demande un agent qui travaille sur votre ordinateur (Claude Code, dans l'onglet Code de l'application Claude, ou un équivalent) ; dans claude.ai seul, la skill ne peut que rédiger.
---

# Site Publii sur Cloudflare R2

Cette skill met en ligne un site Publii de cinq pages, servi par Cloudflare R2 au domaine de la personne. Deux outils font le travail. Le toolkit `publii-agent-toolkit` écrit dans le site Publii ; chaque écriture prend une sauvegarde, passe par une transaction et se termine par une vérification d'intégrité. Le serveur MCP de Cloudflare crée le bucket, branche le domaine et pose la règle de réécriture.

## Avant tout : les permissions

Au début, demandez à la personne ce que vous pouvez faire sans lui redemander. Si elle ne précise rien, demandez la permission avant chaque installation, chaque écriture et chaque publication. Expliquez chaque plan en mots simples avant de l'appliquer. Un refus est un refus : ne le contournez pas.

Ce que vous ne faites jamais : lire, afficher ou recopier un secret (clé S3, mot de passe, jeton) ailleurs qu'à l'endroit où la personne doit le coller ; ouvrir `site.config.json` ; écrire dans un fichier du site sans passer par le toolkit ; acheter quoi que ce soit.

## Ce qui revient à la personne

1. Installer Publii (getpublii.com) et y créer le site : nom du site, auteur, thème proposé par défaut.
2. Avoir un compte Cloudflare où le domaine est déjà une zone active (domaine acheté chez Cloudflare, ou transféré). Le domaine doit être dans le **même compte** que le bucket R2 (developers.cloudflare.com/r2/buckets/public-buckets/).
3. Activer R2 dans le tableau de bord de Cloudflare si ce n'est pas fait : « Storage & databases », puis « R2 Object Storage », puis « Add R2 subscription to my account » ; sur la page « Activate R2 », cocher les deux cases et cliquer « Activate ». Cloudflare demande une carte au dossier (ou reprend celle qui y est déjà), même pour rester dans le forfait gratuit, et ne la charge que si les limites mensuelles sont dépassées (libellés relevés le 20 octobre 2026).
4. Autoriser l'accès à son compte Cloudflare, dans la fenêtre que Cloudflare ouvre à l'étape 2, avec le compte du domaine.
5. Coller les valeurs S3 dans Publii, lancer le premier rendu et la première synchro (étape 3).

## 1. Vérifier l'ordinateur et installer le toolkit

Suivez la section « Installing » d'`AGENTS.md` du toolkit, à chaque étape avec la permission de la personne :

```bash
python --version        # 3.10 ou plus récent ; sinon, sous Windows : winget install Python.Python.3.12
pip install "publii-agent-toolkit[desktop] @ https://github.com/iMathieuB/publii-agent-toolkit/archive/refs/heads/main.zip"
python -c "import pywinauto"    # Windows : si « DLL load failed », winget install Microsoft.VCRedist.2015+.x64
publii-agent ops
publii-agent desktop-status
```

Ce qu'un Windows neuf réserve (constaté le 20 octobre 2026 dans une machine virtuelle vierge) :

- `python` peut n'être qu'un raccourci vers le Microsoft Store, qui n'installe rien. Si `python --version` ne répond pas par un numéro de version, proposez winget.
- Un terminal déjà ouvert, comme le vôtre, ne voit pas le Python que winget vient d'installer. Appelez-le par son chemin complet, `%LOCALAPPDATA%\Programs\Python\Python312\python.exe` (avec `-m pip` pour pip), et `publii-agent` par `%LOCALAPPDATA%\Programs\Python\Python312\Scripts\publii-agent.exe`.
- L'adresse en `.zip` installe le toolkit sans Git. Une adresse `git+https://` échoue quand Git n'est pas installé.
- La publication (`desktop-sync`) passe par pywinauto, qui a besoin du Visual C++ Redistributable de Microsoft. Si `python -c "import pywinauto"` répond « DLL load failed », installez-le avec winget, avec la permission de la personne (Windows demande une confirmation).

`desktop-status` liste les sites Publii trouvés. Demandez à la personne lequel est le bon et retenez son dossier, celui qui contient `input/` : c'est la valeur de `--site`. Lisez `AGENTS.md` en entier avant la première écriture.

## 2. Préparer Cloudflare

Vous avez besoin du serveur MCP de Cloudflare, `https://mcp.cloudflare.com/mcp` (developers.cloudflare.com/agents/model-context-protocol/mcp-servers-for-cloudflare/). C'est lui qui sait brancher un domaine sur un bucket et poser une règle de réécriture. L'ancien connecteur « Cloudflare Developer Platform » de claude.ai ne suffit pas : il crée des buckets, mais il n'a aucun outil pour les domaines ni pour les règles, et Cloudflare l'a déprécié au profit du serveur ci-dessus.

Regardez d'abord si le serveur est là, et dans quel état :

- **Branché et autorisé** : vérifiez avec la personne qu'il voit le bon compte, celui du domaine, puis passez à la suite.
- **Configuré, mais il demande une authentification** : demandez à la personne de taper `/mcp` dans cette session (ou d'ouvrir le menu Connecteurs de la session), de choisir `cloudflare` et de s'authentifier. La fenêtre de Cloudflare s'ouvre dans son navigateur : elle choisit le compte du domaine et autorise.
- **Absent** : ajoutez-le au niveau de l'utilisateur, avec sa permission :
  ```bash
  claude mcp add --transport http --scope user cloudflare https://mcp.cloudflare.com/mcp
  ```
  Dans l'application de bureau, `claude` n'est pas dans le PATH : le programme est sous `%APPDATA%\Claude\claude-code\<version>\<dossier>\claude.exe` (prenez la version la plus récente). Un serveur ajouté ainsi n'apparaît que dans une nouvelle session. Avant de le demander, donnez à la personne une courte instruction de reprise à coller (le dossier du site, ce qui est déjà fait). Demandez-lui ensuite de quitter complètement l'application (icône près de l'horloge, Quitter), de la relancer, d'ouvrir une nouvelle session dans le même dossier, d'y coller l'instruction et de s'authentifier par `/mcp` comme ci-dessus.

Ne comptez pas sur un connecteur ajouté dans les paramètres de claude.ai. Le 21 octobre 2026 (application de bureau 3.1623.1, Claude Code 2.1.304), un connecteur personnalisé `mcp.cloudflare.com` ajouté et autorisé dans claude.ai n'est apparu dans aucune session de l'onglet Code, même ouverte après coup. `claude mcp add` l'a rendu visible.

Repli, si le serveur ne peut pas être branché : Wrangler (`npx wrangler login`, avec sa permission) crée le bucket et branche le domaine, mais il ne pose pas la règle de réécriture ; celle-ci se fait alors avec la personne, dans le tableau de bord. Ne pilotez pas le tableau de bord de Cloudflare dans le navigateur à la place du serveur sans l'avoir d'abord proposé à la personne.

Dans l'ordre, en montrant chaque opération avant de la lancer :

1. **Repérer le compte et la zone** : l'identifiant du compte et celui de la zone du domaine. Si la zone n'est pas active, arrêtez-vous et dites-le.
2. **Créer le bucket**, nommé d'après le domaine avec des traits d'union (`atelier-dupont-ca`). Si Cloudflare répond que R2 n'est pas activé, renvoyez la personne à l'étape 3 de « Ce qui revient à la personne ».
3. **Brancher le domaine au bucket** (domaine personnalisé du bucket, par l'API R2 ou `npx wrangler r2 bucket domain add <bucket> --domain <domaine> --zone-id <zone>`). L'état passe d'« Initializing » à « Active » en quelques minutes.
4. **Poser la règle de réécriture.** R2 ne sert pas `index.html` quand on demande un dossier : sans cette règle, la page d'accueil répond 404. Une règle de réécriture d'URL dans la phase `http_request_transform` de la zone (developers.cloudflare.com/rules/transform/url-rewrite/create-api/) :
   - expression : `ends_with(http.request.uri.path, "/")`
   - réécriture dynamique du chemin : `concat(http.request.uri.path, "index.html")`
   Si la zone a déjà un jeu de règles pour cette phase, ajoutez la règle sans effacer les autres.
5. **Préremplir les réglages du serveur dans Publii** (toolkit 1.3.4 ou plus récent), Publii fermé. Une commande `set_s3_deployment` avec `website_url` (le domaine), `endpoint` (`https://<ID DU COMPTE>.r2.cloudflarestorage.com`, sans le nom du bucket) et `bucket` ; `region` vaut `auto` par défaut. `plan`, résumé à la personne, `apply` sur son accord. La commande écrit les valeurs que Publii écrit lui-même pour R2 et refuse les deux clés : c'est la personne qui les colle.
6. **Obtenir les clés S3 du bucket.** Deux voies :
   - **Tableau de bord (recommandée)** : la personne ouvre R2, « Manage API Tokens », crée un jeton de compte avec la permission « Object Read & Write » limitée à ce bucket, et garde la page ouverte : le secret ne s'affiche qu'une fois (developers.cloudflare.com/r2/api/tokens/).
   - **API**, si votre accès le permet : la clé d'accès est l'`id` du jeton créé, le secret est l'empreinte SHA-256 de sa `value` (même page). Affichez-les une seule fois, à l'étape 3, et ne les écrivez dans aucun fichier.
   Le point de terminaison est `https://<ID DU COMPTE>.r2.cloudflarestorage.com`, sans le nom du bucket.

## 3. Ce que la personne colle dans Publii

Si `set_s3_deployment` a rempli les réglages (étape 2.5), la personne n'a que les deux clés à coller. Dites-lui d'ouvrir Publii et le site, de cliquer « Server » dans la barre latérale, de coller « Access ID » et « Secret key » (étape 2.6), puis de cliquer « Save settings ». Le reste est déjà rempli : elle peut le relire avant d'enregistrer.

Sinon (toolkit antérieur à la 1.3.4, ou commande refusée), donnez-lui toute la liste, puis attendez qu'elle vous dise que c'est fait. Les libellés sont ceux de Publii 0.48.1 (relevés le 20 octobre 2026) ; une autre version peut les formuler autrement.

| Dans Publii, Server | Valeur |
|---|---|
| Type de serveur | S3 compatible storage |
| Website URL | `https://` puis le domaine |
| Use a custom S3 provider | activé (l'interrupteur à gauche du libellé) |
| S3 Provider endpoint | `https://<ID DU COMPTE>.r2.cloudflarestorage.com` |
| Access ID et Secret key | ceux de l'étape 2.6 |
| Bucket | le nom créé à l'étape 2.2 |
| Region | `auto` |
| Prefix, Cache-Control, ACL | laisser les valeurs proposées (ACL : Public read) |

Publii masque les deux clés par des points une fois tapées. Ensuite, dans Publii : Save settings, aperçu du site (premier rendu), puis « Sync your website » et le bouton du même nom dans la fenêtre qui s'ouvre. Quand la synchro est finie, la personne **ferme Publii** et vous le dit. `get_site_settings` montre alors `deployment.keys_set` à `true` : vous savez que les clés sont enregistrées, sans les avoir lues.

## 4. Vérifier la mise en ligne

Demandez `https://<domaine>/`. Si la réponse est 200 avec le thème vide, tout est branché. Si la racine répond 404 alors que `/index.html` répond, la règle de réécriture ne s'applique pas : revoyez l'étape 2.4. Une erreur de certificat dans les premières minutes veut seulement dire que le domaine du bucket n'est pas encore « Active » ; attendez.

## 5. Rédiger et créer les pages

Avant d'écrire, posez les questions qui manquent, en une fois : nom de l'entreprise, ce qu'elle fait, pour qui, où, comment on la joint, l'action attendue du visiteur, le ton (vous ou tu), les sujets à éviter. Si la skill `decouvrabilite-google-et-ia` est installée, suivez-la pour l'accueil et les services. N'inventez aucun chiffre, aucun témoignage, aucun client.

Faites lire chaque texte à la personne et attendez son accord. Ensuite, avec le toolkit, suivez l'exemple « A worked example » d'`AGENTS.md` : `get_site_settings`, puis un seul lot qui crée les pages (`create_page`, avec `"editor":"tinymce"` sur un site neuf, sauf si la personne préfère le Markdown), les menus (`create_menu`, `add_menu_item` avec `page`), le logo (`add_media` avec `"target":"logo"`) et les réglages (`update_site_settings` : `site_name`, `site_description`, `logo`, `front_page`, et le bandeau de témoins si besoin). `plan`, résumé à la personne, `apply` sur son accord, puis `check`. Donnez-lui le chemin de la sauvegarde (`result.backup`).

**Politique de confidentialité.** Rédigez-la à partir de ce que le site fait vraiment : quels renseignements il recueille (un lien courriel n'en recueille aucun par le site lui-même), qui les reçoit, combien de temps ils sont gardés, à qui écrire pour y accéder ou les faire corriger. Au Québec, la Loi 25 encadre ces renseignements : dites à la personne de faire relire la page par quelqu'un de qualifié, et ne présentez jamais votre texte comme un avis juridique.

**Bandeau de témoins.** Publii en a un intégré. Activez-le seulement si le site pose des témoins ou charge un outil de mesure : un site sans traceur n'en a pas besoin. Posez la question à la personne et notez sa réponse.

## 6. Publier

```bash
publii-agent desktop-sync --name <site>            # aperçu : dit ce qui serait publié
publii-agent desktop-sync --name <site> --apply    # publie, avec l'accord de la personne
```

Publii doit être fermé au départ : le module le lance, synchronise, vérifie sur disque que la synchro a réussi, puis le referme. Annoncez la publication seulement quand le module confirme le nouveau `syncDate`. Puis demandez chaque page en ligne et vérifiez qu'elle répond 200, avec le logo et le menu.

## 7. Le compte rendu

Dites à la personne : les adresses en ligne, ce qui a été créé, le chemin de la sauvegarde, ce qui reste à faire de son côté (soumettre le sitemap à Google et à Bing : skill `soumission-sitemaps`), et ce qui reste non vérifié. Si une clé S3 est passée à l'écran pendant un enregistrement vidéo, recommandez de la révoquer et d'en créer une autre.

## Pièges connus

- `publii_running` ou `site_locked` : Publii est ouvert. Demandez de le fermer ; ne réessayez pas en boucle.
- `desktop-sync` refuse un site parce que Publii ouvrirait sa fenêtre « We've made significant changes to the Privacy Settings » : c'est qu'un toolkit antérieur à la 1.3.3 a activé le bandeau de témoins. Demandez à la personne d'ouvrir le site dans Publii, d'aller dans Site settings, de cliquer Save settings une fois, puis de fermer Publii.
- Images ajoutées par le toolkit : Publii n'en fabrique pas les copies réduites ; la personne régénère les miniatures dans Publii si une image s'affiche mal.
- Les textes du bandeau de témoins restent ceux de Publii ; la personne les traduit dans les réglages du site si le thème les affiche en anglais.
- Une synchro publie pour vrai : la sauvegarde du toolkit protège le site sur l'ordinateur, elle n'annule pas une publication.
