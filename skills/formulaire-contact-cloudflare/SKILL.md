---
name: formulaire-contact-cloudflare
description: Ajoute un formulaire de contact ou de liste d'attente à un site statique hébergé chez Cloudflare (par exemple un site Publii sur R2), sans serveur à louer. Un petit programme Cloudflare (Worker) reçoit le formulaire, filtre les robots avec Turnstile et envoie chaque message par courriel à l'adresse de la personne ; rien n'est conservé sur le site. Met aussi à jour la politique de confidentialité. Demande la permission avant chaque installation, chaque écriture et chaque mise en ligne. Déclenche pour « ajoute un formulaire de contact », « formulaire de liste d'attente », « je veux recevoir les inscriptions par courriel », « formulaire sur mon site Publii », « formulaire Cloudflare ». Demande un agent qui travaille sur l'ordinateur (Claude Code ou un équivalent) et un domaine déjà actif dans Cloudflare.
---

# Formulaire de contact sur Cloudflare

Un site statique n'a rien pour recevoir un formulaire. Cette skill en ajoute un, gratuitement : le formulaire de la page envoie ses champs à un Worker sur le même domaine (`/api/inscription`). Le Worker vérifie le jeton Turnstile, contrôle les champs, puis envoie le message par courriel à une adresse vérifiée du compte Cloudflare, et redirige la personne vers une page de remerciement. Le Worker ne garde aucune copie.

Selon la documentation de Cloudflare, l'envoi vers une adresse de destination vérifiée du compte est gratuit sur tous les forfaits, y compris le forfait Workers gratuit, même quand seul le routage des courriels est configuré (developers.cloudflare.com/email-service/platform/pricing/, consulté le 16 octobre 2026). Turnstile est gratuit et ne pose aucun témoin par défaut (developers.cloudflare.com/turnstile/ et /cloudflare-challenges/concepts/clearance/, consultés le 16 octobre 2026). Chaque envoi compte comme une requête Workers, ce qui reste loin des limites du forfait gratuit pour un petit site.

## Avant tout : les permissions et les règles

- Demandez une permission avant chaque installation, chaque écriture dans le site, chaque création dans Cloudflare et chaque mise en ligne. Montrez le code du Worker avant de le déployer.
- Dans l'application Claude, le mode de permission automatique (« Auto », proposé par défaut) peut arrêter la création du widget Turnstile, le déploiement du Worker ou la pose de la route tant que la personne n'a pas donné son accord explicite dans la conversation. Ce n'est pas Cloudflare qui bloque : ne l'attribuez pas à Cloudflare. Avant ces créations, montrez en un seul message tout ce que vous allez créer (widget, secret, Worker, route) et demandez un oui explicite. Si l'instruction de départ de la personne contient déjà cet accord, rappelez-le et continuez. Si l'application bloque quand même, rapportez son message tel quel et redemandez l'accord, sans chercher à contourner le blocage.
- Le serveur MCP de Cloudflare d'abord, pour tout ce qu'il sait faire ; le navigateur seulement pour ce qu'il ne fait pas. Quand il faut une page du tableau de bord, ouvrez-la vous-même, au bon endroit, préremplie : la personne n'intervient que pour ses données sensibles et les clics qui créent ou autorisent.
- Le secret Turnstile ne s'écrit dans aucun fichier et ne s'affiche jamais : il passe directement de la réponse de Cloudflare à la liaison `secret_text` du Worker, dans le même appel (section 2), ou, en repli, à `npx wrangler secret put TURNSTILE_SECRET` par l'entrée standard.
- Si le site est un site Publii, toute écriture dans le site passe par `publii-agent-toolkit` (voir la skill `site-publii-cloudflare`), jamais par une édition directe des fichiers.
- Un formulaire recueille des renseignements personnels. Au Québec, la Loi 25 encadre leur collecte : informer la personne au moment de la collecte, publier une politique de confidentialité, nommer un responsable. Rédigez à partir de ce que le formulaire fait vraiment, et dites à la personne de faire relire la politique par quelqu'un de qualifié. Ce texte n'est pas un avis juridique.

## L'état des lieux, avant toute question

Faites d'abord, en un seul appel `execute` du serveur MCP de Cloudflare, une lecture de ce qui existe déjà :

- les enregistrements DNS de la zone, en particulier les MX : le domaine reçoit-il déjà du courrier ailleurs ?
- l'état du routage des courriels de la zone (`/zones/{zone_id}/email/routing` : `enabled`, `status`) ;
- les adresses de destination du compte et leur état (`/accounts/{account_id}/email/routing/addresses` : `verified`) ;
- les widgets Turnstile, les Workers et les routes déjà présents.

