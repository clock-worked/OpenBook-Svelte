import os
import sys
from huggingface_hub import hf_hub_download

# --- Configuration ---
ADAPTER_REPO = "ZefanCai/Open-Jev-9B"
ADAPTER_REVISION = "47e966881e489511c0c7f5633a9e1960a676a551"
BASE_REPO = "Qwen/Qwen3.5-9B"
BASE_REVISION = "c202236235762e1c871ad0ccb60c8ee5ba337b9a"

TARGET_DIR = r"C:\Users\Chad\openjev_models"
ADAPTER_LOCAL_DIR = os.path.join(TARGET_DIR, "Open-Jev-9B")
BASE_LOCAL_DIR = os.path.join(TARGET_DIR, "Qwen3.5-9B")
CACHE_DIR = os.path.join(TARGET_DIR, "hf_cache")

# --- Main Download Logic ---
def download_model_part(repo_id, revision, local_dir, part_name):
    """Downloads a single file from a repo to act as a full download."""
    print(f"--- Starting download for {part_name} ---")
    print(f"Repo: {repo_id}@{revision}")
    print(f"Target directory: {local_dir}")

    # We need to download all files, not just one. hf_hub_download is for single files.
    # Let's use snapshot_download instead.
    from huggingface_hub import snapshot_download

    try:
        snapshot_download(
            repo_id=repo_id,
            revision=revision,
            local_dir=local_dir,
            local_dir_use_symlinks=False,
            cache_dir=CACHE_DIR,
            # To show progress, we can't use quiet mode.
            # The hf_transfer library is recommended for speed.
            # Set env var for it.
        )
        print(f"--- Successfully completed download for {part_name} ---")
        return True
    except Exception as e:
        print(f"!!! ERROR downloading {part_name}: {e}", file=sys.stderr)
        return False

def main():
    print(f"Target directory: {TARGET_DIR}")
    os.makedirs(TARGET_DIR, exist_ok=True)
    os.environ["HF_HOME"] = CACHE_DIR
    os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "1" # Enable faster downloads

    print("\nDownloading Adapter...")
    adapter_ok = download_model_part(ADAPTER_REPO, ADAPTER_REVISION, ADAPTER_LOCAL_DIR, "Adapter")

    if not adapter_ok:
        sys.exit(1)

    print("\nDownloading Base Model...")
    base_ok = download_model_part(BASE_REPO, BASE_REVISION, BASE_LOCAL_DIR, "Base Model")

    if not base_ok:
        sys.exit(1)

    print("\nCloning loader repository...")
    import subprocess
    LOADER_REPO_URL = "https://github.com/Zefan-Cai/Open-Jev"
    LOADER_LOCAL_DIR = os.path.join(TARGET_DIR, "Open-Jev")
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", LOADER_REPO_URL, LOADER_LOCAL_DIR],
            check=True,
            capture_output=True,
            text=True
        )
        print("--- Successfully cloned loader repo ---")
    except subprocess.CalledProcessError as e:
        print(f"!!! ERROR cloning loader repo: {e}", file=sys.stderr)
        print(f"Stderr: {e.stderr}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
