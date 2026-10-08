from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from pxr import Usd, UsdPhysics, UsdUtils

__all__ = ["export_embodichain_result", "export_embodichain_usdc"]


def export_embodichain_result(run_dir: Path | str, result: Path | str) -> Path | None:
    """Publish the final recorded USDZ when it contains one articulation.

    Compile attempts remain native numbered USDZ files. This helper is called
    only after a successful run has selected one of them as ``record.result``.
    It writes the one EmbodiChain compatibility copy and adds that path to the
    final manifest.
    """

    run_path = Path(run_dir).expanduser().resolve()
    result_path = Path(result)
    source_path = (
        (run_path / result_path).resolve()
        if not result_path.is_absolute()
        else result_path.resolve()
    )
    result_root = (run_path / "result").resolve()
    try:
        source_path.relative_to(result_root)
    except ValueError:
        raise ValueError("the recorded result must stay inside the run result directory") from None
    if source_path.suffix.lower() != ".usdz" or not source_path.is_file():
        raise ValueError("the recorded result must point to an existing USDZ file")

    manifest = result_root / "model.json"
    if not manifest.is_file():
        return None
    stage = Usd.Stage.Open(str(source_path))
    if stage is None:
        raise RuntimeError(f"OpenUSD could not open {source_path}")
    assembly = _single_assembly(stage)
    roots = _articulation_roots(assembly)
    if len(roots) != 1:
        return None

    destination = result_root / "usdc" / source_path.stem / "model.usdc"
    export_embodichain_usdc(source_path, destination)
    try:
        _record_manifest_file(manifest, result_root, destination)
    except BaseException:
        shutil.rmtree(destination.parent, ignore_errors=True)
        raise

    usdc_root = destination.parent.parent
    for stale in usdc_root.iterdir():
        if stale != destination.parent:
            shutil.rmtree(stale, ignore_errors=True)
    return destination


def export_embodichain_usdc(
    source: Path | str,
    destination: Path | str,
    *,
    copy_assets: bool = False,
) -> Path:
    """Publish an Articraft stage with the hierarchy expected by EmbodiChain.

    Articraft authors ``ArticulationRootAPI`` on the selected root rigid body
    and keeps ``/World`` as the default prim. DexSim instead discovers an
    articulation by traversing downward from the prim carrying that API, so
    sibling bodies and joints are invisible to it. The compatibility copy makes
    the assembly both the default prim and the articulation root while leaving
    the native USDZ untouched for Articraft's own viewer.

    A USDZ source is extracted first so packaged texture references remain
    valid in the standalone compatibility output. Files next to an unpackaged
    source layer can optionally be copied for the same reason.

    Args:
        source: Source USD, USDA, USDC, or USDZ stage.
        destination: Destination ``.usdc`` path.
        copy_assets: Copy sibling texture/assets into the output directory.

    Returns:
        The resolved destination path.

    Raises:
        ValueError: If the stage does not contain exactly one articulated assembly.
        RuntimeError: If OpenUSD cannot open or publish the stage.
    """

    source_path = Path(source).expanduser().resolve()
    destination_path = Path(destination).expanduser().resolve()
    if source_path.suffix.lower() not in {".usd", ".usda", ".usdc", ".usdz"}:
        raise ValueError("EmbodiChain compatibility input must be USD, USDA, USDC, or USDZ")
    if destination_path.suffix.lower() != ".usdc":
        raise ValueError("EmbodiChain compatibility output must use the .usdc suffix")

    if source_path.suffix.lower() == ".usdz":
        with tempfile.TemporaryDirectory(prefix="mini-articraft-usdz-") as extract_dir:
            extracted = Path(extract_dir) / "package"
            if not UsdUtils.ExtractUsdzPackage(
                str(source_path), str(extracted), True, False, False
            ):
                raise RuntimeError(f"OpenUSD could not extract {source_path}")
            root_layer = extracted / "model.usdc"
            if not root_layer.is_file():
                raise RuntimeError("Articraft USDZ package has no model.usdc root layer")
            return export_embodichain_usdc(root_layer, destination_path, copy_assets=True)

    source_stage = Usd.Stage.Open(str(source_path))
    if source_stage is None:
        raise RuntimeError(f"OpenUSD could not open {source_path}")
    assembly = _single_assembly(source_stage)
    roots = _articulation_roots(assembly)
    if len(roots) != 1:
        raise ValueError(
            "EmbodiChain compatibility export requires exactly one articulation root; "
            f"found {len(roots)}"
        )

    destination_path.parent.parent.mkdir(parents=True, exist_ok=True)
    temporary_dir = Path(
        tempfile.mkdtemp(
            prefix=f".{destination_path.parent.name}.tmp-",
            dir=destination_path.parent.parent,
        )
    )
    temporary_path = temporary_dir / destination_path.name
    try:
        if copy_assets:
            _copy_layer_assets(source_path.parent, temporary_dir, source_path)

        if not source_stage.Export(str(temporary_path)):
            raise RuntimeError(f"OpenUSD could not export {destination_path}")
        compatible_stage = Usd.Stage.Open(str(temporary_path))
        if compatible_stage is None:
            raise RuntimeError(f"OpenUSD could not reopen {temporary_path}")
        compatible_assembly = compatible_stage.GetPrimAtPath(assembly.GetPath())
        compatible_root = compatible_stage.GetPrimAtPath(roots[0].GetPath())
        compatible_root.RemoveAPI(UsdPhysics.ArticulationRootAPI)
        compatible_assembly.ApplyAPI(UsdPhysics.ArticulationRootAPI)
        compatible_stage.SetDefaultPrim(compatible_assembly)
        compatible_stage.GetRootLayer().Save()
        _validate_compatible_stage(temporary_path, assembly.GetPath().pathString)
        if destination_path.parent.exists():
            _publish_into_existing_directory(temporary_dir, destination_path.parent)
        else:
            temporary_dir.replace(destination_path.parent)
    finally:
        shutil.rmtree(temporary_dir, ignore_errors=True)
    return destination_path


