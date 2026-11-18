"""
Reliability helpers for the Colab LoRA training notebook (bundle v3).

Pure standard library at import time; torch / transformers objects are passed
in by the notebook, so everything here is unit-tested locally without a GPU.

    verify_bundle            every bundled file present, size + SHA-256 match
    gpu_preflight            CUDA present, enough VRAM, versions reported
    check_drive_space        enough free Drive quota before training starts
    run_fingerprint          identity of a training run (base revision, pins,
                             data hashes, LoRA + training config)
    find_resume_checkpoint   latest VALID, COMPATIBLE checkpoint on Drive, or
                             None only when no checkpoint was ever written
    restore_checkpoint       copy a Drive checkpoint back to local disk, verified
    publish_checkpoint       local checkpoint -> Drive staging folder -> verify
                             -> completion marker -> rename (never overwrites a
                             valid checkpoint with an incomplete one) -> prune
    make_drive_sync_callback TrainerCallback that publishes every saved checkpoint

A checkpoint on Drive is VALID only if it holds the completion marker, the
marker's fingerprint matches this run, every file's hash matches the marker,
and trainer_state.json reports the step in the folder name.
"""

import hashlib
import json
import os
import re
import shutil
from pathlib import Path

MARKER = "FYP_CHECKPOINT_COMPLETE.json"
STAGING_PREFIX = ".incomplete-"
CHECKPOINT_RE = re.compile(r"^checkpoint-(\d+)$")
# transformers 5.22.2 Trainer + PEFT adapter checkpoint (names verified locally).
REQUIRED_CHECKPOINT_FILES = ("adapter_model.safetensors", "adapter_config.json",
                             "optimizer.pt", "scheduler.pt", "trainer_state.json")
IGNORED_BUNDLE_ENTRIES = {"__pycache__"}


class BundleError(RuntimeError):
    pass


class GpuError(RuntimeError):
    pass


class CheckpointError(RuntimeError):
    pass


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# ---------------------------------------------------------------- bundle

