# Inkscape extension for Line-us plotter

An [Inkscape](https://inkscape.org/) extension to drive a [Line-us](https://www.line-us.com/) plotter.

This is a significantly modified version of the [older Inkscape extension for Line-us](https://github.com/Line-us/Inkscape-Plugin) developed by Anatoly Besplemennov. The original extension was only directly compatible with Inkscape 0.9.2; it no longer worked after Inkscape 1.0 was released. Inkscape 1.0 introduced big changes to the way that extensions are implemented, so this code follows the new requirements.

From the original documentation for the extension:

> The plugin uses machine co-ordinates and does not do any scaling, so refer to the [drawing space diagram](https://github.com/Line-us/Line-us-Programming/blob/master/Documentation/GCodeSpec.pdf) for details. Note that the Inkscape document units must be set to pixels (File/Document Properties/Custom Size/Units). For reference, a sample drawing is [included here.](./LineUsTestDrawing.svg)

TODO: add installation instructions
TODO: add usage instructions

## Useful links

- [fork with some compatibility updates](https://github.com/amyszczepanski/Inkscape-Plugin)
- [Line-us programming reference](https://github.com/Line-us/Line-us-Programming/)
- [Line-us G-code reference](https://github.com/Line-us/Line-us-Programming/blob/master/Documentation/GCodeSpec.md)
