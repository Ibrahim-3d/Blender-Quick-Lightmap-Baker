# Quick Lightmap Baker

A Blender add-on for quickly generating reusable lightmap bakes for web/Three.js workflows.

## Features

- Creates or reuses a second UV map named `Light Map`
- Smart unwraps and packs selected/static objects into one lightmap atlas
- Bakes lighting using Cycles
- Creates a duplicated baked-output collection
- Assigns one material named `newlightmap`
- Plugs the baked image into an Emission shader
- Hides/unchecks the original unbaked source collection
- Designed for repeatable editing and rebaking

## Installation

1. Download this repository as a ZIP.
2. In Blender, go to:

```text
Edit > Preferences > Add-ons > Install
Select the ZIP file.
Enable Quick Lightmap Baker.
Open the 3D View sidebar:
N Panel > Lightmap
Basic Workflow
Select the source mesh objects.
Set Bake Scope to Selected.
Click Bake And Build New Baked Collection.
Export the Baked_Lightmap_Output collection for Three.js/glTF.
Output

The add-on creates:

Unbaked_Source
Baked_Lightmap_Output

The baked output objects use:

UVMap → Baked Image → Emission → Material Output
Three.js Usage
const texture = textureLoader.load("LightMap_Bake.png");
texture.flipY = false;

material.map = texture;
// Or use the exported emission material directly from glTF.
Notes

For the cleanest result, apply object scale before baking:

Object > Apply > Scale

Unapplied scale may create inconsistent UV density.
```
