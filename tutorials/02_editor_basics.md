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

- **Toolbar (top):** Play, Save, Load, Export, add Cube / Plane / Sphere, Import a model, Library, Particle, Terrain, Building Editor.
- **Hierarchy (left):** every object in the scene. Click a name to select it.
- **Viewport (middle):** the 3D world. Its **View** button (top-left corner) switches between *Lit*, *Lit + Wireframe* and *Wireframe*, and turns the grid on or off.
- **Inspector (right):** the settings of the selected object. **Click any value to type a new one.**

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
- [[Delete]] deletes the selection; the **Duplicate** button copies it.
- [[Ctrl]] + [[P]] starts and stops Play.

> TIP: For exact placement click a value in the Inspector, for example **Y +1.50**, type the number and press Enter. One unit is one metre; the player is 1.8 m tall.

## Seeing the wireframe

Click **View** in the top-left corner of the viewport:

- **Lit**: the normal view.
- **Lit + Wireframe**: the lit scene with every triangle's edges drawn on top. Good for checking how detailed a model is.
- **Wireframe**: edges only, seen through everything (x-ray), so you can find hidden objects.

The selected object's edges are orange. **Show grid** hides or shows the ground grid. The View menu only changes what you see in the editor; exported games always look *Lit*.

## Colours

Click **Color** under Material and type three numbers from 0 to 1: `1 0 0` is red, `0.2 0.6 1` sky blue, `1 0.8 0.1` gold. **Metal** 1 makes it metallic; low **Rough** makes it shiny.

## Try it

1. Click **Cube** and give it Scale 2 / 2 / 2.
2. Make it gold and shiny (Color `1 0.8 0.1`, Metal 1, Rough 0.2).
3. Click **Save**. Your level is stored in the project's `main.heaven3d` file.
