"""Build the public report bundle without including the private review inbox."""
import shutil
import subprocess
import os
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"


def main() -> None:
    npm = "npm.cmd" if os.name == "nt" else "npm"
    subprocess.run([npm, "run", "build"], cwd=ROOT, check=True)

    # Recreate only the generated deployment directory to avoid stale public files.
    if DIST.resolve().parent != ROOT.resolve() or DIST.name != "dist":
        raise RuntimeError("Refusing to clear an unexpected deployment output path")
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True)

    for page in ("index.html", "report.html"):
        shutil.copy2(ROOT / page, DIST / page)

    logo_source = ROOT / "assets" / "logos"
    if logo_source.exists():
        shutil.copytree(logo_source, DIST / "assets" / "logos")

    output = ROOT / ".vercel" / "output"
    if output.resolve().parent != (ROOT / ".vercel").resolve() or output.name != "output":
        raise RuntimeError("Refusing to clear an unexpected Vercel output path")
    if output.exists():
        shutil.rmtree(output)
    static = output / "static"
    static.mkdir(parents=True)
    for item in DIST.iterdir():
        target = static / item.name
        if item.is_dir():
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)
    (output / "config.json").write_text(
        json.dumps({"version": 3}, indent=2) + "\n", encoding="utf-8"
    )

    print("Prepared dist/ and .vercel/output/ with public reports and logos only.")


if __name__ == "__main__":
    main()
