# i-regul — intégration Home Assistant (pompes à chaleur SAPAC)

[![hacs][hacs-badge]][hacs-url]
[![GitHub release][release-badge]][release-url]
[![HACS validation][hacs-action-badge]][actions-url]
[![hassfest][hassfest-badge]][actions-url]
[![License: MIT][license-badge]](LICENSE)

Intégration Home Assistant pour les installations pilotées par un régulateur **i-regul**
(pompes à chaleur SAPAC Mistral, etc.), via le serveur cloud `i-regul.fr` utilisé par
l'application *i-regul connect*.

Le protocole a été obtenu par analyse de l'application Windows *i-regul connect* — voir
[`docs/protocole-i-regul.md`](docs/protocole-i-regul.md). Aucune affiliation avec i-regul ni
SAPAC ; à utiliser à vos risques.

## Installation en un clic

**1. Ajouter le dépôt à HACS :**

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=crapitouille&repository=ha-iregul_sapac&category=integration)

Puis, dans HACS, cliquer sur **WattKeeper - i-regul** → **Télécharger**, et **redémarrer
Home Assistant**.

**2. Ajouter l'intégration :**

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=iregul)

Saisir le **numéro de série (SN)** et le **mot de passe** de votre installation (ceux
d'*i-regul connect*).

> Les boutons « My Home Assistant » ouvrent votre propre instance ; la première fois, indiquez
> son adresse (elle est mémorisée ensuite).

## Fonctionnalités

- **Découverte automatique** : au démarrage, une lecture complète (`{502#}`) récupère le libellé
  français, l'unité et les bornes de chaque point ; les entités sont créées dynamiquement.
- **Capteurs** : sondes de température, pressions HP/BP, débit, puissances, COP, énergies par usage
  (chauffage / ECS / rafraîchissement / dégivrage, compatibles tableau de bord Énergie), heures de
  fonctionnement, nombre de démarrages, état textuel du régulateur, dernière alarme.
- **Capteurs binaires** : compresseur, circulateurs, vanne d'inversion, appoints, alarme active…
- **Climate** (une entité par zone de chauffage) : consigne du mode actif, presets
  `auto / comfort / eco / away` (= auto / normal / réduit / hors-gel), arrêt de la zone.
- **Water heater** (ECS et appoint électrique ECS) : température du ballon, consigne, modes
  `auto / performance / eco / hors_gel / off`, et ce qui gouverne la relance de la PAC.
- **Switch** : autorisation chauffage, autorisation rafraîchissement.
- **Button** : acquitter l'alarme, forcer un dégivrage.

Les entités secondaires (tarifs T2/T3, PWM, entrées TOR, ballons ECS 2/3…) sont créées
désactivées : activez-les dans Paramètres → Entités si besoin.

## Installation (méthodes détaillées)

### HACS — bouton (recommandé)
Voir « Installation en un clic » ci-dessus.

### HACS — dépôt personnalisé (manuel)
1. HACS → ⋮ (en haut à droite) → *Dépôts personnalisés*.
2. URL : `https://github.com/crapitouille/ha-iregul_sapac`, catégorie *Intégration*.
3. Installer **WattKeeper - i-regul**, redémarrer Home Assistant.

### Sans HACS
Copier le dossier `custom_components/iregul` dans `config/custom_components/` puis redémarrer.

## Configuration

Paramètres → Appareils et services → *Ajouter une intégration* → **WattKeeper - i-regul**, puis saisir :

- **Numéro de série (SN)** : visible dans i-regul connect (liste des installations) — 6 chiffres.
- **Mot de passe** : celui de l'installation dans i-regul connect.

Options : **intervalle de scrutation** (défaut 300 s) et **délai d'attente d'une réponse**
(défaut 60 s). Une seule commande est envoyée à la fois ; chaque écriture est suivie d'une
relecture de l'état.

> À titre de repère, l'application officielle ne rafraîchit qu'**une fois par heure**
> (`_delay_refresh = 3600000 / nb_installations` ms). 300 s reste 12× plus réactif. Descendre
> en dessous sollicite beaucoup un serveur qui met parfois 40 s à répondre.

## Limites connues

- Dépend entièrement du cloud i-regul.fr (pas d'accès local) ; le port 443 est utilisé **sans TLS**
  par le protocole officiel, les identifiants transitent en clair — comme avec l'application.
- Si le régulateur est hors ligne, le serveur renvoie de vieilles données (`OLD`) : les entités
  passent *indisponibles* et un avertissement est journalisé.
- **Consigne ECS et appoint électrique** : la PAC ne chauffe l'eau chaude que jusqu'au
  paramètre installateur *T°max ECS* (55 °C sur une Mistral Compact), et ne relance la production
  que lorsque le ballon descend sous *consigne − hystérésis*. Demander 60 °C à la zone ECS ne
  suffit donc pas : la PAC vise au mieux 55 °C et ne redémarre que sous 50 °C. Au-delà, c'est
  l'**appoint électrique ECS** qui chauffe, avec sa propre consigne et son programme horaire —
  il est exposé comme entité à part (`water_heater.…_appoint_ecs`). L'entité ECS affiche
  `temperature_max_pac`, `hysteresis` et `seuil_relance_estime` pour qu'on voie pourquoi elle
  ne démarre pas. Le seuil est déduit du comportement observé, pas lu dans le firmware.
  La consigne ECS est plafonnée à la valeur publiée par le régulateur (57 °C) : l'application
  officielle laisse saisir 60, mais le régulateur n'en tient pas compte au-delà.
- Les zones n'ont pas de sonde d'ambiance par défaut (régulation par courbe de chauffe) : l'entité
  climate n'affiche pas de température courante ; la consigne calculée est dans les attributs.

## Développement

```bash
uv venv -p 3.13 .venv && . .venv/bin/activate
uv pip install homeassistant pytest pytest-homeassistant-custom-component
pytest
```

Les tests rejouent des trames réelles anonymisées (`tests/fixtures/`) sur un serveur TCP simulé
et vérifient octet par octet les commandes d'écriture générées.

## Licence

MIT.

## Dashboard

`dashboard/pompe-a-chaleur.yaml` : tableau de bord prêt à l'emploi (cartes natives, 2 vues :
*Vue d'ensemble* et *Énergie*). Paramètres → Tableaux de bord → Ajouter → ouvrir → ⋮ → Modifier →
⋮ → *Éditeur de configuration brute* → coller le fichier. Remplacer `pompe_a_chaleur_108944`
par `pompe_a_chaleur_<votre SN>` si nécessaire.

## Notes de fonctionnement

Le serveur i-regul refuse une connexion ouverte trop vite après la précédente (RST immédiat).
Le client espace donc chaque commande d'au moins 2 s et réessaie automatiquement les lectures
en cas de reset transitoire (jusqu'à 4 tentatives, backoff progressif). Les écritures ne sont
jamais rejouées (elles peuvent avoir été appliquées malgré le reset).

Le serveur peut aussi accepter la connexion puis **ne jamais répondre**. Ces délais dépassés
coûtent 60 s chacun : ils sont donc plafonnés à 2 par commande, pour qu'un cycle de scrutation
ne dépasse pas son propre intervalle. Au démarrage de Home Assistant, une seule longue attente
est tolérée : l'intégration renvoie alors `ConfigEntryNotReady` et HA réessaie avec son propre
backoff, plutôt que de retarder le démarrage de plusieurs minutes.

Quand le serveur a une absence, l'intégration **conserve les dernières valeurs connues** pendant
deux cycles plutôt que de faire clignoter toutes les entités en « indisponible » ; au troisième
échec consécutif elle devient honnête et les marque indisponibles. Tant que ça échoue, la
scrutation s'espace progressivement (jusqu'à 4× l'intervalle) au lieu d'insister — le premier
échec garde la cadence nominale pour ne pas retarder la détection du retour.

Quand une **écriture** dépasse le délai, l'erreur affichée précise que la commande a pu être
appliquée malgré tout : le serveur ne l'a simplement pas confirmée. Vérifiez la valeur après le
rafraîchissement suivant. À titre de comparaison, l'application officielle abandonne une écriture
de zone au bout de 17,5 s seulement — si le serveur est lent, elle échoue elle aussi.

[hacs-badge]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg
[hacs-url]: https://github.com/hacs/integration
[release-badge]: https://img.shields.io/github/v/release/crapitouille/ha-iregul_sapac?display_name=tag&sort=semver
[release-url]: https://github.com/crapitouille/ha-iregul_sapac/releases
[hacs-action-badge]: https://github.com/crapitouille/ha-iregul_sapac/actions/workflows/hacs.yaml/badge.svg
[hassfest-badge]: https://github.com/crapitouille/ha-iregul_sapac/actions/workflows/hassfest.yaml/badge.svg
[actions-url]: https://github.com/crapitouille/ha-iregul_sapac/actions
[license-badge]: https://img.shields.io/badge/License-MIT-green.svg
