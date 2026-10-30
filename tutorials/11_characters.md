---
title: Characters: people, NPCs and enemies
level: Intermediate
minutes: 20
summary: Create MakeHuman characters, dress them, give them Mixamo animations, and make walking NPCs and enemies with foot IK.
---
# Characters: people, NPCs and enemies

The **MakeHuman Characters** package gives you realistic people with hundreds of free (CC0) clothes, hair styles and skins. They walk paths, chase the player and keep their feet on the ground on stairs and slopes.

> TIP: Everything in the package is CC0. Use it in commercial games with no credit and no fees.

## Install it

1. In the Hub, open **Packages** and press **Install** on *MakeHuman Characters*.
2. **Projects > + New project** and pick **Character Town**, or press **Add to project** on the package card for one of your own projects.

## Make a character

1. Open the project. In the Hierarchy header, open **Packages** and choose **Characters: new character**.
2. Click the character. The **Character Creator** opens beside the Inspector.
3. **Body**: drag *Gender*, *Age*, *Muscle*, *Weight* and *Height*. Open a region (*Torso*, *Legs* ...) for detail sliders.
4. **Face**: press *Random face* until you like it, then fine-tune the nose, mouth, eyes ...
5. **Clothes**: pick a slot (*full*, *top*, *bottom*, *feet*, *head* ...) and click items to put them on or take them off. Press **look** on a worn item for its other colours.
6. **Hair** and **Skin**: choose a hair style and colour, eyes, eyebrows, a skin and a tint.

> TIP: **Random**, **Male** and **Female** at the top make a whole new person. The **Style** button switches them between civilian, soldier, viking, zombie and sci-fi.

## Make NPCs walk

1. **Packages > Characters: new NPC walker (+ path)**. You get a person and four blue points.
2. Move the blue points with the gizmo ([[W]]). Select one and use **Packages > NPC path: add point** to add more.
3. Press **Play**. The NPC walks from point to point, waits a moment at each one, and waves when you come close.
4. In the Creator's **AI** tab, change *loop* to *pingpong* or *random*, and set the speed and waiting time.

> TIP: **Characters: crowd of 6 on a path** fills a square with people in one click.

## Add an enemy

1. Add a **Player Spawn** from the **Library** if your scene has none.
2. **Packages > Characters: new enemy (foot IK)**. In the **AI** tab, give it a path to patrol.
3. Press **Play**. When it sees you it runs at you and attacks. Shoot it (click) and it reacts, then falls when its health runs out.

> WATCH OUT: Enemies with *Needs line of sight* on can't see you through walls. Turn it off for enemies that always know where you are.

## Use Mixamo animations

1. On [mixamo.com](https://www.mixamo.com), pick an animation, then **Download**: *FBX Binary*, *Without Skin*, *30* fps. Tick **In Place** for walking and running.
2. Save the files in a folder called **animations** next to your project, with names like `Walking.fbx`, `Running.fbx`, `Punching.fbx`, `Dying.fbx`.
3. **Packages > Characters: use project 'animations' folder**. Every character now uses them.
4. Or select one character, open **Anim** and press **Load...** next to a slot. Use the preview buttons to watch it in the viewport.

## Feet on the ground

Foot IK is on for every character. Walk an NPC up stairs or a ramp (Character Town has both) and watch the knees bend and the feet tilt to the slope. Switch it off per character in the **Anim** tab.
