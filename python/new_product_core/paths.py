import sys
import os

def get_binary_path(binary_name: str) -> str:
    """
    Resolves the cross-platform path for a Rust binary.
    Assuming the binaries are built and placed in the target/release directory
    or alongside the package installation.
    """
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    target_dir = os.path.join(base_dir, "target", "release")

    if sys.platform == "win32":
        return os.path.join(target_dir, f"{binary_name}.exe")
    return os.path.join(target_dir, binary_name)
