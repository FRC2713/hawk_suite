#!/usr/bin/env python3
"""Build a local-only test candidate and record its immutable identity and inputs."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/authentication"
INPUTS = ["Dockerfile.candidate", "userinfo-subject.patch", "userinfo_subject_test.go",
          "apply-suite-patch.py", "hawk_slack.go", "hawk_slack_test.go"]


def input_hashes():
    # Normalize checkout line endings; Docker copies these text files verbatim,
    # while git checkouts on Windows may use CRLF.
    return {name: hashlib.sha256((FIXTURE / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
            for name in INPUTS}


def main():
    with tempfile.TemporaryDirectory(prefix="hawk-auth-build-") as directory:
        iidfile = Path(directory) / "image-id"
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        subprocess.run(["docker", "build", "--label", "io.hawk.suite-source-revision=" + revision, "--iidfile", str(iidfile), "-f",
                        str(FIXTURE / "Dockerfile.candidate"), "-t", "hawk-auth-candidate:local",
                        str(FIXTURE)], check=True)
        info = json.loads(subprocess.check_output(["docker", "image", "inspect", iidfile.read_text().strip()], encoding="utf-8"))[0]
        image_id = info["Id"]
        receipt = {"schema": 1, "image_id": image_id, "manifest_digest": info.get("Descriptor", {}).get("digest"),
                   "platform": info["Os"] + "/" + info["Architecture"],
                   "source_sha256": input_hashes(), "published": False}
        (FIXTURE / "candidate.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        print("Local candidate pinned: " + image_id)


if __name__ == "__main__":
    main()
