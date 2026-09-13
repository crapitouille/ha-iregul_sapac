# Protocole i-regul connect (PAC SAPAC) — analyse

Analyse réalisée par décompilation de l'application Windows **i-regul connect** installée dans `C:\cdram\i-regul connect` (application B4J/JavaFX, classe principale `b4j.iregul_connect.b4xmainpage`), recoupée avec le projet open-source [RedPaladin/i_regul_heatpump](https://github.com/RedPaladin/i_regul_heatpump) (rétro-ingénierie de la même appli, testée sur une SAPAC Mistral Compact 7).

## 1. Architecture

L'application ne parle **jamais directement à la pompe à chaleur**. Elle interroge un serveur cloud (« cdram ») auquel le régulateur i-regul est lui-même connecté par internet :

```
i-regul connect (PC / Android)  ──TCP──▶  i-regul.fr:443  ◀──  régulateur i-regul (PAC)
```

Le point important : le port 443 est utilisé **sans TLS**. C'est un socket TCP brut avec un protocole texte maison (probablement pour passer les pare-feux). Aucune couche HTTP.

Les identifiants sont stockés en clair sur le PC : `bin\SN.txt` (numéro de série, 6 chiffres), `bin\password.txt`, `bin\clients.txt` (nom d'affichage). Le même couple SN/mot de passe est celui que tu utilises dans l'appli.

## 2. Cycle d'un échange

Chaque commande = **une nouvelle connexion TCP** (l'appli ouvre, envoie, lit la réponse, ferme).

1. `connect("i-regul.fr", 443)`, timeout 1 200 000 ms dans l'appli.
2. Envoi en UTF-8, sans terminateur :
   ```
   cdraminfo<SN><PASSWORD>{<code>#<param1>#<param2>...}
   ```
   Exemple lecture d'état : `cdraminfo108944xxxxxxxx{10#}`
3. Lecture jusqu'à ce que la réponse se termine par `}` (suivi d'un `\r`).
4. L'appli sérialise les commandes dans une file (`_list_commandes`) traitée par un timer de 500 ms, une seule commande en vol à la fois, avec un délai max d'attente (`_max_verrou`, 35 à 240 ticks selon la commande).

Rafraîchissement automatique : `3600000 / nombre_d_installations` ms, soit **1 requête par heure** pour une seule PAC (le dépôt HA utilise 30–300 s sans problème apparent).

## 3. Format de la réponse

La réponse commence éventuellement par un en-tête de 3 lettres :

| En-tête | Signification | Traitement appli |
|---|---|---|
| `PWD` | mot de passe incorrect | abandon, message « no data » |
| `SNI` | numéro de série inconnu | abandon |
| `OLD` | données anciennes (régulateur hors ligne, le cloud renvoie le dernier état connu) | parse quand même (état 42 « vieilles données ») |
| `MES` | message texte | affiché tel quel |
| *(aucun)* | trame normale (« TRA ») | parse |

Corps d'une trame :

```
[JJ/MM/AAAA HH:MM:SS]{<code_echo>#<T>@<id>&<champ>[<valeur>]#<T>@<id>&<champ>[<valeur>]#...}
```

- Un horodatage optionnel de 19 caractères précède parfois le `{` (timecode de la trame).
- `code_echo` = le code de la commande envoyée (ex. `10`).
- Chaque élément : **type `T`**, **identifiant numérique `id`**, **nom de champ**, **valeur entre crochets**.
- Regex pratique : `#(\w+)@(\d+)&(\w+)\[(.*?)\]`

### Types d'éléments (`T`)

| T | Signification | Champs rencontrés |
|---|---|---|
| `A` | Entrée **analogique** (sondes de température, débit…) | `esclave`, `adr`, `valeur`, `unit`, `francais`/`anglais`/…(libellé), `alias` |
| `I` | Entrée **TOR** (contact) | `esclave`, `adr`, `valeur`, libellé langue, `alias` |
| `O` | **Sortie** TOR (compresseur, circulateurs, vannes…) | `esclave`, `adr`, `valeur`, libellé langue, `alias` |
| `M` | **Mesure** calculée (énergies, puissances, compteurs) | `valeur`, `unit`, libellé langue, `alias` |
| `B` | Registres **Modbus** esclaves | `nom_esclave`, `esclave`, `adresse`, `valeur`, `resultat`, `nom_registre`, `fonction`, `flag`, `etat` |
| `Z` | **Zone** de régulation (chauffage, ECS, piscine…) | voir §5 |
| `W` | **Journal** d'alarmes | `time_code`, `type`, `num_alarme`, `text_alarme` |
| `mem` | **État global** du régulateur | `etat`, `sous_etat`, `alarme`, `alarme_flag`, `alarme_num_sonde`, `solar_fct`, `journal`, `version`, `m_e`, `CZ`, `info`, `test_*` |
| `P` | **Paramètres** installateur | `valeur`, `nom` |
| `C` | **Configuration** client | `option_capteur_air`, `autorisation_chauffage`, `autorisation_rafraichissement`, `lila`, `nom_client`, `adresse_client`, `CP_client`, `ville_client`, `tel_client`, `mail_client`, `mail_installateur`, `option_internet` |

Le libellé de chaque point est envoyé par le serveur dans la langue demandée (champ `francais`, `anglais`, `allemand`, `espagnol`, `italien`) : on peut donc **découvrir dynamiquement** la signification de chaque id sans table en dur.

### Identifiants connus (Mistral Compact, d'après le dépôt HA + le code de l'écran d'accueil)

| Clé | Signification |
|---|---|
| `A@1&valeur` | T° ECS (ballon) |
| `A@2&valeur` | T° intérieure / ambiance (écran accueil) |
| `A@3&valeur` | T° extérieure |
| `A@21&valeur` | Débit chauffage (l/min) |
| `A@23&valeur` | T° recirculation ECS |
| `A@100..110` | sondes circuit frigo (écran « frigo ») |
| `M@20..27&valeur` | Énergies (kWh) : 20 autres, 21 chauffage, 22 rafraîchissement, 23 ECS, 25 dégivrage, 26 total, 27 compteur |
| `O@1` | Production ECS |
| `O@3` | Compresseur |
| `O@4` | Détendeur |
| `O@5` | Circulation ECS |
| `O@7` | Appoint ECS |
| `O@8` | Capteur |
| `O@10` | Vanne d'inversion |
| `O@60` | Circulateur ballon tampon |
| `O@100` | Circulateur technique |
| `O@101` | Appoint chauffage |
| `O@110,120,…` | Circulateur zone 1, 2, … (`id_o_zone = 100 + 10×n`) |
| `mem@0&etat` | Code d'état PAC (table §6) |

## 4. Codes de commande

Format : `{<code>#...}`. Les codes sans paramètre sont `{code#}`.

### Lecture

| Code | Rôle | Note |
|---|---|---|
| `10` | **État courant** (A, I, O, M, Z, mem, C) — la commande de base | utilisée pour le rafraîchissement |
| `100` | Tables complètes (paramètres, pentes) si `version = 0` | attente longue (80) |
| `200` | Relecture après modification d'une table (`113/114/115/116/120`) | |
| `501` | Lecture « légère » au démarrage / changement d'installation | |
| `502` | Première lecture complète avec libellés (quand la liste des sorties est vide) | attente 80–240 |
| `98` | Journal des alarmes (court) | contexte 15 |
| `99` | Journal des alarmes (long, appui long) | contexte 16 |
| `500/510/520/530/540/550` | Téléchargement des coordonnées/config client dans la langue FR/EN/DE/ES/IT | |

### Écriture (pilotage)

| Code | Rôle | Format |
|---|---|---|
| `11` | **Modifier une ou plusieurs zones** (consignes + mode) | `{11#DT_zones@<z>&consigne_normal[20.5]#DT_zones@<z>&consigne_reduit[18]#DT_zones@<z>&consigne_horsgel[8]#DT_zones@<z>&mode_select[0]}` (+ optionnel `temperature_pente_chaud`, `temperature_pente_froid`) |
| `12` | **Autorisation chauffage / rafraîchissement** | `{12#DT_config@0&autorisation_chauffage[1]#DT_config@0&autorisation_rafraichissement[0]}` |
| `19` | Modifier les infos client (nom, mot de passe, adresse…) | `{19#DT_config@0&nom_client[...]#DT_config@0&password[...]#...}` |
| `113` / `114` | Modifier une ligne de config (écran pro) | `{113#DT_config@0&<champ>[<valeur>]}` |
| `115` | Modifier un champ de zone (écran pro) | `{115#DT_zones@<id>&<champ>[<valeur>]}` |
| `116` | Forcer/modifier une sortie TOR, entrée analogique ou entrée TOR (écran pro) | `{116#DT_out_tor@<id>&<champ>[<val>]}` / `DT_in_ana` / `DT_in_tor` |
| `120` | Modifier une donnée « cdram » (serveur) | `{120#DT_cdram@<id>&<champ>[<val>]}` |
| `202` | **Reset alarme** | `{202#}` |
| `203` | Forcer un **dégivrage** | `{203#}` |
| `204` | Mise à l'heure | `{204#DT_config@0&day[1]}` (mot de passe pro `@_…` requis) |
| `205` | Sauvegarde config (pro) | |
| `206` / `216` | RAZ journal / demi-RAZ (pro) | |
| `210` | **Reset régulation** (pro) | |

Les commandes marquées « pro » ne sont envoyées par l'appli que si le mot de passe commence par `@_` (compte installateur) ; le serveur les refuse probablement sinon.

Après une écriture, l'appli renvoie systématiquement un `{10#}` (ou `{200#}`) pour relire l'état.

## 5. Zones (`Z@<id>&<champ>`)

Table des zones (fichier `bin\108944_zones.txt`, index = id) :

| id | Zone | id | Zone |
|---|---|---|---|
| 0 | sans | 10 | T° minimum (pente globale) |
| 1 | ecs1 | 11–30 | Zone 1 … Zone 20 (chauffage) |
| 2 | piscine | 31 | ecs2 |
| 3 | appoint ecs1 | 32 | ecs3 |
| 4 | appoint chauffage | 33 | appoint ecs2 |
| 5 | recirculation ecs | 34 | appoint ecs3 |
| 6–9 | ----> 1..4 (circuits) | | |

Champs d'une zone (`_z[id][col]` dans l'appli) :

