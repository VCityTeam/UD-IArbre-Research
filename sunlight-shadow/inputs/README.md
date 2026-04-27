Place your 3D Tiles input dataset here.

Expected structure:

```text
inputs/
  tileset.json
  tiles/
    tile_0.b3dm
    tile_1.b3dm
```

You can change the mounted host directory through `INPUTS_FOLDER` in `.env`.
If your `tileset.json` is inside a nested folder, keep `INPUTS_FOLDER` as the mount root and set `INPUT_PATH` to the in-container tileset directory, for example `/inputs/lyon_dataset`.
