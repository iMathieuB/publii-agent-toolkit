// Reçoit le formulaire de la page Contact d'un site statique (Publii sur R2), filtre les robots
// avec Turnstile et envoie chaque inscription par courriel à une adresse vérifiée du compte.
// Rien n'est conservé : le Worker ne garde aucune copie du message.
//
// Liaisons et variables (wrangler.toml) :
//   EMAIL            liaison send_email, limitée à DESTINATION
//   DESTINATION      adresse qui reçoit les inscriptions (vérifiée dans Email Routing)
//   EXPEDITEUR       adresse d'envoi, sur le domaine du site (ex. formulaire@exemple.ca)
//   PAGE_MERCI       chemin de la page de remerciement (ex. /merci/)
//   ORIGINE          adresse du site (ex. https://exemple.ca), seule origine acceptée
//   TURNSTILE_SECRET secret Turnstile (wrangler secret put), jamais dans le code

const LIMITES = { nom: 100, courriel: 254, message: 2000 };
const COURRIEL = /^[^\s@<>"]+@[^\s@<>"]+\.[^\s@<>"]{2,}$/;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname !== "/api/inscription") return new Response("Introuvable", { status: 404 });
    if (request.method !== "POST") return new Response("Méthode non permise", { status: 405, headers: { Allow: "POST" } });

    const origine = request.headers.get("Origin");
    if (origine && env.ORIGINE && origine !== env.ORIGINE) return page(403, "Ce formulaire ne peut être envoyé que depuis le site.");

    let form;
    try { form = await request.formData(); } catch { return page(400, "Le formulaire n'a pas pu être lu."); }
    const champ = (n) => String(form.get(n) || "").trim();

    // Champ piège : invisible pour une personne, souvent rempli par un robot.
    if (champ("site_web")) return Response.redirect(new URL(env.PAGE_MERCI || "/", url).toString(), 303);

    const nom = champ("nom"), courriel = champ("courriel"), message = champ("message");
    if (!nom || !courriel) return page(400, "Le nom et le courriel sont obligatoires.");
    if (nom.length > LIMITES.nom || courriel.length > LIMITES.courriel || message.length > LIMITES.message)
      return page(400, "Un des champs est trop long.");
    if (!COURRIEL.test(courriel)) return page(400, "L'adresse courriel ne semble pas valide.");
    if (champ("consentement") !== "oui") return page(400, "Il faut cocher la case de consentement pour être inscrit.");

    if (env.TURNSTILE_SECRET) {
      const ok = await verifierTurnstile(champ("cf-turnstile-response"), env.TURNSTILE_SECRET, request.headers.get("CF-Connecting-IP"));
      if (!ok) return page(403, "La vérification anti-robot a échoué. Rechargez la page et réessayez.");
    }

    const texte = [
      "Nouvelle inscription reçue par le formulaire du site.",
      "",
      `Nom : ${nom}`,
      `Courriel : ${courriel}`,
      `Message : ${message || "(aucun)"}`,
      "",
      `Consentement à être contacté : oui, le ${new Date().toISOString()}`,
    ].join("\n");

    try {
      await env.EMAIL.send({
        from: env.EXPEDITEUR,
        to: env.DESTINATION,
        replyTo: courriel,
        subject: `Inscription : ${nom.replace(/[\r\n]+/g, " ")}`,
        text: texte,
      });
    } catch (e) {
      console.error("envoi du courriel", e && e.message);
      return page(502, "L'inscription n'a pas pu être transmise. Réessayez dans quelques minutes.");
    }
    return Response.redirect(new URL(env.PAGE_MERCI || "/", url).toString(), 303);
  },
};

async function verifierTurnstile(jeton, secret, ip) {
  if (!jeton) return false;
  const corps = new FormData();
  corps.append("secret", secret);
  corps.append("response", jeton);
  if (ip) corps.append("remoteip", ip);
  try {
    const r = await fetch("https://challenges.cloudflare.com/turnstile/v0/siteverify", { method: "POST", body: corps });
    const d = await r.json();
    return d.success === true;
  } catch {
    return false;
  }
}

function page(statut, phrase) {
  const html = `<!doctype html><html lang="fr"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Formulaire</title><body style="font-family:system-ui,sans-serif;max-width:40rem;margin:3rem auto;padding:0 1rem"><p>${phrase}</p><p><a href="javascript:history.back()">Revenir au formulaire</a></p></body></html>`;
  return new Response(html, { status: statut, headers: { "Content-Type": "text/html; charset=utf-8" } });
}
