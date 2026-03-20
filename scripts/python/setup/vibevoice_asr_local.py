"""Helper CLI for local VibeVoice-ASR setup and inference."""

import argparse
import shlex
import subprocess
import sys
from pathlib import Path


DEFAULT_REPO_URL = "https://github.com/microsoft/VibeVoice.git"
DEFAULT_MODEL_ID = "microsoft/VibeVoice-ASR"


def run_cmd(command: list[str], cwd: Path | None = None, dry_run: bool = False) -> None:
    """Print and optionally execute a shell command."""
    printable = " ".join(shlex.quote(part) for part in command)
    prefix = f"[{cwd}] " if cwd else ""
    print(f"{prefix}$ {printable}")
    if dry_run:
        return
    subprocess.run(command, cwd=str(cwd) if cwd else None, check=True)


def clone_repo(repo_dir: Path, repo_url: str, dry_run: bool) -> None:
    """Clone the VibeVoice repo if it is not present yet."""
    if repo_dir.exists():
        print(f"Repo already exists at: {repo_dir}")
        return
    repo_dir.parent.mkdir(parents=True, exist_ok=True)
    run_cmd(["git", "clone", repo_url, str(repo_dir)], dry_run=dry_run)


def update_repo(repo_dir: Path, dry_run: bool) -> None:
    """Pull the latest changes from origin for an existing repo."""
    if not repo_dir.exists():
        raise FileNotFoundError(f"Repo does not exist: {repo_dir}")
    run_cmd(["git", "pull", "--ff-only"], cwd=repo_dir, dry_run=dry_run)


def install_repo(repo_dir: Path, dry_run: bool) -> None:
    """Install VibeVoice in editable mode."""
    if not repo_dir.exists():
        raise FileNotFoundError(f"Repo does not exist: {repo_dir}")
    run_cmd([sys.executable, "-m", "pip", "install",
            "-e", "."], cwd=repo_dir, dry_run=dry_run)


def install_flash_attn(repo_dir: Path, dry_run: bool) -> None:
    """Install flash-attn as an optional performance dependency."""
    if not repo_dir.exists():
        raise FileNotFoundError(f"Repo does not exist: {repo_dir}")
    run_cmd(
        [sys.executable, "-m", "pip", "install",
            "flash-attn", "--no-build-isolation"],
        cwd=repo_dir,
        dry_run=dry_run,
    )


def download_model(model_id: str, local_dir: Path | None, dry_run: bool) -> None:
    """Download model weights from Hugging Face without running inference."""
    snippet = [
        "from huggingface_hub import snapshot_download",
        f"kwargs = {{'repo_id': {model_id!r}}}",
        f"local_dir = {str(local_dir)!r}",
        "if local_dir:",
        "    kwargs['local_dir'] = local_dir",
        "path = snapshot_download(**kwargs)",
        "print(path)",
    ]
    run_cmd([sys.executable, "-c", "\n".join(snippet)], dry_run=dry_run)


def run_inference(
    repo_dir: Path,
    model_path: str,
    audio_files: list[Path],
    dry_run: bool,
    extra_args: list[str],
) -> None:
    """Run official VibeVoice-ASR file inference script for given audio files."""
    script_path = repo_dir / "demo" / "vibevoice_asr_inference_from_file.py"
    if not script_path.exists():
        raise FileNotFoundError(
            f"Could not find inference script: {script_path}. Did setup complete successfully?"
        )

    missing = [audio for audio in audio_files if not audio.exists()]
    if missing:
        missing_text = "\n".join(str(item) for item in missing)
        raise FileNotFoundError(
            f"These audio files do not exist:\n{missing_text}")

    command = [
        sys.executable,
        str(script_path),
        "--model_path",
        model_path,
        "--audio_files",
        *[str(path) for path in audio_files],
        *extra_args,
    ]
    run_cmd(command, cwd=repo_dir, dry_run=dry_run)


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        description=(
            "Local VibeVoice-ASR helper. This script never runs inference unless you "
            "explicitly call the 'transcribe' command."
        )
    )
    parser.add_argument(
        "--repo-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "VibeVoice",
        help="Where to clone/use the VibeVoice repo.",
    )
    parser.add_argument(
        "--repo-url",
        default=DEFAULT_REPO_URL,
        help="Git URL for VibeVoice.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print commands without executing them.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    setup = subparsers.add_parser(
        "setup", help="Clone + install VibeVoice locally.")
    setup.add_argument("--update", action="store_true",
                       help="Run git pull if repo already exists.")
    setup.add_argument(
        "--install-flash-attn",
        action="store_true",
        help="Also try to install flash-attn (optional per official docs).",
    )

    download = subparsers.add_parser(
        "download-model",
        help="Pre-download model weights from Hugging Face without running ASR.",
    )
    download.add_argument(
        "--model-path", default=DEFAULT_MODEL_ID, help="Hugging Face model id.")
    download.add_argument(
        "--local-dir",
        type=Path,
        default=None,
        help="Optional local cache/output directory for model files.",
    )

    transcribe = subparsers.add_parser(
        "transcribe",
        help="Run file-based ASR inference (this is the command that will use GPU).",
    )
    transcribe.add_argument(
        "--model-path",
        default=DEFAULT_MODEL_ID,
        help="Model id or local model path.",
    )
    transcribe.add_argument(
        "--audio-files",
        nargs="+",
        type=Path,
        required=True,
        help="One or more audio files to transcribe.",
    )
    transcribe.add_argument(
        "--extra-args",
        nargs=argparse.REMAINDER,
        default=[],
        help="Extra args passed directly to demo/vibevoice_asr_inference_from_file.py",
    )

    return parser


def main() -> None:
    """Parse args and dispatch CLI commands."""
    parser = build_parser()
    args = parser.parse_args()

    repo_dir = args.repo_dir.resolve()

    if args.command == "setup":
        clone_repo(repo_dir=repo_dir, repo_url=args.repo_url,
                   dry_run=args.dry_run)
        if getattr(args, "update", False) and repo_dir.exists():
            update_repo(repo_dir=repo_dir, dry_run=args.dry_run)
        install_repo(repo_dir=repo_dir, dry_run=args.dry_run)
        if getattr(args, "install_flash_attn", False):
            install_flash_attn(repo_dir=repo_dir, dry_run=args.dry_run)
        print("Setup complete.")
        return

    if args.command == "download-model":
        download_model(model_id=args.model_path,
                       local_dir=args.local_dir, dry_run=args.dry_run)
        print("Model download command complete.")
        return

    if args.command == "transcribe":
        run_inference(
            repo_dir=repo_dir,
            model_path=args.model_path,
            audio_files=args.audio_files,
            dry_run=args.dry_run,
            extra_args=args.extra_args,
        )
        print("Transcription command complete.")
        return

    parser.error(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    main()
