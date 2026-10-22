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
- Le secret Turnstile ne s'écrit dans aucun fichier : il va dans les secrets du Worker (`npx wrangler secret put TURNSTILE_SECRET`). Ne l'affichez qu'une fois, au moment de le poser.
- Si le site est un site Publii, toute écriture dans le site passe par `publii-agent-toolkit` (voir la skill `site-publii-cloudflare`), jamais par une édition directe des fichiers.
- Un formulaire recueille des renseignements personnels. Au Québec, la Loi 25 encadre leur collecte : informer la personne au moment de la collecte, publier une politique de confidentialité, nommer un responsable. Rédigez à partir de ce que le formulaire fait vraiment, et dites à la personne de faire relire la politique par quelqu'un de qualifié. Ce texte n'est pas un avis juridique.

## Ce qu'il faut demander d'abord, en une fois

- L'adresse qui recevra les messages (elle sera vérifiée par Cloudflare).
- L'objet du formulaire : liste d'attente, demande de soumission, contact général.
- Les champs : par défaut nom, courriel, message facultatif, et une case de consentement.
- Le texte de la case de consentement (par défaut : « J'accepte que <entreprise> m'écrive au sujet de <objet> »).
- Combien de temps les messages sont gardés dans la boîte courriel, et qui est responsable de la protection des renseignements personnels (nom et adresse), pour la politique de confidentialité.

## 1. Le routage des courriels et l'adresse de destination

Dans le compte Cloudflare du domaine :

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

## 2. Turnstile

Créer un widget Turnstile : nom du site, domaine du site, mode « Managed », **pré-autorisation (pre-clearance) : non**, pour qu'aucun témoin ne soit posé. Vous obtenez une clé publique (elle va dans la page) et une clé secrète (elle va dans les secrets du Worker).

```bash
npx wrangler turnstile widget create <nom> --domain <domaine> --mode managed --clearance-level no_clearance --json
```

La sortie JSON contient la clé secrète. Ne l'affichez pas : lisez-la dans un script qui la passe directement à `npx wrangler secret put TURNSTILE_SECRET` par l'entrée standard, après le déploiement du Worker. Le serveur MCP de Cloudflare peut aussi créer le widget (API Turnstile, même règle : la clé secrète ne s'affiche pas). Sinon, ouvrez vous-même la création d'un widget dans le tableau de bord (Turnstile, ajouter un widget), préremplie, et laissez la personne la créer et coller la clé secrète elle-même.

## 3. Le Worker

Les gabarits sont dans `assets/` : `worker.js` (le programme, aucune dépendance), `wrangler.toml` (la configuration à remplir). Copiez-les dans un dossier de travail de la personne, par exemple `Documents\formulaire-<domaine>`, et remplacez chaque `<...>` de `wrangler.toml` :

- la route `<domaine>/api/inscription` sur la zone du domaine ;
- `DESTINATION` : l'adresse vérifiée à l'étape 1, aussi dans `destination_address` de la liaison `send_email`, ce qui empêche le Worker d'écrire à qui que ce soit d'autre ;
- `EXPEDITEUR` : une adresse du domaine du site, par exemple `formulaire@<domaine>` (l'expéditeur doit être sur un domaine où le routage est activé) ;
- `PAGE_MERCI` : `/merci/` ;
- `ORIGINE` : `https://<domaine>`.

Une route ne s'exécute que si le nom d'hôte a un enregistrement DNS proxifié. C'est le cas dès que le domaine est branché sur le bucket R2 : le Worker répond alors à la place du site pour ce seul chemin, et le site sert tout le reste.

Déploiement avec Wrangler (Node.js requis ; s'il manque, sous Windows : `winget install OpenJS.NodeJS.LTS`, avec permission) :

```bash
npx wrangler login                          # ouvre une fenêtre d'autorisation : la personne autorise, avec le compte du domaine
npx wrangler secret put TURNSTILE_SECRET    # coller la clé secrète Turnstile
npx wrangler deploy
```

Montrez `worker.js` à la personne avant `deploy`, en une phrase par bloc : ce qu'il accepte, ce qu'il refuse, à qui il écrit.

Si Wrangler ne peut pas être utilisé, le serveur MCP de Cloudflare peut téléverser le même script par l'API des Workers et poser la route ; les liaisons et le secret doivent être les mêmes.

## 4. La page de remerciement et le formulaire

Si le site est un site Publii, avec le toolkit, dans un seul lot, après `plan` et l'accord de la personne :

- `create_page` : une page `Merci` (slug `merci`), une phrase qui confirme la réception et dit quand la personne recevra des nouvelles ; pas dans le menu.
- `update_post` sur la page Contact : ajouter le contenu de `assets/formulaire.html`, avec la clé publique Turnstile, le nom de l'entreprise et l'objet de la liste remplacés. Garder un texte court avant le formulaire.
- `update_post` sur la politique de confidentialité (section 6).

Puis publier (`publii-agent desktop-sync`, à blanc puis réel, avec permission).

Gardez la case de consentement telle que dans le fragment : la case d'abord, puis son `<label for="consentement">` juste après, pas autour. Des thèmes cachent la case native et en dessinent une dans l'étiquette qui la suit : c'est le cas du thème par défaut de Publii (« Simple » : `input[type=checkbox]{opacity:0;position:absolute}`, puis `input[type=checkbox]+label:before`). Une case placée dans son étiquette y reste invisible, et le Worker refuse alors chaque envoi faute de consentement (constaté le 21 octobre 2026). **Vérifiez sur la page en ligne que la case se voit et se coche.**

Le formulaire fonctionne sans JavaScript, sauf Turnstile : le script de Turnstile est chargé à la fin du fragment. **Vérifiez sur la page en ligne que le script est bien là** (code source de `/contact/`). Si l'éditeur l'a retiré, dites-le à la personne : le Worker refusera chaque envoi tant que la vérification Turnstile est exigée. Deux voies : ajouter le script dans le code personnalisé du thème (dans Publii, geste de la personne, la skill ne touche pas à ce réglage), ou retirer temporairement le secret Turnstile (le Worker se rabat alors sur le champ piège seul, moins efficace contre les robots).

## 5. L'essai

L'essai, c'est la personne qui le fait. Ne remplissez pas et n'envoyez pas le formulaire vous-même, même si vous pilotez le navigateur : Turnstile est fait pour bloquer un navigateur piloté par un programme, et il bloque aussi le vôtre (constaté le 21 octobre 2026 : l'envoi par l'agent a échoué, la personne a dû le refaire à la main).

1. Ouvrez vous-même `https://<domaine>/contact/` dans son navigateur, puis donnez-lui les consignes en une fois : un nom et un courriel d'essai, cocher la case, attendre que la vérification de Turnstile affiche son succès, envoyer.
2. Surveillez l'onglet, sans capture d'écran : quand l'adresse passe à `/merci/`, l'envoi est accepté ; dites-le-lui.
3. La personne confirme que le message est arrivé. Vous ne lisez pas sa boîte. Avec un domaine neuf, les premiers messages arrivent souvent dans les courriels indésirables (observé à l'essai du 20 octobre 2026) : dites-lui de regarder là, de marquer le message comme légitime et d'ajouter l'adresse d'envoi à ses contacts, sinon les inscriptions suivantes iront au même endroit.

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
