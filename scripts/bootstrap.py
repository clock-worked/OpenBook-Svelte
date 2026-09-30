"""Set up OpenBook dependencies and optional local model assets."""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MAIN_ENV = REPO_ROOT / ".venv"
CHATTERBOX_ENV = REPO_ROOT / "chatterbox_env"
VIBEVOICE_REPO = "FabioSarracino/VibeVoice-Large-Q8"
VIBEVOICE_REVISION = "a92ce0b91e5e4500c5c5af45c1e76d8897ec5893"
VIBEVOICE_DIR = (
    REPO_ROOT / "py_services" / "openbook_parser" / "data" / "VibeVoice-Large-Q8"
)
OPENJEV_MODELS = (
    ("ZefanCai/Open-Jev-9B", "47e966881e489511c0c7f5633a9e1960a676a551", "Open-Jev-9B"),
    ("Qwen/Qwen3.5-9B", "c202236235762e1c871ad0ccb60c8ee5ba337b9a", "Qwen3.5-9B"),
)


def display_command(command: list[str]) -> str:
    """Format a command for readable dry-run output."""
    if os.name == "nt":
        return subprocess.list2cmdline(command)
    return shlex.join(command)


def run(command: list[str], *, cwd: Path = REPO_ROOT, dry_run: bool) -> None:
    """Print and optionally execute a command."""
    print(f"[{cwd}] $ {display_command(command)}")
    if not dry_run:
        subprocess.run(command, cwd=cwd, check=True)


def env_python(env_dir: Path) -> Path:
    """Return the platform-specific Python path for an environment."""
    if os.name == "nt":
        return env_dir / "Scripts" / "python.exe"
    return env_dir / "bin" / "python"


def create_environment(env_dir: Path, *, dry_run: bool) -> Path:
    """Create a virtual environment when it does not already exist."""
    python = env_python(env_dir)
    if not python.exists():
        run([sys.executable, "-m", "venv", str(env_dir)], dry_run=dry_run)
    else:
        print(f"Using existing environment: {env_dir}")
    return python


def install_requirements(
    python: Path,
    requirements: Path,
    *,
    torch_index_url: str | None,
    dry_run: bool,
) -> None:
    """Install one environment's pinned requirements."""
    run([str(python), "-m", "pip", "install", "--upgrade", "pip"], dry_run=dry_run)
    if torch_index_url:
        run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--index-url",
                torch_index_url,
                "torch==2.6.0",
                "torchaudio==2.6.0",
                "torchvision==0.21.0",
            ],
            dry_run=dry_run,
        )
    run(
        [str(python), "-m", "pip", "install", "-r", str(requirements)],
        dry_run=dry_run,
    )


def download_snapshot(
    python: Path,
    repo_id: str,
    revision: str,
    target: Path,
    *,
    dry_run: bool,
) -> None:
    """Download one pinned Hugging Face snapshot."""
    snippet = (
        "from huggingface_hub import snapshot_download; "
        f"print(snapshot_download(repo_id={repo_id!r}, revision={revision!r}, "
        f"local_dir={str(target)!r}))"
    )
    run([str(python), "-c", snippet], dry_run=dry_run)


def parse_args() -> argparse.Namespace:
    """Parse bootstrap options."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print actions only.")
    parser.add_argument("--skip-node", action="store_true", help="Skip npm dependencies.")
    parser.add_argument(
        "--skip-python",
        action="store_true",
        help="Skip the main Python environment.",
    )
    parser.add_argument(
        "--with-chatterbox",
        action="store_true",
        help="Create the isolated Chatterbox TTS environment.",
    )
    parser.add_argument(
        "--with-vibevoice",
        action="store_true",
        help="Download the pinned VibeVoice Q8 model (about 11.6 GB).",
    )
    parser.add_argument(
        "--with-openjev",
        action="store_true",
        help="Download pinned OpenJEV adapter and base model snapshots.",
    )
    parser.add_argument(
        "--torch-index-url",
        help="Optional PyTorch wheel index, for example https://download.pytorch.org/whl/cu124.",
    )
    return parser.parse_args()


def main() -> None:
    """Set up selected dependencies and model assets."""
    args = parse_args()
    if sys.version_info[:2] != (3, 11):
        raise SystemExit("Run bootstrap with Python 3.11; the ML dependency set is pinned to it.")

    run(
        ["git", "submodule", "update", "--init", "--recursive"],
        dry_run=args.dry_run,
    )

    main_python = env_python(MAIN_ENV)
    if not args.skip_python:
        main_python = create_environment(MAIN_ENV, dry_run=args.dry_run)
        install_requirements(
            main_python,
            REPO_ROOT / "py_services" / "requirements.txt",
            torch_index_url=args.torch_index_url,
            dry_run=args.dry_run,
        )

    if not args.skip_node:
        run(["npm", "ci"], cwd=REPO_ROOT / "apps" / "desktop", dry_run=args.dry_run)

    if args.with_chatterbox:
        chatterbox_python = create_environment(CHATTERBOX_ENV, dry_run=args.dry_run)
        install_requirements(
            chatterbox_python,
            REPO_ROOT / "py_services" / "requirements-chatterbox.txt",
            torch_index_url=args.torch_index_url,
            dry_run=args.dry_run,
        )

    if (args.with_vibevoice or args.with_openjev) and not main_python.exists() and not args.dry_run:
        raise SystemExit("The main environment is missing; rerun without --skip-python first.")

    if args.with_vibevoice:
        download_snapshot(
            main_python,
            VIBEVOICE_REPO,
            VIBEVOICE_REVISION,
            VIBEVOICE_DIR,
            dry_run=args.dry_run,
        )

    if args.with_openjev:
        openjev_root = REPO_ROOT / ".models" / "openjev"
        for repo_id, revision, folder_name in OPENJEV_MODELS:
            download_snapshot(
                main_python,
                repo_id,
                revision,
                openjev_root / folder_name,
                dry_run=args.dry_run,
            )
        loader_dir = openjev_root / "Open-Jev"
        if loader_dir.exists():
            print(f"Using existing OpenJEV loader: {loader_dir}")
        else:
            run(
                [
                    "git",
                    "clone",
                    "--depth",
                    "1",
                    "https://github.com/Zefan-Cai/Open-Jev.git",
                    str(loader_dir),
                ],
                dry_run=args.dry_run,
            )

    print("Bootstrap complete.")


if __name__ == "__main__":
    main()
