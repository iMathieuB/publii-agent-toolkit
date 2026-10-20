# Skills

Four skills for Claude, written in French for small-business owners in Quebec. They are plain Markdown instructions: any agent that reads skills can use them, and two of them rely on this toolkit.

Quatre skills pour Claude, écrites en français pour les propriétaires de petites entreprises du Québec.

| Skill | Ce qu'elle fait | Où elle fonctionne |
|---|---|---|
| [`site-publii-cloudflare`](site-publii-cloudflare/SKILL.md) | Met en ligne un site Publii de cinq pages sur Cloudflare R2, avec votre domaine : installation du toolkit, bucket, domaine, règle de réécriture, pages, menu, logo, publication. Les clés S3, le premier rendu et la première synchro restent à vous. | Un agent qui travaille sur votre ordinateur (Claude Code, dans l'onglet Code de l'application Claude) |
| [`formulaire-contact-cloudflare`](formulaire-contact-cloudflare/SKILL.md) | Ajoute à votre page Contact un formulaire qui vous envoie chaque message par courriel, sans serveur à louer : un petit programme Cloudflare reçoit le formulaire et filtre les robots avec Turnstile. Elle met aussi à jour votre politique de confidentialité. Le clic qui vérifie votre adresse reste à vous. | Un agent qui travaille sur votre ordinateur, avec Node.js (Claude Code, dans l'onglet Code de l'application Claude) |
| [`decouvrabilite-google-et-ia`](decouvrabilite-google-et-ia/SKILL.md) | Rédige ou révise votre page d'accueil et vos pages de service pour que Google et les IA les comprennent. Elle n'invente aucun fait et ne promet aucun rang. | Partout, y compris claude.ai avec un compte gratuit |
| [`soumission-sitemaps`](soumission-sitemaps/SKILL.md) | Vérifie le plan de votre site, puis vous guide dans Google Search Console et Bing Webmaster Tools. Vous vous connectez toujours vous-même. | Un agent avec un navigateur, ou vous, étape par étape |

## Installer une skill

**Dans claude.ai** : téléchargez le ZIP de la skill depuis la page [Releases](https://github.com/iMathieuB/publii-agent-toolkit/releases) du dépôt, puis Personnaliser (Customize), Skills, téléverser une skill (Upload a skill). Un ZIP à la fois. L'exécution de code doit être activée dans vos réglages.

**Dans Claude Code** : copiez le dossier de la skill dans `%USERPROFILE%\.claude\skills\` sous Windows, ou `~/.claude/skills/` ailleurs.

## Avant de vous en servir

`site-publii-cloudflare` écrit dans votre site et dans votre compte Cloudflare. Elle demande votre permission avant chaque installation, chaque écriture et chaque publication, et le toolkit prend une sauvegarde avant chaque écriture. Une sauvegarde protège les fichiers de votre site sur votre ordinateur ; elle n'annule pas une publication. Faites un premier essai sur un site d'essai.

`formulaire-contact-cloudflare` écrit aussi dans votre compte Cloudflare et dans votre site, avec les mêmes demandes de permission. Un formulaire recueille des renseignements personnels : faites relire par quelqu'un de qualifié la politique de confidentialité qu'elle rédige.

Licence MIT, comme le reste du dépôt.