def _articulation_roots(assembly: Usd.Prim) -> list[Usd.Prim]:
    return [prim for prim in Usd.PrimRange(assembly) if prim.HasAPI(UsdPhysics.ArticulationRootAPI)]


def _record_manifest_file(manifest: Path, result_root: Path, destination: Path) -> None:
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("files"), dict):
        raise ValueError(f"manifest has no files object: {manifest}")
    payload["files"]["embodichain_usdc"] = destination.relative_to(result_root).as_posix()
    temporary = manifest.with_name(f".{manifest.name}.tmp")
    try:
        temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        temporary.replace(manifest)
    finally:
        temporary.unlink(missing_ok=True)


def _single_assembly(stage: Usd.Stage) -> Usd.Prim:
    default_prim = stage.GetDefaultPrim()
    if default_prim and default_prim.GetChild("rigid_bodies"):
        return default_prim
    assemblies = [prim for prim in default_prim.GetChildren() if prim.GetChild("rigid_bodies")]
    if len(assemblies) != 1:
        raise ValueError(
            "EmbodiChain compatibility export requires exactly one rigid-body assembly; "
            f"found {len(assemblies)}"
        )
    return assemblies[0]


def _copy_layer_assets(source_dir: Path, destination_dir: Path, source_layer: Path) -> None:
    for path in source_dir.iterdir():
        if path == source_layer:
            continue
        target = destination_dir / path.name
        if path.is_dir():
            shutil.copytree(path, target)
        elif path.is_file():
            shutil.copy2(path, target)


def _publish_into_existing_directory(source_dir: Path, destination_dir: Path) -> None:
    conflicts = [
        path.name for path in source_dir.iterdir() if (destination_dir / path.name).exists()
    ]
    if conflicts:
        raise FileExistsError(
            "EmbodiChain compatibility output would overwrite existing files: "
            + ", ".join(sorted(conflicts))
        )
    for path in source_dir.iterdir():
        path.replace(destination_dir / path.name)


def _validate_compatible_stage(path: Path, expected_root: str) -> None:
    stage = Usd.Stage.Open(str(path))
    if stage is None:
        raise RuntimeError(f"OpenUSD could not reopen {path}")
    root = stage.GetDefaultPrim()
    if not root or root.GetPath().pathString != expected_root:
        raise RuntimeError("EmbodiChain USDC default prim does not match the assembly")
    if not root.HasAPI(UsdPhysics.ArticulationRootAPI):
        raise RuntimeError("EmbodiChain USDC assembly has no ArticulationRootAPI")
    if not root.GetChild("rigid_bodies") or not root.GetChild("joints"):
        raise RuntimeError("EmbodiChain USDC assembly has no rigid_bodies or joints scope")
