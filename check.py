from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
APP_VERSION_PATH = ROOT / "app" / "version.py"
UPDATE_JSON_PATH = ROOT / "update.json"
DIST_EXE_PATH = ROOT / "dist" / "ClipFlow.exe"
RELEASE_DIR = ROOT / "release"
RELEASE_ZIP_PATH = RELEASE_DIR / "ClipFlow.zip"
SPEC_PATH = ROOT / "ClipFlow.spec"
VENV_PYTHON = ROOT / "venv" / "Scripts" / "python.exe"
DOWNLOAD_URL = "https://github.com/maiviet283/meida_harvester/releases/latest/download/ClipFlow.zip"
RELEASE_URL = "https://github.com/maiviet283/meida_harvester/releases/latest"


def main() -> int:
    args = parse_args()
    if args.publish and (args.no_bump or args.no_build):
        raise SystemExit("--publish cannot be combined with --no-bump or --no-build")
    check_required_files()
    if args.publish:
        check_publish_prerequisites()

    python = VENV_PYTHON if VENV_PYTHON.exists() else Path(sys.executable)

    run_step([str(python), "-m", "unittest", "discover", "-s", "tests", "-v"], "Run tests")

    if args.test_only:
        return 0

    old_version_content = APP_VERSION_PATH.read_text(encoding="utf-8")
    old_update_content = UPDATE_JSON_PATH.read_text(encoding="utf-8")
    did_bump = False

    if not args.no_bump:
        new_version = bump_patch_version(read_app_version())
        write_app_version(new_version)
        write_update_manifest(new_version)
        did_bump = True
        print(f"[version] bumped to {new_version} and marked as required", flush=True)
    else:
        new_version = read_app_version()
        print(f"[version] kept at {new_version}", flush=True)

    try:
        run_step([str(python), "-m", "compileall", "app"], "Compile app")

        if args.no_build:
            print("[build] skipped by --no-build", flush=True)
            return 0

        ensure_icon(python)
        ensure_pyinstaller(python)
        run_step(
            [
                str(python),
                "-m",
                "PyInstaller",
                "--noconfirm",
                "--clean",
                str(SPEC_PATH),
            ],
            "Build executable",
        )
        make_release_zip()
    except BaseException:
        if did_bump:
            APP_VERSION_PATH.write_text(old_version_content, encoding="utf-8")
            UPDATE_JSON_PATH.write_text(old_update_content, encoding="utf-8")
            print("[version] build failed; rolled version files back", flush=True)
        raise

    print(f"[release] ready: {RELEASE_ZIP_PATH}", flush=True)
    if args.publish:
        publish_release(new_version)
        return 0

    print("[next] upload release/ClipFlow.zip to GitHub Releases, then push app/version.py and update.json", flush=True)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run tests, force-bump version, build ClipFlow.exe, and create release/ClipFlow.zip."
    )
    parser.add_argument("--test-only", action="store_true", help="Run tests only, skip version bump and build.")
    parser.add_argument("--no-bump", action="store_true", help="Do not increment APP_VERSION/update.json.")
    parser.add_argument("--no-build", action="store_true", help="Skip PyInstaller build and zip creation.")
    parser.add_argument(
        "--publish",
        action="store_true",
        help="After build, commit version files, push a git tag, create a GitHub Release, upload the zip, and push the branch.",
    )
    return parser.parse_args()


def check_required_files() -> None:
    missing = [
        path
        for path in (APP_VERSION_PATH, UPDATE_JSON_PATH, SPEC_PATH, ROOT / "app.py")
        if not path.exists()
    ]
    if missing:
        raise SystemExit("Missing required files: " + ", ".join(str(path) for path in missing))


def read_app_version() -> str:
    content = APP_VERSION_PATH.read_text(encoding="utf-8")
    match = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', content)
    if not match:
        raise SystemExit("Could not find APP_VERSION in app/version.py")
    return match.group(1)


def bump_patch_version(version: str) -> str:
    parts = [int(part) for part in version.strip().lstrip("v").split(".")]
    while len(parts) < 3:
        parts.append(0)
    parts[2] += 1
    return ".".join(str(part) for part in parts[:3])