| Champ | Colonne | Rôle |
|---|---|---|
| `temperature_pente_chaud` | 2 | pente / courbe de chauffe |
| `temperature_pente_froid` | 3 | pente rafraîchissement |
| `temperature_pente_mini` | 4 | |
| `temperature_pente_maxi` | 5 | |
| `consigne_reduit` | 6 | consigne réduit |
| `consigne_normal` | 7 | consigne confort |
| `consigne_horsgel` | 8 | consigne hors-gel |
| `mode_select` | 11 | **0 = auto, 1 = normal, 2 = réduit, 3 = hors-gel, 4 = arrêt** |
| `zone_nom` | 12 | nom |
| `mode` | 20 | mode effectif courant |
| `zone_regle` | 24 | |
| `derog_chaud` / `derog_froid` | 25/26 | dérogations |
| autres (pro) | | `zone_raf`, `zone_sonde_depart`, `zone_sonde_ambiance`, `zone_hygrometre`, `zone_circulateur_permanent`, `zone_correction_pente`, `zone_blocage`, `zone_adjust`, `zone_4-20mA`, `zone_active`, `temperature_max`, `temperature_min` |

Une zone est considérée « présente » si la sortie circulateur associée `O@(100+10×n)` est dans la trame.

## 6. Codes d'état (`mem@…&etat`, langue FR)

