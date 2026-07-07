# Radar M&A — tracker d'offres de stage en M&A / Corporate Finance

Un tracker de type Fyntraq qui détecte les nouvelles offres directement sur les
sites carrières des banques, boutiques M&A et fonds — c'est-à-dire à la source,
avant qu'elles n'apparaissent sur LinkedIn ou Welcome to the Jungle.

## Comment ça marche

Les entreprises n'hébergent presque jamais elles-mêmes leurs offres : elles
utilisent un ATS (Workday, Greenhouse, SmartRecruiters, Lever, Recruitee…).
Ces ATS exposent des API JSON publiques, sans authentification, prévues pour
alimenter leurs propres pages carrières. Le scraper interroge ces API,
filtre par mots-clés (M&A, corporate finance, leveraged finance, private
equity…), déduplique, conserve la date de première détection, et écrit
`docs/jobs.json`, lu par le dashboard `docs/index.html`.

```
companies.yaml  ──►  scraper/scrape.py  ──►  docs/jobs.json  ──►  docs/index.html
 (vos cibles)         (collecte + filtre)       (données)          (dashboard)
```

## Démarrage rapide (local)

```bash
pip install -r scraper/requirements.txt
python scraper/scrape.py          # remplit docs/jobs.json
cd docs && python -m http.server  # ouvre http://localhost:8000
```

## Déploiement gratuit et automatique (GitHub)

1. Créez un dépôt GitHub et poussez ce projet.
2. Settings → Pages → Source : branche `main`, dossier `/docs`.
3. Le workflow `.github/workflows/scrape.yml` relance la collecte 3×/jour
   et commit le nouveau `jobs.json`. Votre tracker est en ligne, à jour,
   sans serveur ni coût.

## Ajouter des entreprises

Ouvrez la page carrières de l'entreprise et regardez l'URL :

| URL contient | ATS | À ajouter dans companies.yaml |
|---|---|---|
| `greenhouse.io/xxx` ou `boards.greenhouse.io/xxx` | Greenhouse | `token: xxx` |
| `jobs.lever.co/xxx` | Lever | `slug: xxx` |
| `jobs.smartrecruiters.com/Xxx` | SmartRecruiters | `slug: Xxx` |
| `xxx.wd3.myworkdayjobs.com/SiteName` | Workday | `tenant: xxx, wd: wd3, site: SiteName` |
| `xxx.recruitee.com` | Recruitee | `slug: xxx` |

Astuce pour vérifier un slug SmartRecruiters :
`https://api.smartrecruiters.com/v1/companies/<slug>/postings` doit renvoyer du JSON.

Certaines banques (Crédit Agricole CIB, Oracle Cloud Recruiting de Lazard/JPMorgan,
SuccessFactors…) utilisent des ATS propriétaires : leurs endpoints JSON existent
aussi (ouvrez l'onglet Réseau des outils développeur sur leur page carrières et
repérez la requête qui renvoie la liste des offres), mais ils changent plus souvent —
ajoutez-les au cas par cas en vous inspirant du connecteur Workday.

## Sources agrégées officielles (optionnel)

- **France Travail** — API "Offres d'emploi v2", gratuite : créez une application
  sur https://francetravail.io puis renseignez `client_id`/`client_secret`.
- **Adzuna** — agrège de nombreux job boards, API gratuite : https://developer.adzuna.com
- **JobTeaser / Welcome to the Jungle / LinkedIn** — pas de scraping : leurs CGU
  l'interdisent et LinkedIn bloque activement. WTTJ et JobTeaser proposent des
  accès partenaires si vous voulez aller plus loin ; sinon les offres qu'on y
  trouve viennent de toute façon des ATS déjà couverts ici.

## Bonnes pratiques

- Gardez une fréquence raisonnable (quelques collectes/jour suffisent) et le
  `User-Agent` identifiable défini dans `scrape.py`.
- Ne stockez que les métadonnées (titre, lieu, lien) et renvoyez toujours vers
  l'offre d'origine — c'est ce que fait ce projet.
- Respectez les robots.txt et CGU des sites que vous ajoutez.

## Idées d'évolution

- Alertes : un job GitHub Actions qui compare l'ancien et le nouveau `jobs.json`
  et envoie les nouveautés par e-mail (ex. via Resend/Brevo) ou Telegram.
- Classification plus fine (division, spécialisation, langues) avec l'API Claude.
- Détection de la date de début (regex "Janvier 2027", "Sep 2026" dans le titre).