def verify_bundle(bundle_dir):
    """All files listed in BUNDLE_MANIFEST.json exist with the recorded size and SHA-256."""
    bundle_dir = Path(bundle_dir)
    manifest_path = bundle_dir / "BUNDLE_MANIFEST.json"
    if not bundle_dir.is_dir():
        raise BundleError(f"Bundle folder not found: {bundle_dir} (is Google Drive mounted?)")
    if not manifest_path.is_file():
        raise BundleError(f"BUNDLE_MANIFEST.json missing in {bundle_dir}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    problems = []
    for name, expected in manifest["files"].items():
        path = bundle_dir / name
        if not path.is_file():
            problems.append(f"missing: {name}")
        elif path.stat().st_size != expected["bytes"]:
            problems.append(f"size differs: {name}")
        elif sha256_file(path) != expected["sha256"]:
            problems.append(f"SHA-256 differs: {name}")
    allowed = set(manifest["files"]) | {"BUNDLE_MANIFEST.json"} | IGNORED_BUNDLE_ENTRIES
    extra = sorted(p.name for p in bundle_dir.iterdir() if p.name not in allowed)
    if extra:
        problems.append(f"unexpected files: {extra}")
    forbidden = [n for n in manifest.get("excluded", []) if (bundle_dir / n).exists()]
    if forbidden:
        problems.append(f"excluded files present: {forbidden}")
    if problems:
        raise BundleError("REFUSED: bundle verification failed: " + "; ".join(problems))
    return manifest


# ---------------------------------------------------------------- GPU / storage

def gpu_preflight(torch, min_total_gib, min_free_gib):
    """Report the GPU; raise GpuError (never fall back to CPU) if it is missing or too small."""
    if not torch.cuda.is_available():
        raise GpuError("REFUSED: no CUDA GPU. In Colab: Runtime > Change runtime type > GPU (T4 or better).")
    free, total = torch.cuda.mem_get_info(0)
    report = {"gpu": torch.cuda.get_device_name(0),
              "total_vram_gib": round(total / 2 ** 30, 2), "free_vram_gib": round(free / 2 ** 30, 2),
              "torch": torch.__version__, "cuda": torch.version.cuda}
    if report["total_vram_gib"] < min_total_gib:
        raise GpuError(f"REFUSED: {report['gpu']} has {report['total_vram_gib']} GiB VRAM; the unchanged "
                       f"configuration (batch 16, fp32) needs at least {min_total_gib} GiB.")
    if report["free_vram_gib"] < min_free_gib:
        raise GpuError(f"REFUSED: only {report['free_vram_gib']} GiB VRAM free (need {min_free_gib}); "
                       "restart the runtime to release GPU memory.")
    return report


def check_drive_space(path, required_bytes, disk_usage=shutil.disk_usage):
    try:
        free = disk_usage(str(path)).free
    except OSError as error:
        raise CheckpointError(f"REFUSED: cannot read free space of {path}: {error}") from error
    if free < required_bytes:
        raise CheckpointError(f"REFUSED: {free / 2 ** 30:.2f} GiB free on Google Drive; "
                              f"need {required_bytes / 2 ** 30:.2f} GiB for checkpoints and export.")
    return free


# ---------------------------------------------------------------- fingerprint

def run_fingerprint(config, bundle_manifest):
    """Anything that must not change between a checkpoint and the run resuming it."""
    identity = {
        "base_model": config["base_model"]["repo"],
        "revision": config["base_model"]["revision"],
        "packages": {k: config["pinned_packages"][k] for k in ("peft", "transformers", "accelerate")},
        "lora": config["lora"],
        "training": config["training"],
        "checkpointing": config.get("checkpointing"),
        "data": {n: bundle_manifest["files"][n]["sha256"]
                 for n in ("train.jsonl", "validation_dev.jsonl", "manifest.json")},
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- checkpoints

def _file_hashes(folder):
    folder = Path(folder)
    return {p.relative_to(folder).as_posix(): sha256_file(p)
            for p in sorted(folder.rglob("*")) if p.is_file() and p.name != MARKER}


def _step(name):
    match = CHECKPOINT_RE.match(name)
    return int(match.group(1)) if match else None


def check_checkpoint(folder, fingerprint):
    """('valid' | 'incomplete' | 'incompatible', reason) for one checkpoint folder on Drive."""
    folder = Path(folder)
    marker_path = folder / MARKER
    if not marker_path.is_file():
        return "incomplete", "no completion marker (copy interrupted)"
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except ValueError:
        return "incomplete", "unreadable completion marker"
    if marker.get("fingerprint") != fingerprint:
        return "incompatible", "written by a different model / data / config"
    missing = [n for n in REQUIRED_CHECKPOINT_FILES if not (folder / n).is_file()]
    if missing:
        return "incomplete", f"missing {missing}"
    if _file_hashes(folder) != marker.get("files"):
        return "incomplete", "file hashes differ from the completion marker"
    try:
        state = json.loads((folder / "trainer_state.json").read_text(encoding="utf-8"))
    except ValueError:
        return "incomplete", "unreadable trainer_state.json"
    if state.get("global_step") != _step(folder.name) or marker.get("global_step") != _step(folder.name):
        return "incomplete", "global_step does not match the folder name"
    return "valid", "ok"


def find_resume_checkpoint(drive_root, fingerprint, log=print):
    """
    Path of the newest valid compatible checkpoint, or None when the Drive
    folder holds no checkpoint at all (genuine fresh start). Raises
    CheckpointError when checkpoints exist but none can be resumed, or when any
    checkpoint belongs to a different run -- never a silent restart from step 0.
    """
    drive_root = Path(drive_root)
    if not drive_root.exists():
        return None
    entries = [p for p in drive_root.iterdir() if p.is_dir()]
    checkpoints = sorted((p for p in entries if _step(p.name) is not None),
                         key=lambda p: _step(p.name), reverse=True)
    staging = [p.name for p in entries if p.name.startswith(STAGING_PREFIX)]
    if staging:
        log(f"ignoring interrupted copies: {staging}")
    if not checkpoints and not staging:
        return None
    chosen = None
    for folder in checkpoints:
        status, reason = check_checkpoint(folder, fingerprint)
        if status == "incompatible":
            raise CheckpointError(f"REFUSED: {folder.name} is {reason}; move it aside or use a new "
                                  "output folder - it will not be mixed with this run")
        if status == "valid" and chosen is None:
            chosen = folder
        elif status != "valid":
            log(f"skipping {folder.name}: {reason}")
    if chosen is None:
        raise CheckpointError("REFUSED: checkpoints exist on Drive but none is complete and valid; "
                              "training will NOT silently restart from step 0. Inspect "
                              f"{drive_root} and decide explicitly.")
    log(f"resuming from {chosen.name}")
    return chosen


def restore_checkpoint(drive_checkpoint, local_root):
    """Copy a valid Drive checkpoint to local disk and verify every file again."""
    drive_checkpoint, local_root = Path(drive_checkpoint), Path(local_root)
    target = local_root / drive_checkpoint.name
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(drive_checkpoint, target)
    marker = json.loads((target / MARKER).read_text(encoding="utf-8"))
    if _file_hashes(target) != marker["files"]:
        raise CheckpointError(f"REFUSED: local copy of {drive_checkpoint.name} does not match its marker")
    return target


def restore_for_resume(drive_root, local_root, fingerprint, log=print):
    """Restore the latest checkpoint and (if different) the best one its trainer_state refers to."""
    chosen = find_resume_checkpoint(drive_root, fingerprint, log=log)
    if chosen is None:
        return None
    local = restore_checkpoint(chosen, local_root)
    state = json.loads((local / "trainer_state.json").read_text(encoding="utf-8"))
    best = state.get("best_model_checkpoint")
    if best and Path(best).name != chosen.name:
        best_drive = Path(drive_root) / Path(best).name
        status, reason = check_checkpoint(best_drive, fingerprint)
        if status != "valid":
            raise CheckpointError(f"REFUSED: best checkpoint {best_drive.name} is not restorable ({reason})")
        restore_checkpoint(best_drive, local_root)
    return local


def publish_checkpoint(local_checkpoint, drive_root, fingerprint, keep_last, protect=(),
                       log=print):
    """
    Copy a finished local checkpoint to Drive safely:
      staging folder -> verify hashes -> write marker -> rename to checkpoint-N
    A valid existing checkpoint-N is never overwritten. Then keep only the
    newest `keep_last` valid checkpoints plus any in `protect` (the best one).
    """
    local_checkpoint, drive_root = Path(local_checkpoint), Path(drive_root)
    step = _step(local_checkpoint.name)
    if step is None:
        raise CheckpointError(f"not a checkpoint folder: {local_checkpoint}")
    missing = [n for n in REQUIRED_CHECKPOINT_FILES if not (local_checkpoint / n).is_file()]
    if missing:
        raise CheckpointError(f"REFUSED: local {local_checkpoint.name} is missing {missing}")
    drive_root.mkdir(parents=True, exist_ok=True)
    final = drive_root / local_checkpoint.name
    if final.exists():
        status, _ = check_checkpoint(final, fingerprint)
        if status == "valid":
            log(f"{final.name} already on Drive and valid")
            return final
        aside = drive_root / f"{STAGING_PREFIX}replaced-{final.name}"
        if aside.exists():
            shutil.rmtree(aside)
        final.rename(aside)              # keep evidence; it is ignored by resume
    staging = drive_root / f"{STAGING_PREFIX}{local_checkpoint.name}"
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(local_checkpoint, staging)
    source_hashes = _file_hashes(local_checkpoint)
    if _file_hashes(staging) != source_hashes:
        shutil.rmtree(staging)
        raise CheckpointError(f"REFUSED: Drive copy of {local_checkpoint.name} differs from local; not published")
    marker = {"fingerprint": fingerprint, "global_step": step, "files": source_hashes}
    (staging / MARKER).write_text(json.dumps(marker, indent=1), encoding="utf-8")
    staging.rename(final)
    status, reason = check_checkpoint(final, fingerprint)
    if status != "valid":
        raise CheckpointError(f"REFUSED: published {final.name} failed verification: {reason}")
    prune_checkpoints(drive_root, fingerprint, keep_last, protect=set(protect) | {final.name}, log=log)
    log(f"published {final.name} to Drive")
    return final


def prune_checkpoints(drive_root, fingerprint, keep_last, protect=(), log=print):
    """Delete older VALID checkpoints of this run beyond the newest keep_last (never protected ones)."""
    valid = sorted((p for p in Path(drive_root).iterdir()
                    if p.is_dir() and _step(p.name) is not None
                    and check_checkpoint(p, fingerprint)[0] == "valid"),
                   key=lambda p: _step(p.name), reverse=True)
    for folder in valid[keep_last:]:
        if folder.name not in protect:
            shutil.rmtree(folder)
            log(f"pruned {folder.name}")


def make_drive_sync_callback(callback_base, drive_root, fingerprint, keep_last, log=print):
    """TrainerCallback publishing each checkpoint right after the Trainer finished writing it."""

    class DriveCheckpointSync(callback_base):
        def on_save(self, args, state, control, **kwargs):
            local = Path(args.output_dir) / f"checkpoint-{state.global_step}"
            protect = {Path(state.best_model_checkpoint).name} if state.best_model_checkpoint else set()
            publish_checkpoint(local, drive_root, fingerprint, keep_last, protect=protect, log=log)
            return control

    return DriveCheckpointSync()


def estimate_checkpoint_bytes(trainable_parameters):
    """fp32 adapter + AdamW (2 states) + ~1 MB of scheduler / RNG / trainer state."""
    return trainable_parameters * 4 * 3 + (1 << 20)