0 reset régulation · 1 démarrage en cours · 2 démarrage · 3 début de fonctionnement · 4 acquisition des paramètres · **5 pompe à chaleur en marche** · 6 arrêt en cours · 7 arrêt du compresseur · 8 arrêt de l'installation · 9 temporisation après arrêt · **10 pompe à chaleur à l'arrêt** · 11 temporisation redémarrage · 12 blocage cause défaut · 20 défaut connexion carte · 21 défaut sonde · 22 défaut démarreur · 23 verrouillage · 24 défaut débit capteur · 25 défaut niveau puits · 26 défaut basse pression · 27 pression maximum · 28 attente remontée pression · 29 défaut rafraîchissement · 30 température eau maximum · 31 montée HP rapide · 32 T° évaporateur trop basse · 33 T° condenseur minimum · 34 T° compresseur trop haute · 35 dégivrage infructueux · 36 défaut débit chauffage · 37 séchage chape · 38 défaut liaison esclave · 39 info driver · 40 manque pression eau · 41 no data · 42 vieilles données.

## 7. Pistes pour Home Assistant

1. **Lecture** : reprendre `RedPaladin/i_regul_heatpump` (déjà fonctionnel, HACS) et l'enrichir : parser *toutes* les clés retournées et utiliser les libellés `francais` pour nommer les entités automatiquement au lieu d'une table en dur.
2. **Pilotage** (ce qui manque dans le dépôt) : ajouter une entité `climate` par zone et `water_heater` pour l'ECS, qui envoient `{11#DT_zones@<id>&consigne_normal[..]#…&mode_select[..]}` puis relisent avec `{10#}`. Ajouter des `switch`/`button` pour `{12#…}` (autorisation chauffage/rafraîchissement), `{202#}` (reset alarme) et `{203#}` (dégivrage).
3. **Limites** : dépendance totale au cloud i-regul.fr (pas de LAN), pas de chiffrement, une seule requête à la fois → garder un verrou global et un intervalle de scrutation raisonnable (60–300 s).
4. Étape suivante conseillée : **capturer une vraie trame `{10#}` et `{502#}`** depuis ton PC (script fourni : `iregul_probe.py`) pour obtenir la liste exacte des ids/libellés de *ta* installation avant d'écrire l'intégration.

