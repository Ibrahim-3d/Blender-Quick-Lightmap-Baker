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
