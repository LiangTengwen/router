"""Package the working tree, submit through the configured H20 head, and collect evidence."""

import argparse
import hashlib
import os
import re
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=12", "vllm-h20-head"]
BASE = "/home/david_cwq/ltw"


def remote(command, **options):
    return subprocess.run([*SSH, command], check=True, **options)


def main(output):
    output.mkdir(exist_ok=False, parents=True)
    # Verify scheduler + canonical existing user directory before creating any remote file.
    remote(
        f'test "$(realpath {BASE})" = {BASE} && test -d {BASE} && scontrol ping && sinfo -h -p h20'
    )
    run = remote(
        f"umask 077 && mktemp -d {BASE}/session-admission-XXXXXXXX",
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert re.fullmatch(re.escape(BASE) + r"/session-admission-[a-zA-Z0-9]+", run), run
    (output / "remote.txt").write_text(run + "\n")
    with (ROOT / "tests/session_admission/h20-health.sh").open("rb") as stream:
        remote(f"bash -s -- head {run} > {run}/head-health.log 2>&1", stdin=stream)
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp)
        # Bundle pinned dependencies on the workstation; no shared cluster registry writes.
        with (stage / "vendor.toml").open("wb") as source_config:
            subprocess.run(
                [
                    "cargo",
                    "vendor",
                    "--locked",
                    "--manifest-path",
                    str(ROOT / "Cargo.toml"),
                    "vendor",
                ],
                cwd=stage,
                stdout=source_config,
                check=True,
            )
        config = stage / "vendor.toml"
        config.write_text(
            config.read_text().replace(
                'directory = "vendor"', f'directory = "{run}/vendor"'
            )
        )
        source = subprocess.check_output(
            ["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=ROOT
        )
        with tarfile.open(stage / "source.tar.gz", "w:gz", compresslevel=2) as tar:
            for name in filter(None, os.fsdecode(source).split("\0")):
                file = ROOT / name
                if file.is_file():
                    tar.add(file, "src/" + name, recursive=False)
            tar.add(stage / "vendor", "vendor")
            tar.add(config, "vendor.toml")
        archive = stage / "source.tar.gz"
        with archive.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        (output / "input.sha256").write_text(digest + "  input.tar.gz\n")
        for name, path in (
            ("input.tar.gz", archive),
            ("input.sha256", output / "input.sha256"),
            ("h20.sbatch", ROOT / "tests/session_admission/h20.sbatch"),
            ("h20-health.sh", ROOT / "tests/session_admission/h20-health.sh"),
        ):
            with path.open("rb") as stream:
                remote(f"umask 077 && cat > {run}/{name}", stdin=stream)
    job = (
        remote(
            f"cd {run} && sbatch --parsable --chdir={run} --output={run}/slurm-%j.log h20.sbatch",
            capture_output=True,
            text=True,
        )
        .stdout.strip()
        .split(";")[0]
    )
    assert job.isdigit(), job
    (output / "job.txt").write_text(job + "\n")
    print(f"Submitted {job} in {run}", flush=True)
    while remote(
        f"squeue -h -j {job} -o %T", capture_output=True, text=True
    ).stdout.strip():
        time.sleep(45)
    with (output / "accounting.txt").open("wb") as out:
        remote(
            f"sacct -j {job} --format=JobID,State,ExitCode,Elapsed,AllocTRES",
            stdout=out,
        )
    with (output / "evidence.tar.gz").open("xb") as out:
        remote(f"cat {run}/evidence-{job}.tar.gz", stdout=out)
    with (output / "slurm.log").open("wb") as stream:
        remote(f"cat {run}/slurm-{job}.log", stdout=stream)
    remote(
        f'test -f {run}/result-{job}/results.json && grep -q \'"check": "completed"\' {run}/result-{job}/results.json'
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args().output)