def write_app_version(version: str) -> None:
    content = APP_VERSION_PATH.read_text(encoding="utf-8")
    content = re.sub(r'APP_VERSION\s*=\s*"[^"]+"', f'APP_VERSION = "{version}"', content)
    APP_VERSION_PATH.write_text(content, encoding="utf-8")


def write_update_manifest(version: str) -> None:
    content = json.loads(UPDATE_JSON_PATH.read_text(encoding="utf-8"))
    content["latest_version"] = version
    content["minimum_supported_version"] = version
    content["download_url"] = DOWNLOAD_URL
    content["release_url"] = RELEASE_URL
    if not content.get("message"):
        content["message"] = "Có bản cập nhật mới cho ClipFlow. Vui lòng cập nhật để tiếp tục sử dụng ổn định."
    UPDATE_JSON_PATH.write_text(
        json.dumps(content, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def ensure_icon(python: Path) -> None:
    icon_path = ROOT / "assets" / "icon.ico"
    if icon_path.exists():
        return
    print("[icon] assets/icon.ico not found — generating...", flush=True)
    run_step([str(python), str(ROOT / "generate_icon.py")], "Generate icon")


def ensure_pyinstaller(python: Path) -> None:
    result = subprocess.run(
        [str(python), "-m", "PyInstaller", "--version"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode == 0:
        return
    run_step([str(python), "-m", "pip", "install", "pyinstaller"], "Install PyInstaller")


def check_publish_prerequisites() -> None:
    if shutil.which("git") is None:
        raise SystemExit("Git is required for --publish")
    if shutil.which("gh") is None:
        raise SystemExit("GitHub CLI is required for --publish. Install it, then run: gh auth login")

    run_step(["git", "rev-parse", "--is-inside-work-tree"], "Check git repository")
    branch = git_output(["git", "branch", "--show-current"]).strip()
    if not branch:
        raise SystemExit("--publish requires a normal branch, not detached HEAD")

    status = git_output(["git", "status", "--porcelain"]).strip()
    if status:
        raise SystemExit(
            "Working tree is not clean. Commit your code changes first, then run check.py --publish.\n"
            + status
        )

    run_step(["gh", "auth", "status"], "Check GitHub login")


def publish_release(version: str) -> None:
    tag = f"v{version}"
    branch = git_output(["git", "branch", "--show-current"]).strip()

    if git_output(["git", "tag", "--list", tag]).strip():
        raise SystemExit(f"Git tag already exists: {tag}")

    run_step(["git", "add", str(APP_VERSION_PATH), str(UPDATE_JSON_PATH)], "Stage release version")
    run_step(["git", "commit", "-m", f"Release {tag}"], "Commit release version")
    run_step(["git", "tag", tag], "Create release tag")
    run_step(["git", "push", "origin", tag], "Push release tag")
    run_step(
        [
            "gh",
            "release",
            "create",
            tag,
            str(RELEASE_ZIP_PATH),
            "--title",
            f"ClipFlow {tag}",
            "--notes",
            f"ClipFlow {tag}",
        ],
        "Create GitHub Release",
    )
    run_step(["git", "push", "origin", branch], "Push branch")
    print(f"[publish] released {tag} and pushed {branch}", flush=True)


def make_release_zip() -> None:
    if not DIST_EXE_PATH.exists():
        raise SystemExit(f"Build did not create {DIST_EXE_PATH}")
    RELEASE_DIR.mkdir(parents=True, exist_ok=True)
    if RELEASE_ZIP_PATH.exists():
        RELEASE_ZIP_PATH.unlink()
    with zipfile.ZipFile(RELEASE_ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(DIST_EXE_PATH, "ClipFlow.exe")


def run_step(command: list[str], title: str) -> None:
    print(f"\n== {title} ==", flush=True)
    result = subprocess.run(command, cwd=ROOT, check=False)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def git_output(command: list[str]) -> str:
    result = subprocess.run(command, cwd=ROOT, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or result.stdout.strip() or str(result.returncode))
    return result.stdout


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
