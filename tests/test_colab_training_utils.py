"""
Colab reliability helpers (bundle v3): bundle verification, GPU preflight,
Drive space, run fingerprint, and checkpoint publish / discovery / resume.
No GPU, no training, no network: fake torch and fake checkpoint folders.
"""

import copy
import hashlib
import json
import shutil
import tempfile
import types
import unittest
from collections import namedtuple
from pathlib import Path
from unittest.mock import patch

from training import colab_training_utils as ctu

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "training" / "test_input_lora_config.json").read_text(encoding="utf-8"))
MANIFEST = {"files": {n: {"sha256": c * 64, "bytes": 1} for n, c in
                      (("train.jsonl", "a"), ("validation_dev.jsonl", "b"), ("manifest.json", "c"))}}
FP = ctu.run_fingerprint(CONFIG, MANIFEST)
SILENT = lambda *_: None                                         # noqa: E731


def make_local_checkpoint(root, step, best=None, payload=b"x"):
    folder = Path(root) / f"checkpoint-{step}"
    folder.mkdir(parents=True)
    for name in ctu.REQUIRED_CHECKPOINT_FILES:
        if name != "trainer_state.json":
            (folder / name).write_bytes(payload + name.encode() + str(step).encode())
    (folder / "rng_state.pth").write_bytes(b"rng" + str(step).encode())
    (folder / "trainer_state.json").write_text(json.dumps(
        {"global_step": step, "best_model_checkpoint": best}), encoding="utf-8")
    return folder


class BundleVerificationTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bundle = Path(self.tmp.name) / "bundle"
        self.bundle.mkdir()
        files = {}
        for name, data in (("train.jsonl", b"t\n"), ("validation_dev.jsonl", b"v\n"), ("x.py", b"p")):
            (self.bundle / name).write_bytes(data)
            files[name] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        (self.bundle / "BUNDLE_MANIFEST.json").write_text(json.dumps(
            {"files": files, "excluded": ["validation_heldout.jsonl"]}), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_valid_bundle_and_pycache_ignored(self):
        (self.bundle / "__pycache__").mkdir()
        self.assertEqual(len(ctu.verify_bundle(self.bundle)["files"]), 3)

    def test_failures_are_clear(self):
        cases = {
            "missing": lambda: (self.bundle / "x.py").unlink(),
            "SHA-256 differs": lambda: (self.bundle / "x.py").write_bytes(b"q"),
            "size differs": lambda: (self.bundle / "x.py").write_bytes(b"pp"),
            "unexpected files": lambda: (self.bundle / "extra.txt").write_text("e"),
            "excluded files present": lambda: (self.bundle / "validation_heldout.jsonl").write_text("h"),
        }
        for expected, damage in cases.items():
            with self.subTest(expected):
                self.tearDown()
                self.setUp()
                damage()
                with self.assertRaisesRegex(ctu.BundleError, expected):
                    ctu.verify_bundle(self.bundle)

    def test_missing_folder_mentions_drive_mount(self):
        with self.assertRaisesRegex(ctu.BundleError, "Google Drive mounted"):
            ctu.verify_bundle(Path(self.tmp.name) / "nope")


def fake_torch(available=True, total_gib=14.7, free_gib=14.5):
    cuda = types.SimpleNamespace(
        is_available=lambda: available,
        mem_get_info=lambda i: (int(free_gib * 2 ** 30), int(total_gib * 2 ** 30)),
        get_device_name=lambda i: "Tesla T4")
    return types.SimpleNamespace(cuda=cuda, __version__="2.8.0+cu126",
                                 version=types.SimpleNamespace(cuda="12.6"))


class GpuAndStorageTests(unittest.TestCase):

    def test_gpu_report(self):
        report = ctu.gpu_preflight(fake_torch(), 12, 11)
        self.assertEqual(report, {"gpu": "Tesla T4", "total_vram_gib": 14.7, "free_vram_gib": 14.5,
                                  "torch": "2.8.0+cu126", "cuda": "12.6"})

    def test_gpu_refusals(self):
        for torch, message in ((fake_torch(available=False), "no CUDA GPU"),
                               (fake_torch(total_gib=8.0, free_gib=8.0), "needs at least 12"),
                               (fake_torch(free_gib=4.0), "VRAM free")):
            with self.assertRaisesRegex(ctu.GpuError, message):
                ctu.gpu_preflight(torch, 12, 11)

    def test_thresholds_come_from_config(self):
        c = CONFIG["checkpointing"]
        self.assertEqual((c["min_total_vram_gib"], c["min_free_vram_gib"]), (12, 11))

    def test_drive_space(self):
        usage = namedtuple("usage", "total used free")
        ok = lambda p: usage(0, 0, 5 * 2 ** 30)                    # noqa: E731
        low = lambda p: usage(0, 0, 2 ** 20)                       # noqa: E731

        def broken(p):
            raise OSError("no drive")
        self.assertEqual(ctu.check_drive_space("/d", 2 ** 30, disk_usage=ok), 5 * 2 ** 30)
        with self.assertRaisesRegex(ctu.CheckpointError, "GiB free"):
            ctu.check_drive_space("/d", 2 ** 30, disk_usage=low)
        with self.assertRaisesRegex(ctu.CheckpointError, "cannot read free space"):
            ctu.check_drive_space("/d", 2 ** 30, disk_usage=broken)

    def test_storage_estimate(self):
        per = ctu.estimate_checkpoint_bytes(CONFIG["lora"]["expected_trainable_parameters"])
        self.assertAlmostEqual(per / 1e6, 31.7, delta=0.1)
        worst = per * (CONFIG["checkpointing"]["keep_last_on_drive"] + 2)    # + best + staging copy
        self.assertLess(worst + 320e6, CONFIG["checkpointing"]["required_drive_free_gib"] * 2 ** 30)


class FingerprintTests(unittest.TestCase):

    def test_any_identity_change_changes_fingerprint(self):
        self.assertEqual(FP, ctu.run_fingerprint(copy.deepcopy(CONFIG), copy.deepcopy(MANIFEST)))
        for mutate in (lambda c, m: c["base_model"].update(revision="0" * 40),
                       lambda c, m: c["pinned_packages"].update(peft="0.21.3"),
                       lambda c, m: c["lora"].update(r=8),
                       lambda c, m: c["training"].update(learning_rate=0.002),
                       lambda c, m: c["checkpointing"].update(keep_last_on_drive=3),
                       lambda c, m: m["files"]["train.jsonl"].update(sha256="f" * 64)):
            c, m = copy.deepcopy(CONFIG), copy.deepcopy(MANIFEST)
            mutate(c, m)
            self.assertNotEqual(FP, ctu.run_fingerprint(c, m))


class CheckpointTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.local, self.drive, self.restore = base / "local", base / "drive" / "checkpoints", base / "restore"
        self.local.mkdir()
        self.restore.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def publish(self, step, best=None, keep=2, protect=()):
        local = make_local_checkpoint(self.local, step, best=best)
        return ctu.publish_checkpoint(local, self.drive, FP, keep, protect=protect, log=SILENT)

    def names(self):
        return sorted(p.name for p in self.drive.iterdir())

    def test_no_checkpoint_folder_means_fresh_start(self):
        self.assertIsNone(ctu.find_resume_checkpoint(self.drive, FP, log=SILENT))
        self.drive.mkdir(parents=True)
        self.assertIsNone(ctu.find_resume_checkpoint(self.drive, FP, log=SILENT))

    def test_publish_writes_verified_marker_and_resume_picks_latest(self):
        self.publish(500)
        self.publish(1000)
        self.assertEqual(self.names(), ["checkpoint-1000", "checkpoint-500"])
        marker = json.loads((self.drive / "checkpoint-1000" / ctu.MARKER).read_text(encoding="utf-8"))
        self.assertEqual((marker["fingerprint"], marker["global_step"]), (FP, 1000))
        self.assertIn("optimizer.pt", marker["files"])
        self.assertIn("rng_state.pth", marker["files"])
        self.assertEqual(ctu.find_resume_checkpoint(self.drive, FP, log=SILENT).name, "checkpoint-1000")

    def test_retention_keeps_last_two_plus_best(self):
        best = str(self.local / "checkpoint-500")
        for step in (500, 1000, 1500, 2000):
            self.publish(step, best=best, protect={"checkpoint-500"})
        self.assertEqual(self.names(), ["checkpoint-1500", "checkpoint-2000", "checkpoint-500"])
        self.publish(2500)                                   # best no longer protected
        self.assertEqual(self.names(), ["checkpoint-2000", "checkpoint-2500"])

    def test_restore_for_resume_restores_latest_and_best_with_optimizer_state(self):
        best = "/content/checkpoints/checkpoint-500"
        self.publish(500, best=best)
        self.publish(1000, best=best, protect={"checkpoint-500"})
        self.publish(1500, best=best, protect={"checkpoint-500"})
        local = ctu.restore_for_resume(self.drive, self.restore, FP, log=SILENT)
        self.assertEqual(local.name, "checkpoint-1500")
        self.assertEqual(sorted(p.name for p in self.restore.iterdir()), ["checkpoint-1500", "checkpoint-500"])
        for name in ("optimizer.pt", "scheduler.pt", "adapter_model.safetensors", "rng_state.pth"):
            self.assertEqual((local / name).read_bytes(),
                             (self.local / "checkpoint-1500" / name).read_bytes())
        self.assertEqual(json.loads((local / "trainer_state.json").read_text())["global_step"], 1500)

    def test_incomplete_newest_falls_back_to_previous_valid(self):
        self.publish(500)
        self.publish(1000)
        (self.drive / "checkpoint-1000" / ctu.MARKER).unlink()          # interrupted before marker
        shutil.copytree(self.drive / "checkpoint-500", self.drive / ".incomplete-checkpoint-1500")
        logs = []
        chosen = ctu.find_resume_checkpoint(self.drive, FP, log=logs.append)
        self.assertEqual(chosen.name, "checkpoint-500")
        self.assertTrue(any("skipping checkpoint-1000" in l for l in logs))
        self.assertTrue(any("interrupted copies" in l for l in logs))

    def test_tampered_or_wrong_step_checkpoint_is_not_resumed(self):
        self.publish(500)
        self.publish(1000)
        (self.drive / "checkpoint-1000" / "optimizer.pt").write_bytes(b"corrupt")
        self.assertEqual(ctu.check_checkpoint(self.drive / "checkpoint-1000", FP)[0], "incomplete")
        (self.drive / "checkpoint-500").rename(self.drive / "checkpoint-700")
        self.assertIn("global_step", ctu.check_checkpoint(self.drive / "checkpoint-700", FP)[1])

    def test_only_incomplete_checkpoints_refuse_instead_of_restarting(self):
        self.publish(500)
        (self.drive / "checkpoint-500" / "scheduler.pt").unlink()
        with self.assertRaisesRegex(ctu.CheckpointError, "NOT silently restart"):
            ctu.find_resume_checkpoint(self.drive, FP, log=SILENT)
        shutil.rmtree(self.drive / "checkpoint-500")
        (self.drive / ".incomplete-checkpoint-500").mkdir()
        with self.assertRaisesRegex(ctu.CheckpointError, "NOT silently restart"):
            ctu.find_resume_checkpoint(self.drive, FP, log=SILENT)

    def test_incompatible_checkpoint_is_refused(self):
        self.publish(500)
        other = ctu.run_fingerprint(dict(CONFIG, lora=dict(CONFIG["lora"], r=8)), MANIFEST)
        with self.assertRaisesRegex(ctu.CheckpointError, "different model / data / config"):
            ctu.find_resume_checkpoint(self.drive, other, log=SILENT)

    def test_best_checkpoint_missing_is_refused(self):
        self.publish(500, best="/content/checkpoints/checkpoint-200")
        with self.assertRaisesRegex(ctu.CheckpointError, "best checkpoint checkpoint-200"):
            ctu.restore_for_resume(self.drive, self.restore, FP, log=SILENT)

    def test_valid_checkpoint_never_overwritten_invalid_one_moved_aside(self):
        first = self.publish(500)
        original = (first / "optimizer.pt").read_bytes()
        shutil.rmtree(self.local / "checkpoint-500")
        make_local_checkpoint(self.local, 500, payload=b"DIFFERENT")
        ctu.publish_checkpoint(self.local / "checkpoint-500", self.drive, FP, 2, log=SILENT)
        self.assertEqual((first / "optimizer.pt").read_bytes(), original)
        (first / ctu.MARKER).unlink()                                     # now invalid
        ctu.publish_checkpoint(self.local / "checkpoint-500", self.drive, FP, 2, log=SILENT)
        self.assertIn(".incomplete-replaced-checkpoint-500", self.names())
        self.assertEqual(ctu.check_checkpoint(first, FP)[0], "valid")

    def test_corrupted_drive_copy_is_not_published(self):
        local = make_local_checkpoint(self.local, 500)
        real_copytree = shutil.copytree

        def corrupting_copytree(src, dst, **kw):
            real_copytree(src, dst, **kw)
            (Path(dst) / "optimizer.pt").write_bytes(b"truncated")
        with patch.object(ctu.shutil, "copytree", corrupting_copytree):
            with self.assertRaisesRegex(ctu.CheckpointError, "differs from local"):
                ctu.publish_checkpoint(local, self.drive, FP, 2, log=SILENT)
        self.assertEqual(self.names(), [])

    def test_publish_refuses_incomplete_local_checkpoint(self):
        local = make_local_checkpoint(self.local, 500)
        (local / "optimizer.pt").unlink()
        with self.assertRaisesRegex(ctu.CheckpointError, "missing"):
            ctu.publish_checkpoint(local, self.drive, FP, 2, log=SILENT)

    def test_trainer_callback_publishes_after_each_save(self):
        class Base:
            pass
        callback = ctu.make_drive_sync_callback(Base, self.drive, FP, 2, log=SILENT)
        args = types.SimpleNamespace(output_dir=str(self.local))
        for step in (500, 1000, 1500):
            make_local_checkpoint(self.local, step, best=str(self.local / "checkpoint-500"))
            state = types.SimpleNamespace(global_step=step,
                                          best_model_checkpoint=str(self.local / "checkpoint-500"))
            self.assertEqual(callback.on_save(args, state, "control"), "control")
        self.assertEqual(self.names(), ["checkpoint-1000", "checkpoint-1500", "checkpoint-500"])


if __name__ == "__main__":
    unittest.main()
