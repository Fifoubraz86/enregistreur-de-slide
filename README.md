# Capture réunion

Logiciel Windows qui :

1. **enregistre une fenêtre précise** (Zoom, Teams, onglet Chrome…) **même si vous travaillez sur autre chose** : la fenêtre peut rester cachée derrière les autres ;
2. **enregistre le son de l'ordinateur** (le son de la réunion, propre, sans micro) ;
3. **photographie automatiquement chaque diapo** du diaporama qui défile ;
4. **crée un PowerPoint « maison »** à partir de ces photos, avec votre modèle (.potx : logo, bandeau CHU…) ;
5. **se couple au Plaud** : chaque phrase de la transcription est rattachée à la diapo affichée à ce moment-là (dans les notes du PowerPoint et dans un compte-rendu Word « diapo + ce qui a été dit »).

## Installation (une seule fois)

1. Installez **Python 3.11 ou plus récent** depuis <https://www.python.org/downloads/> en cochant **« Add python.exe to PATH »**.
2. Téléchargez ce dossier (bouton vert *Code* → *Download ZIP* sur GitHub), puis décompressez-le.
3. Double-cliquez sur **`installer.bat`** (quelques minutes).

Ensuite, double-cliquez sur **`lancer.bat`** pour démarrer le logiciel.

ffmpeg est fourni automatiquement (paquet `imageio-ffmpeg`), vous n'avez rien à installer de plus.

> Pour obtenir un vrai `CaptureReunion.exe` (à copier sur un autre PC sans Python) : double-cliquez sur `construire_exe.bat`. Le résultat se trouve dans `dist\CaptureReunion\`.

## Utilisation

### 1. Enregistrer

- Choisissez la fenêtre dans la liste (Zoom, Teams et les navigateurs apparaissent en premier).
- Facultatif, mais conseillé : **« Définir la zone des diapos… »**, puis tracez un rectangle autour de la diapo, sans les vignettes des participants. Les visages qui bougent ne déclenchent alors plus de fausses détections.
- Cliquez sur **Démarrer**. Vous pouvez passer à autre chose : la dernière diapo détectée s'affiche en miniature.

Deux règles à respecter :

| Situation | Que faire |
|---|---|
| Fenêtre **réduite** dans la barre des tâches | Windows ne la dessine plus, donc l'image se fige. Laissez-la ouverte derrière les autres fenêtres, ou sur un 2ᵉ écran. Le logiciel affiche un avertissement si cela arrive. |
| **Onglet Chrome** | Seul l'onglet actif d'une fenêtre est dessiné. Détachez l'onglet de la réunion dans sa propre fenêtre (glissez l'onglet hors de la barre d'onglets). |

À l'arrêt, un dossier est créé dans `Vidéos\Capture reunion\` :

```
2026-10-03_14h30 - Zoom Meeting\
├── video.mp4          vidéo + son
├── son.mp3            son seul (à importer dans l'appli Plaud si besoin)
├── diapos\            diapo_001_00h00m12s.png, diapo_002_…
└── session.json       heure de début, horodatage des diapos
```

### 2. Diapos et compte-rendu

Ouvrez l'onglet **« 2. Diapos et compte-rendu »** :

- **Extraire les diapos de la vidéo** : refait la détection, par exemple avec une autre zone, ou sur une vidéo enregistrée ailleurs (bouton « Ouvrir une autre vidéo… »).
- **Modèle PowerPoint** : votre .potx ou .pptx. Chaque photo est centrée sur la diapo. Si votre modèle comporte une disposition avec un espace réservé « Image », la photo est placée dans ce cadre.
- **Transcription Plaud** : exportez la transcription depuis l'appli Plaud **avec les horodatages** (TXT, SRT ou DOCX).
- **Heure de début du Plaud** : l'heure à laquelle vous avez lancé l'enregistrement Plaud (visible dans l'appli). Le logiciel connaît l'heure de début de la vidéo et calcule le décalage. « Ajustement fin » permet de corriger de quelques secondes.
- **Créer le PowerPoint** : une diapo par photo. Les notes du présentateur contiennent l'heure d'affichage et, si une transcription est fournie, ce qui a été dit.
- **Créer le compte-rendu Word** : chaque diapo suivie de la transcription correspondante.

## Comment la détection des diapos fonctionne

Une image est analysée chaque seconde :

- quand l'image reste **stable environ 2 s** et que le contenu précédent a **disparu**, c'est une nouvelle diapo ;
- quand le contenu précédent est **toujours là avec des éléments en plus** (puces animées), la photo de la diapo est remplacée par la version la plus complète ;
- un **passage vidéo** (image qui bouge en permanence) n'est jamais photographié ;
- un **retour à une diapo déjà vue** n'est pas photographié une 2ᵉ fois, mais il est noté pour la synchronisation avec la transcription.

## Ligne de commande (facultatif)

```
.venv\Scripts\python -m capture_reunion fenetres
.venv\Scripts\python -m capture_reunion enregistrer Zoom --zone 0,0,0.8,1
.venv\Scripts\python -m capture_reunion extraire "chemin\video.mp4"
.venv\Scripts\python -m capture_reunion pptx "dossier de session" --modele modele.potx --transcription plaud.txt --debut-plaud 14:29:50
.venv\Scripts\python -m capture_reunion compte-rendu "dossier de session" --transcription plaud.txt --debut-plaud 14:29:50
```

## Cadre légal

Prévenez les participants avant d'enregistrer (RGPD). En RCP, les données de santé imposent un stockage sécurisé (pas de cloud grand public) et une durée de conservation limitée.

## Pour les développeurs

- Code dans `capture_reunion/` : `recorder.py` (capture Windows Graphics Capture + ffmpeg), `audio.py` (WASAPI loopback), `slides.py` (détection), `export.py` (PowerPoint/Word), `transcript.py` (Plaud), `gui.py` (PySide6).
- Tests : `python -m pytest`. Ils tournent aussi hors Windows : la capture est simulée par une fausse fenêtre.
