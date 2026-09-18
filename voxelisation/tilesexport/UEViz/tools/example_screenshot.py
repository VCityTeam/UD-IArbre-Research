"""Editor-side script: position the camera and take a screenshot.

    python ue_drive.py example_screenshot.py

Output lands in IarbreVoxels/Saved/Screenshots/WindowsEditor/.
Edit CAMERA_POS / CAMERA_ROT / NAME for repeatable named viewpoints -
the basis for before/after comparisons and automated captures.
"""
import unreal

CAMERA_POS = unreal.Vector(0.0, -60000.0, 40000.0)   # cm, Unreal frame
CAMERA_ROT = unreal.Rotator(0.0, -35.0, 90.0)        # roll, pitch, yaw
NAME = "viewpoint_oblique.png"

unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem) \
      .set_level_viewport_camera_info(CAMERA_POS, CAMERA_ROT)
unreal.AutomationLibrary.take_high_res_screenshot(1920, 1080, NAME)
print("screenshot requested:", NAME)
