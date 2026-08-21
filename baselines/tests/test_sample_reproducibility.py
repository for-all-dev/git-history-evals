"""Test for reproducibility of sample_disjoint.py across separate processes."""

import json
import subprocess
import sys
from pathlib import Path
import tempfile
import hashlib

# Add pipeline to path
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PIPELINE_DIR = REPO_ROOT / "pipeline"


def test_sample_disjoint_seed_reproducibility() -> None:
    """
    Test that the same seed produces byte-identical sample.jsonl across two separate processes.
    This verifies that the seed-based randomness is properly implemented and not affected by
    PEP 456 PYTHONHASHSEED randomization.
    """
    # Use two temporary directories to simulate independent runs
    with tempfile.TemporaryDirectory() as tmpdir1:
        with tempfile.TemporaryDirectory() as tmpdir2:
            tmpdir1_path = Path(tmpdir1)
            tmpdir2_path = Path(tmpdir2)

            # Create temporary test data that sample_disjoint.py can use
            # We need to create artifacts/lean-ablate structure with challenges
            test_repo_name = "test_repo"
            artifacts_dir = Path(tmpdir1_path.parent) / "artifacts" / "lean-ablate" / test_repo_name
            artifacts_dir.mkdir(parents=True, exist_ok=True)

            # Create a sample challenges.jsonl file with 10 test challenges
            challenges_file = artifacts_dir / "challenges.jsonl"
            test_challenges = []
            for i in range(10):
                test_challenges.append({
                    "challenge_file_content": f"challenge {i} content to be hashed",
                    "challenge_id": f"ch_{i}",
                    "solution_file_content": f"solution {i}",
                })

            with open(challenges_file, "w") as f:
                for ch in test_challenges:
                    f.write(json.dumps(ch) + "\n")

            # Create registry file
            registry_file = PIPELINE_DIR / "registry_all.tsv"
            # We need to back up the original if it exists
            registry_backup = None
            if registry_file.exists():
                import shutil
                registry_backup = registry_file.read_text()

            # Create a minimal registry with our test repo
            with open(registry_file, "w") as f:
                f.write(f"{test_repo_name}\thttps://test.repo/url\n")

            try:
                # Run sample_disjoint.py twice with the same seed in separate processes
                seed = 42
                out_dir1 = tmpdir1_path / "sample1"
                out_dir2 = tmpdir2_path / "sample2"

                # First run
                result1 = subprocess.run(
                    [
                        sys.executable,
                        str(PIPELINE_DIR / "sample_disjoint.py"),
                        str(out_dir1),
                        "5",
                        "--seed",
                        str(seed),
                    ],
                    capture_output=True,
                    text=True,
                )
                assert result1.returncode == 0, f"First run failed: {result1.stderr}"

                # Second run (in a separate process)
                result2 = subprocess.run(
                    [
                        sys.executable,
                        str(PIPELINE_DIR / "sample_disjoint.py"),
                        str(out_dir2),
                        "5",
                        "--seed",
                        str(seed),
                    ],
                    capture_output=True,
                    text=True,
                )
                assert result2.returncode == 0, f"Second run failed: {result2.stderr}"

                # Read the generated sample.jsonl files
                sample_file1 = out_dir1 / "sample.jsonl"
                sample_file2 = out_dir2 / "sample.jsonl"

                assert sample_file1.exists(), f"Sample file 1 not found: {sample_file1}"
                assert sample_file2.exists(), f"Sample file 2 not found: {sample_file2}"

                # Compare byte-for-byte
                content1 = sample_file1.read_bytes()
                content2 = sample_file2.read_bytes()

                assert (
                    content1 == content2
                ), f"Sample files differ:\nFile 1 size: {len(content1)}\nFile 2 size: {len(content2)}"

                # Also verify manifest.json contains the seed
                manifest1 = json.loads((out_dir1 / "manifest.json").read_text())
                manifest2 = json.loads((out_dir2 / "manifest.json").read_text())

                for entry in manifest1:
                    assert "seed" in entry, f"Seed not found in manifest entry: {entry}"
                    assert entry["seed"] == seed, f"Seed mismatch in manifest1: {entry['seed']} != {seed}"

                for entry in manifest2:
                    assert "seed" in entry, f"Seed not found in manifest entry: {entry}"
                    assert entry["seed"] == seed, f"Seed mismatch in manifest2: {entry['seed']} != {seed}"

            finally:
                # Restore registry file
                if registry_backup is not None:
                    with open(registry_file, "w") as f:
                        f.write(registry_backup)
                elif registry_file.exists():
                    registry_file.unlink()

                # Clean up artifacts
                if artifacts_dir.exists():
                    import shutil
                    shutil.rmtree(artifacts_dir.parent)