## 8. Points réels de l'installation (capture `{502#}` du 06/09/2026, régulateur version 1205)

Installation détectée via `C@0` : PAC air/eau (`option_capteur_air=1`), inverter, mono, R410, 1 zone de chauffage (`nb_zones=1`), ECS directe avec appoint électrique, rafraîchissement indirect, ballon tampon, compteur d'énergie, pas de piscine ni solaire.

### Sondes analogiques `A@<id>&valeur`

| Clé | Libellé | Unité |
|---|---|---|
| `A@1` | T° ECS | ° |
| `A@3` | T° extérieure | ° |
| `A@4` | T° départ condenseur | ° |
| `A@5` | T° retour condenseur | ° |
| `A@6` | Basse pression | bar |
| `A@7` | Haute pression | bar |
| `A@15` | T° évaporation | ° |
| `A@16` | T° condensation | ° |
| `A@17` | T° évaporateur | ° |
| `A@21` | Débit chauffage | l/m |
| `A@28` | T° entrée évaporateur | ° |
| `A@29` | T° sortie évaporateur | ° |
| `A@100` | T° ballon tampon | ° |
| `A@101` | T° ballon technique | ° |
| `A@102` | T° ballon froid | ° |

### Entrées TOR `I@<id>&valeur`

| Clé | Libellé |
|---|---|
| `I@1` | Sécurité capteur |
| `I@9` | Sécurité chauffage |
| `I@13` | Indication tarif T3 |
| `I@14` | Indication tarif T2 |
| `I@30` | Forcage chaud |
| `I@31` | Forcage froid |
| `I@32` | Forcage ECS |
| `I@33` | Forcage piscine |
| `I@34` | Forcage divers |
| `I@35` | Mode silence |

### Sorties `O@<id>&valeur` (0/1 sauf valeurs analogiques : détendeur, vitesses, PWM)

| Clé | Libellé |
|---|---|
| `O@1` | Eau chaude sanitaire |
| `O@3` | Compresseur |
| `O@4` | Détendeur |
| `O@6` | Distri chaud/froid |
| `O@7` | Appoint ECS |
| `O@8` | Capteur |
| `O@10` | Vanne inversion |
| `O@12` | Cordon chauffant |
| `O@13` | Vitesse capteur |
| `O@25` | Fonction externe |
| `O@26` | Prod chaud/froid |
| `O@27` | Mode silence |
| `O@50` | Pas d'alarme |
| `O@52` | PWM a |
| `O@53` | PWM b |
| `O@54` | PWM c |
| `O@55` | PWM d |
| `O@60` | Circulateur ballon tampon |
| `O@61` | Forcage divers |
| `O@62` | Forcage chaud |
| `O@63` | Forcage froid |
| `O@64` | Forcage ECS |
| `O@72` | Vitesse condenseur |
| `O@97` | Test |
| `O@98` | Chauffage autorisé |
| `O@99` | Rafraichissement autorisé |
| `O@100` | Circulateur technique |
| `O@101` | Appoint chauffage |
| `O@108` | Capteur bis |
| `O@110` | Circulateur zone 1 |
| `O@451` | Vitesse compresseur |
| `O@601` | Eau chaude sanitaire bis |
| `O@606` | Distri chaud/froid bis |
| `O@625` | Fonction externe bis |
| `O@626` | Prod chaud/froid bis |
| `O@627` | Eau chaude sanitaire ' |

### Mesures `M@<id>&valeur` (T1/T2/T3 = tarifs ; seul T1 est utilisé ici)

