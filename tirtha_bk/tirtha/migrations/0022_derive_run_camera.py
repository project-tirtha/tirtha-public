"""
Derive the unified camera fields from the two legacy sets (internal design notes).

82 runs carry framing an operator tuned by hand, in two viewer-specific
vocabularies: `rotaX/Y/Z` for Google `<model-viewer>` on the photogrammetry
path, and `camUp*`/`initCamPos*`/`initCamLookAt*` for gsplat on the GS path.
Neither viewer understood the other's numbers.

This maps both onto one camera model. It reads the legacy columns rather than
replacing them -- they stay, so the derivation can be corrected and re-run if a
mapping turns out to be wrong on a particular run.
"""

from django.db import migrations

DEFAULT_UP = [0.0, 1.0, 0.0]


def _triple(a, b, c, fallback):
    """
    Carry the stored values across, whatever they are.

    There is deliberately no attempt to tell "framed by hand" from "left at the
    default". `initCamPos*` defaults to (0, 0, 5) and is non-null, so a run
    nobody touched is indistinguishable from one an operator framed to exactly
    that -- and it does not matter: whatever value the old viewer used is the
    framing that produced the look people have already seen and shared.

    The fallback applies only when every component is genuinely NULL.
    """
    values = [a, b, c]
    if all(v is None for v in values):
        return list(fallback)
    return [float(v) if v is not None else 0.0 for v in values]


def derive_camera(apps, schema_editor):
    Run = apps.get_model("tirtha", "Run")
    updates = []
    for run in Run.objects.all().iterator():
        position = _triple(
            run.initCamPosX, run.initCamPosY, run.initCamPosZ, fallback=[]
        )
        target = _triple(
            run.initCamLookAtX, run.initCamLookAtY, run.initCamLookAtZ, fallback=[]
        )
        up = _triple(run.camUpX, run.camUpY, run.camUpZ, fallback=DEFAULT_UP)

        # rotaX/Y/Z are integer degrees applied to the model. They were only
        # ever meaningful on the aV path, but a GS run with non-zero values had
        # them set deliberately, so they are carried across regardless of kind.
        rotation = [
            float(run.rotaX or 0),
            float(run.rotaY or 0),
            float(run.rotaZ or 0),
        ]

        run.cam_position = position
        run.cam_target = target
        run.cam_up = up
        run.model_rotation = rotation
        updates.append(run)

    if updates:
        Run.objects.bulk_update(
            updates,
            ["cam_position", "cam_target", "cam_up", "model_rotation"],
            batch_size=200,
        )


def clear_camera(apps, schema_editor):
    """Reversible: the derived fields carry nothing the legacy ones do not."""
    Run = apps.get_model("tirtha", "Run")
    Run.objects.update(
        cam_position=[], cam_target=[], cam_up=[], model_rotation=[]
    )


class Migration(migrations.Migration):
    dependencies = [("tirtha", "0021_unified_run_camera")]
    operations = [migrations.RunPython(derive_camera, clear_camera)]
