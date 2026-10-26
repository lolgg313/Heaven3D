---
title: Share your game
level: Beginner
minutes: 10
template: coin_collector
summary: Export a stand-alone game for Windows and Linux and send it to friends.
---
# Share your game

## Play without the editor

In the Hub, press **▶ Play** on a project. The game starts in its own window with no editor panels, exactly like your players will see it.

## Export a stand-alone game

1. In the Hub, press **Export** on your project (or **⋯ > Export game…**).
2. Type the name of your game. It becomes the window title and the name of the program.
3. Turn on **Windows** and/or **Linux**. You can build both from either system.
4. Pick the window size and where the build should be saved.
5. Press **Export** and wait about a minute.

You get one folder per platform, plus a ready-to-share `.zip` of each:

- **Windows**: `My Game-Windows/My Game.exe`. Double-click to play.
- **Linux**: `My Game-Linux/My Game`. Double-click it, or run `./My Game`. `install-shortcut.sh` adds it to the app menu.

Both carry the Heaven3D logo as their icon and start with a short Heaven3D splash.

> NOTE: The **Export** button in the editor's toolbar does the same thing: it saves your project and builds every platform into the `Heaven3D Builds` folder.

## Your friends install nothing

Every build contains its own copy of Python and all the libraries the engine needs. Your friend unzips it anywhere (Desktop, Downloads, a USB stick) and plays. No Python, no `pip`, no setup.

## Files are always portable

You don't need FILE BURNER any more. Heaven3D 2.7 does the same work every time you save:

- every model, texture and sound is stored as a path **inside** the project folder (`assets/...`) with `/` slashes, which work on Windows and Linux
- anything you imported from outside the project (Downloads, Desktop, another drive) is copied into `assets/imported/` together with its side files (`.bin`, textures, `.mtl`)
- on load, Heaven3D finds files even if their letter case changed or the project moved to another computer

So you can zip the **project folder** itself and open it on any other PC with Heaven3D.

## Save games travel with the game

Use the save-game helpers in your scripts:

```python
self.engine.save_game("slot1", {"coins": 7, "level": 2})
data = self.engine.load_game("slot1", default={})
```

Saves are written safely: they're never half-written, and a backup of the previous save is kept. In an exported game they live in the game's own `saves` folder, so copying the game folder to another computer keeps the player's progress. If that folder can't be written to, saves go to the player's data folder instead (`%APPDATA%\Heaven3D\<Game>` on Windows, `~/.local/share/heaven3d/<Game>` on Linux).

> TIP: Test the zip on a second computer before you announce your release.