Le 21 octobre 2026, le routage était déjà actif et l'adresse déjà vérifiée : deux étapes de cette skill étaient inutiles. Passez ce qui est déjà fait, et ne demandez à la personne que ce qui reste (une fiche de renseignements sur le site, si elle existe, répond souvent au reste).

## Ce qu'il faut demander, en une fois, s'il manque

- L'adresse qui recevra les messages (elle sera vérifiée par Cloudflare).
- L'objet du formulaire : liste d'attente, demande de soumission, contact général.
- Les champs : par défaut nom, courriel, message facultatif, et une case de consentement.
- Le texte de la case de consentement (par défaut : « J'accepte que <entreprise> m'écrive au sujet de <objet> »).
- Combien de temps les messages sont gardés dans la boîte courriel, et qui est responsable de la protection des renseignements personnels (nom et adresse), pour la politique de confidentialité.

## 1. Le routage des courriels et l'adresse de destination

Si l'état des lieux montre le routage actif et l'adresse vérifiée, passez à la section 2. Sinon, dans le compte Cloudflare du domaine :

1. Activer Email Routing sur la zone, s'il ne l'est pas. Cloudflare ajoute des enregistrements MX et TXT au domaine. Si le domaine reçoit déjà des courriels ailleurs, **arrêtez-vous** et demandez à la personne : ces enregistrements changeraient la livraison de son courrier.
2. Ajouter l'adresse qui recevra les messages comme adresse de destination (Email Routing, Destination Addresses). Cloudflare envoie un courriel de vérification à cette adresse : **la personne clique le lien elle-même**. Attendez qu'elle vous le confirme. Tant que l'adresse n'est pas vérifiée, aucun envoi ne passe.

Avec Wrangler (essayé avec la version 4.123 le 20 octobre 2026 ; ces commandes sont en bêta) :

```bash
npx wrangler email routing enable <domaine>               # ajoute les enregistrements MX et TXT
npx wrangler email routing addresses create <adresse>     # envoie le courriel de vérification
npx wrangler email routing addresses list                 # la date de vérification apparaît après le clic
```

Si le serveur MCP de Cloudflare est branché, servez-vous-en plutôt (API Email Routing) : il évite une deuxième connexion par Wrangler. Sinon, ouvrez vous-même la page Email Routing du tableau de bord (developers.cloudflare.com/email-service/configuration/email-routing-addresses/).

Si la personne a accès à plusieurs comptes Cloudflare, fixez celui du domaine à chaque commande : `account_id` dans `wrangler.toml`, ou la variable `CLOUDFLARE_ACCOUNT_ID`. Pour défaire une connexion faite dans un profil nommé (`wrangler auth create`), utilisez `wrangler auth delete <profil>`, jamais `wrangler logout` : cette commande déconnecte le profil par défaut, même quand un autre profil est actif.

## 2. Turnstile, le Worker et la route, par le serveur MCP de Cloudflare

C'est la voie principale : elle ne demande ni Node.js ni Wrangler, et le secret Turnstile ne passe jamais à l'écran. Essayée le 21 octobre 2026 sur un ordinateur sans Node.js, en un seul appel `execute` du serveur MCP de Cloudflare. Montrez d'abord `worker.js` à la personne, en une phrase par bloc (ce qu'il accepte, ce qu'il refuse, à qui il écrit), puis demandez l'accord pour les trois créations ensemble.

Dans le même appel, enchaînez :

1. Le widget Turnstile : `POST /accounts/{account_id}/challenges/widgets`, avec `{ "name": "<nom du site>", "domains": ["<domaine>"], "mode": "managed", "clearance_level": "no_clearance" }`. Sans pré-autorisation (`no_clearance`), aucun témoin n'est posé. La réponse contient `sitekey` (la clé publique, qui va dans la page) et `secret`.
2. Le Worker : `PUT /accounts/{account_id}/workers/scripts/formulaire-<domaine-avec-tirets>`, en multipart, avec une partie `metadata` (JSON) et une partie `worker.js` de type `application/javascript+module`. Les métadonnées :
   - `"main_module": "worker.js"` ;
   - `"compatibility_date"` : la date du jour ;
   - `"bindings"` : des liaisons `plain_text` pour `DESTINATION`, `EXPEDITEUR`, `PAGE_MERCI` et `ORIGINE` (mêmes valeurs qu'en section 3), `{ "type": "send_email", "name": "EMAIL", "destination_address": "<adresse vérifiée>" }`, et `{ "type": "secret_text", "name": "TURNSTILE_SECRET", "text": <le secret reçu à l'étape 1> }`.
3. La route : `POST /zones/{zone_id}/workers/routes`, avec `{ "pattern": "<domaine>/api/inscription", "script": "formulaire-<domaine-avec-tirets>" }`.
4. Ne retournez que la clé publique, le nom du Worker, la liste des liaisons (`type:nom`) et la route. Jamais le secret.

Piège : `worker.js` contient des gabarits JavaScript `${...}`. Pour le placer dans le code passé à `execute`, encodez-le en chaîne JSON (par exemple avec `json.dumps` en Python), jamais en le recopiant dans un gabarit JavaScript, qui évaluerait ces `${...}`.

Gardez une copie de travail sans secret (`worker.js` et `wrangler.toml` remplis, section 3) dans `Documents\formulaire-<domaine>` : elle servira à un redéploiement futur, avec Wrangler ou par la même voie.

### Vérifier sans envoyer de courriel

Avant l'essai réel, trois requêtes suffisent :

- `GET https://<domaine>/api/inscription` répond 405 ;
- `POST` avec `Origin: https://<domaine>`, `Content-Type: application/x-www-form-urlencoded` et un corps vide répond 400, avec « Le nom et le courriel sont obligatoires » ;
- `POST` avec `Origin: https://exemple.com` répond 403.

