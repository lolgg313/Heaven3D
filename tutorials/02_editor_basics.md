---
title: Editor basics
level: Beginner
minutes: 15
template: empty
summary: Camera controls, selecting, moving and editing objects.
---
# Editor basics

Click **Create practice project** above, then press **Open**. The editor appears with a sun, clouds and an ocean.

## The screen

- **Menu bar (top):** File, Edit, Create, View, Play, Tools, Packages, Help. Everything the editor can do is in here, with its shortcut next to it.
- **Toolbar (under it):** Play, Save, Open, the Move / Rotate / Scale tools, quick buttons to add a cube, sphere, plane, light, player spawn and enemy spawn, **Import**, and the three world tools **Terrain**, **Particles** and **Buildings**.
- **Outliner (left):** every object in the scene. Click a name to select it, type in the box above it to search, right-click for Rename / Duplicate / Delete, drag one object onto another to make it its child.
- **Viewport (middle):** the 3D world. The buttons in its top-left corner switch between *Lit*, *Lit + Wire* and *Wire*.
- **Inspector (right):** the settings of the selected object. **Drag a number to change it, or double-click it to type a value.**
- **Bottom panel:** tabs for the **Console** (messages and a Python prompt), **Assets** (the project's files), **Script** (a code editor), **Graph** (visual scripts), **Material**, **Animation** and the **API** reference.

## Moving the camera

Hold the **right mouse button** while you fly:

- Right mouse + move the mouse — look around
- Right mouse + [[W]] [[A]] [[S]] [[D]] — fly forward, left, back, right
- Right mouse + [[Q]] / [[E]] — down / up
- [[Shift]] — fly 4× faster; right mouse + wheel changes the speed
- Mouse wheel — zoom
- [[Alt]] + left drag — orbit
- Middle drag — pan
- [[F]] — jump to the selected object (use it when you get lost)

> NOTE: On a Mac trackpad, the right mouse button is a two-finger click (or [[Ctrl]] + click).

## Selecting and changing things

- Left-click an object to select it.
- [[W]] move, [[E]] rotate, [[R]] scale — then drag the coloured handles.
- [[Delete]] deletes the selection, [[Ctrl]] + [[D]] duplicates it, [[F2]] renames it.
- [[Ctrl]] + [[Z]] undoes, [[Ctrl]] + [[Y]] redoes.
- [[Ctrl]] + [[P]] (or [[F5]]) starts and stops Play.

> TIP: For exact placement use **Location**, **Rotation** and **Scale** in the Inspector's Transform panel: double-click a number, type the value and press Enter. One unit is one metre; the player is 1.8 m tall.

## Seeing the wireframe

Use the three buttons in the top-left corner of the viewport (or the **View** menu, or [[F4]] to go to the next one):

- **Lit**: the normal view.
- **Lit + Wire**: the lit scene with every triangle's edges drawn on top. Good for checking how detailed a model is.
- **Wire**: edges only, seen through everything (x-ray), so you can find hidden objects.

The selected object's edges are orange. **View > Grid** hides or shows the ground grid. The view mode only changes what you see while editing; Play and exported games always look *Lit*.

## Colours

In the Inspector's **Material** panel click the colour swatch, or type three numbers from 0 to 1: `1 0 0` is red, `0.2 0.6 1` sky blue, `1 0.8 0.1` gold. **Metal** 1 makes it metallic; low **Rough** makes it shiny.

## Try it

1. Click the cube button in the toolbar (or **Create > Cube**) and give it Scale 2 / 2 / 2.
2. Make it gold and shiny (Color `1 0.8 0.1`, Metal 1, Rough 0.2).
3. Press [[Ctrl]] + [[S]] (or the Save button). Your level is stored in the project's `main.heaven3d` file.