| Clé | Libellé | Unité |
|---|---|---|
| `M@1` | Surchauffe | ° |
| `M@2` | Delta mesuré | ° |
| `M@3` | Delta maxi | ° |
| `M@4` | Delta moyen | ° |
| `M@5` | Puissance moyenne | kW |
| `M@6` | Puissance calorifique | kW |
| `M@7` | Puissance absorbée | W |
| `M@8` | COP |  |
| `M@11` | delta chauffage | ° |
| `M@12` | delta capteur | ° |
| `M@15` | Puissance accessoires | W |
| `M@16` | Puissance absorbée | kWh |
| `M@17` | Puissance absorbée | MWh |
| `M@19` | Nombre Démarrages |  |
| `M@20` | Conso divers T1 | kWh |
| `M@21` | Conso chauffage T1 | kWh |
| `M@22` | Conso Rafraichissement T1 | kWh |
| `M@23` | Conso ECS T1 | kWh |
| `M@25` | Conso dégivrage T1 | kWh |
| `M@26` | Conso total T1 | kWh |
| `M@27` | Conso compteur 1 T1 | kWh |
| `M@28` | Conso compteur 2 T1 | kWh |
| `M@29` | Conso compteur 3 T1 | kWh |
| `M@50` | Heures divers T1 | h |
| `M@51` | Heures chauffage T1 | h |
| `M@52` | Heures rafraichissement T1 | h |
| `M@53` | Heures ECS T1 | h |
| `M@55` | Heures dégivrage T1 | h |
| `M@56` | Heures total T1 | h |
| `M@57` | Heures appoint T1 | h |
| `M@58` | Heures appoint ECS T1 | h |
| `M@99` | boot |  |
| `M@100` | u(t) | zorg |
| `M@30..49`, `M@60..78` | mêmes compteurs pour tarifs T2/T3 (à 0) | kWh / h |

### Zones présentes `Z@<id>`

| id | zone_nom | mode_select | consignes normal/réduit/hors-gel |
|---|---|---|---|
| 1 | (ecs1) | 0 | 55 / 55 / 10 |
| 2 | (piscine) | 4 | 25 / 20 / 10 |
| 3 | (appoint ecs1) | 0 | 60 / 35 / 10 |
| 4 | (appoint chauffage ) | 0 | 10 / 10 / 10 |
| 5 | (recirculation ecs1) | 0 | 20 / 19 / 10 |
| 6 | ----> 1 | 0 | 80 / 40 / 10 |
| 7 | ----> 2 | 0 | 80 / 40 / 10 |
| 8 | ----> 3 | 0 | 25 / 20 / 10 |
| 9 | ----> 4 | 0 | 20 / 19 / 10 |
| 10 | T° minimum | 0 | 20 / 19 / 10 |
| 11 | zone 1 | 0 | 20 / 19 / 10 |
| 31 | (ecs2) | 4 | 50 / 40 / 10 |
| 32 | (ecs3) | 4 | 50 / 40 / 10 |
| 33 | (appoint ecs2) | 0 | 35 / 35 / 10 |
| 34 | (appoint ecs3) | 0 | 35 / 35 / 10 |

Champs supplémentaires de `Z` renvoyés par `{502#}` : `temperature_max`, `temperature_min`, `zone_active`, `zone_raf`, `zone_circulateur_permanent`, `derog_chaud`, `derog_froid`, etc. La zone 11 (« zone 1 ») est la seule zone de chauffage réelle ; 1 = ECS ; 3 = appoint ECS ; 10 = pente/T° minimum.

### Autres blocs

- `B@0..80` : registres Modbus du variateur de compresseur (« Driver », fréquence moteur, courant, puissance, tension bus DC, T° variateur, compteurs kWh/MWh, codes d'alarme) et des compteurs d'énergie (« Compteur »). Champ `resultat` = valeur lue.
- `P@0..177` : paramètres installateur avec `nom`, `valeur`, `min`, `max`, `pas` (modifiables via `{113#}/{114#}` avec un compte pro).
- `C@0` : toutes les options de configuration + coordonnées client (`nom_client`, `mail_client`… — champs de démonstration ici).
- `mem@0` : `etat`, `sous_etat`, `alarme`, `alarme_flag`, `version`, `date_time` (horodatage côté régulateur, format US `M/D/YYYY h:mm:ss AM/PM`) ; `mem@8..11&CZ` = consignes calculées par zone.

### Observations

- `{10#}` renvoie uniquement les `valeur` (pas de libellés) : ~7 ko, adapté au polling. `{502#}` (~30 ko) sert à la découverte initiale.
- Les valeurs `A/M` sont des flottants bruts ; les `O` sont 0/1 ou une valeur analogique (`O@4` détendeur en pas, `O@13/72` vitesses ventilateur en %, `O@451` vitesse compresseur, `O@52..55` PWM en %).
- `M@16`/`M@17` sont libellés « Puissance absorbée » mais en kWh/MWh : ce sont des compteurs d'énergie absorbée (627.3 kWh + 6 MWh).
- `M@5`/`M@6` sont négatifs en mode rafraîchissement (puissance frigorifique).
- Zone `mode` (effectif) observé : 5 = en production, 4 = repos/auto, 3 = arrêt ; `mode_select` = consigne utilisateur (0 auto … 4 arrêt).