Une route ne s'exécute que si le nom d'hôte a un enregistrement DNS proxifié. C'est le cas dès que le domaine est branché sur le bucket R2 : le Worker répond alors à la place du site pour ce seul chemin, et le site sert tout le reste.

## 3. Repli : Wrangler

Si le serveur MCP ne peut pas être branché, Wrangler fait la même chose. Les gabarits sont dans `assets/` : `worker.js` (le programme, aucune dépendance), `wrangler.toml` (la configuration à remplir). Copiez-les dans `Documents\formulaire-<domaine>`, et remplacez chaque `<...>` de `wrangler.toml` :

- la route `<domaine>/api/inscription` sur la zone du domaine ;
- `DESTINATION` : l'adresse vérifiée à l'étape 1, aussi dans `destination_address` de la liaison `send_email`, ce qui empêche le Worker d'écrire à qui que ce soit d'autre ;
- `EXPEDITEUR` : une adresse du domaine du site, par exemple `formulaire@<domaine>` (l'expéditeur doit être sur un domaine où le routage est activé) ;
- `PAGE_MERCI` : `/merci/` ;
- `ORIGINE` : `https://<domaine>`.

Le widget Turnstile, avec Wrangler :

```bash
npx wrangler turnstile widget create <nom> --domain <domaine> --mode managed --clearance-level no_clearance --json
```

La sortie JSON contient la clé secrète. Ne l'affichez pas : lisez-la dans un script qui la passe directement à `npx wrangler secret put TURNSTILE_SECRET` par l'entrée standard, après le déploiement du Worker.

Déploiement (Node.js requis ; s'il manque, sous Windows : `winget install OpenJS.NodeJS.LTS`, avec permission) :

```bash
npx wrangler login                          # ouvre une fenêtre d'autorisation : la personne autorise, avec le compte du domaine
npx wrangler deploy
npx wrangler secret put TURNSTILE_SECRET    # par l'entrée standard, sans affichage
```

Faites ensuite les trois vérifications sans courriel de la section 2.

## 4. La page de remerciement et le formulaire

Si le site est un site Publii, avec le toolkit, dans un seul lot, après `plan` et l'accord de la personne :

- `create_page` : une page `Merci` (slug `merci`), une phrase qui confirme la réception et dit quand la personne recevra des nouvelles, et un lien de retour à l'accueil (`<a href="#INTERNAL_LINK#/frontpage/1">`, le format de lien interne de Publii) ; pas dans le menu.
- `update_meta` sur la page Merci : `"metaRobots": "noindex, follow"`, pour que Google ne la propose pas ; Publii la retire alors aussi du sitemap (vérifié le 21 octobre 2026). Le toolkit 1.3.5 ne prend pas encore `metaRobots` dans `create_page`, d'où cette commande à part, dans le même lot.
- `update_post` sur la page Contact : ajouter le contenu de `assets/formulaire.html`, avec la clé publique Turnstile, le nom de l'entreprise et l'objet de la liste remplacés. Garder un texte court avant le formulaire.
- `update_post` sur la politique de confidentialité (section 6).

Puis publier (`publii-agent desktop-sync`, à blanc puis réel, avec permission).

Gardez la case de consentement telle que dans le fragment : la case d'abord, puis son `<label for="consentement">` juste après, pas autour. Des thèmes cachent la case native et en dessinent une dans l'étiquette qui la suit : c'est le cas du thème par défaut de Publii (« Simple » : `input[type=checkbox]{opacity:0;position:absolute}`, puis `input[type=checkbox]+label:before`). Une case placée dans son étiquette y reste invisible, et le Worker refuse alors chaque envoi faute de consentement (constaté le 21 octobre 2026). **Vérifiez sur la page en ligne que la case se voit et se coche.**

Prévenez la personne : si elle ouvre la page Contact dans l'éditeur visuel de Publii et l'enregistre, l'éditeur risque de retirer le `<form>` ou le script de Turnstile. Après toute modification de cette page, elle vérifie la page en ligne, ou vous demande de le faire.

Le formulaire fonctionne sans JavaScript, sauf Turnstile : le script de Turnstile est chargé à la fin du fragment. **Vérifiez sur la page en ligne que le script est bien là** (code source de `/contact/`). Si l'éditeur l'a retiré, dites-le à la personne : le Worker refusera chaque envoi tant que la vérification Turnstile est exigée. Deux voies : ajouter le script dans le code personnalisé du thème (dans Publii, geste de la personne, la skill ne touche pas à ce réglage), ou retirer temporairement le secret Turnstile (le Worker se rabat alors sur le champ piège seul, moins efficace contre les robots).

## 5. L'essai

Annoncez dès le plan que l'essai demande un clic de la personne : la vérification de Turnstile. Turnstile est fait pour bloquer un navigateur piloté par un programme, et il bloque aussi le vôtre (constaté le 21 octobre 2026 : l'envoi par l'agent a échoué, et la case « Vérifiez que vous êtes humain » est apparue dans le navigateur intégré). Vous ne répondez jamais vous-même à une vérification anti-robot.

1. Ouvrez vous-même `https://<domaine>/contact/` dans son navigateur et préremplissez le formulaire : un nom, un courriel d'essai (par exemple `essai@<domaine>`) et un court message.
2. Cochez la case de consentement, puis vérifiez qu'elle l'est vraiment : par JavaScript (`checked: true`) et par une capture. Le thème « Simple » de Publii cache la case native (`opacity: 0`) et en dessine une dans l'étiquette, ce qui rend un clic raté difficile à voir.
3. La personne fait le clic de Turnstile, puis clique sur Envoyer. Rien d'autre ne lui revient.
4. Surveillez l'onglet, sans capture d'écran : quand l'adresse passe à `/merci/`, l'envoi est accepté ; dites-le-lui.
5. Demandez-lui si le message est arrivé. Vous ne lisez pas sa boîte. Avec un domaine neuf, les premiers messages arrivent souvent dans les courriels indésirables (observé à l'essai du 20 octobre 2026) : dites-lui de regarder là, de marquer le message comme légitime et d'ajouter l'adresse d'envoi à ses contacts, sinon les inscriptions suivantes iront au même endroit.

Si rien n'arrive : `npx wrangler tail` pendant un nouvel essai montre l'erreur. Les causes habituelles sont une adresse de destination non vérifiée, un expéditeur hors du domaine routé, ou un jeton Turnstile absent (script retiré de la page).

## 6. La politique de confidentialité

Ajoutez ou remplacez une section « Formulaire de contact » qui dit, sans rien inventer :

- quels renseignements le formulaire recueille (les champs réels) ;
- pourquoi (l'objet du formulaire) et que la case de consentement sert à cela seulement ;
- qu'ils sont transmis par courriel à l'entreprise par l'intermédiaire de Cloudflare, sans être conservés sur le site ;
- que Turnstile, un service de Cloudflare, vérifie que l'envoi vient d'une personne, sans témoin ;
- combien de temps l'entreprise garde les messages (réponse de la personne) ;
- à qui écrire pour consulter, corriger ou faire retirer ses renseignements, et le nom du responsable de la protection des renseignements personnels.

Retirez toute phrase devenue fausse, par exemple « ce site ne recueille aucun renseignement ».

## 7. Le compte rendu

Dites à la personne : l'adresse du formulaire, l'adresse qui reçoit les messages, le nom du Worker, où sont les fichiers de travail, que le secret Turnstile n'existe que dans Cloudflare, et ce qui reste à faire de son côté (faire relire la politique, répondre aux inscriptions). Si une clé secrète est passée à l'écran pendant un enregistrement vidéo, recommandez d'en créer une autre dans Turnstile et de reposer le secret.

## Ce que cette skill ne fait pas

Elle ne stocke pas les inscriptions dans une base de données, n'envoie pas d'accusé de réception à la personne qui s'inscrit (envoyer à une adresse quelconque demande le forfait Workers payant), et n'envoie pas d'infolettre. Pour une liste qui grossit, la personne exporte ses courriels vers l'outil d'envoi de son choix, avec le consentement recueilli.
