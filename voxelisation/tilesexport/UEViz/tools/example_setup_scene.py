"""Editor-side script: build (or repair) a voxel-tileset scene safely.

Run from a terminal while the editor is open:

    python ue_drive.py example_setup_scene.py

Idempotent: reuses existing actors when present. Applies the
crash-prevention settings BEFORE the URL triggers tile loading, and parks
the viewport camera above the model so screen-space error stays sane.
"""
import unreal

TILESET_URL = "http://localhost:8765/tileset.json"
ORIGIN_LAT, ORIGIN_LON, ORIGIN_H = 45.7327, 4.7416, 280.0   # Run1 centre

ues = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
world = ues.get_editor_world()

# -- georeference (exactly one, at the dataset centre) -------------------
geos = unreal.GameplayStatics.get_all_actors_of_class(world, unreal.CesiumGeoreference)
georef = geos[0] if geos else actors.spawn_actor_from_class(
    unreal.CesiumGeoreference, unreal.Vector(0, 0, 0))
georef.set_editor_property("origin_latitude", ORIGIN_LAT)
georef.set_editor_property("origin_longitude", ORIGIN_LON)
georef.set_editor_property("origin_height", ORIGIN_H)

# -- camera above the model BEFORE loading -------------------------------
ues.set_level_viewport_camera_info(
    unreal.Vector(0.0, 0.0, 250000.0), unreal.Rotator(0.0, -90.0, 0.0))

# -- tileset actor with safe settings, THEN the URL ----------------------
tss = unreal.GameplayStatics.get_all_actors_of_class(world, unreal.Cesium3DTileset)
ts = tss[0] if tss else actors.spawn_actor_from_class(
    unreal.Cesium3DTileset, unreal.Vector(0, 0, 0))
ts.set_editor_property("georeference", georef)   # avoid the default-georef trap
ts.set_editor_property("create_physics_meshes", False)
ts.set_editor_property("maximum_screen_space_error", 48.0)
ts.set_editor_property("maximum_cached_bytes", 512 * 1024 * 1024)
ts.set_editor_property("tileset_source", unreal.TilesetSource.FROM_URL)
ts.set_editor_property("url", TILESET_URL)
ts.refresh_tileset()

print("scene ready:", ts.get_name(), "->", TILESET_URL)
