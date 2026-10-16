---
name: soumission-sitemaps
description: Fait connaître un site neuf à Google et à Bing. Vérifie d'abord que le plan du site (sitemap) est propre, puis guide ou fait l'ajout du site dans Google Search Console et Bing Webmaster Tools, la vérification de propriété et la soumission du sitemap. La connexion aux comptes reste toujours à la personne. À faire une fois par site, après la première mise en ligne. Déclenche pour « soumets mon sitemap », « fais connaître mon site à Google », « Search Console », « Bing Webmaster », « indexe mon site », « est-ce que Google connaît mon site ».
---

# Soumettre le sitemap à Google et à Bing

Un sitemap est la liste des pages d'un site, dans un fichier que les moteurs lisent. Publii le fabrique seul (`/sitemap.xml`) et le met à jour à chaque synchro. On le soumet **une fois** par adresse de sitemap ; Google et Bing le relisent ensuite d'eux-mêmes. À refaire seulement pour un nouveau site, un nouveau domaine, une nouvelle langue publiée sous un autre sitemap, ou une refonte qui change beaucoup d'adresses.

Si l'agent peut piloter un navigateur (Claude dans Chrome, par exemple), il fait les clics. Sinon, il donne les étapes une à une et la personne clique.

## Les règles

- **La personne se connecte elle-même** à Google et à Microsoft. L'agent ne choisit jamais un compte à sa place et ne voit jamais un mot de passe.
- L'agent demande la permission avant d'ajouter un enregistrement DNS et avant toute soumission.
- **L'import de Bing depuis Google Search Console** donne à Bing un accès aux données de la Search Console : c'est la voie la plus simple, mais elle demande l'accord explicite de la personne.

## 1. Vérifier le sitemap avant de le soumettre

```
python -X utf8 scripts/check_sitemaps.py https://<domaine>
```

Le script (bibliothèque standard de Python seulement) lit `robots.txt`, télécharge chaque sitemap et demande chaque page listée comme le ferait Googlebot, sans suivre les redirections. Tout doit répondre 200, sans `noindex`, avec une adresse canonique qui pointe sur la page elle-même. Corrigez ce qu'il signale avant de soumettre, parce qu'une page qui redirige ou une 404 dans un sitemap finit en erreur dans les deux consoles.

Sans Python : ouvrez `https://<domaine>/sitemap.xml` dans le navigateur et vérifiez que chaque adresse listée s'ouvre.

## 2. Google Search Console

1. La personne ouvre search.google.com/search-console et se connecte avec son compte Google.
2. Ajouter une propriété de type **Domaine** (elle couvre `www`, `http`, `https` et toutes les pages), en tapant le domaine sans `https://`.
3. Google propose la vérification par un enregistrement TXT dans le DNS. Si le domaine est chez Cloudflare, Google peut proposer de le faire automatiquement en se connectant à Cloudflare [à vérifier à l'écran] ; sinon, l'agent ajoute l'enregistrement TXT dans la zone Cloudflare (par le serveur MCP de Cloudflare ou le tableau de bord), avec la permission de la personne, puis lance « Vérifier ». Ne retirez jamais cet enregistrement : Google revérifie de temps en temps.
4. Menu Sitemaps : soumettre l'adresse complète `https://<domaine>/sitemap.xml`.

Juste après l'envoi, un sitemap neuf affiche souvent « Impossible de récupérer le sitemap ». Si le script de l'étape 1 passe, Google n'a simplement pas encore lu le fichier : revérifiez dans un jour ou deux.

## 3. Bing Webmaster Tools

1. La personne ouvre bing.com/webmasters et se connecte (Microsoft, Google ou Facebook : c'est son choix).
2. Deux voies :
   - **Importer depuis Google Search Console**, avec l'accord de la personne (voir les règles) : Bing reprend le site vérifié et ses sitemaps.
   - **Ajouter le site à la main** avec l'adresse `https://<domaine>/`, puis vérifier la propriété. Si Bing propose une vérification automatique avec le fournisseur du domaine, la personne peut l'accepter ; sinon, un enregistrement CNAME ou TXT dans le DNS, ajouté comme pour Google.
3. Page des sitemaps : « Submit sitemap », l'adresse complète du sitemap.

## 4. Consigner

Notez pour la personne, dans un fichier qu'elle garde : la date, les sitemaps soumis à chaque console, la méthode de vérification, les enregistrements DNS à ne jamais supprimer. Dites-lui ce qui est fait et ce qui attend Google ou Bing. Précisez qu'elle n'aura rien à refaire aux prochaines synchros.

## Ce que la soumission ne fait pas

Soumettre un sitemap dit aux moteurs où sont les pages. Cela ne garantit ni l'indexation ni un rang. Google dit que l'exploration peut prendre de quelques jours à quelques semaines (developers.google.com/search/docs/crawling-indexing/ask-google-to-recrawl, mise à jour le 10 décembre 2025), et chaque moteur décide seul de ce qu'il indexe.
