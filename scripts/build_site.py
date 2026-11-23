"""Generate both the Vercel entry point and the existing local preview file."""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
subprocess.run([sys.executable,"-m","src.select_review_claims"],cwd=ROOT,check=True)
subprocess.run([sys.executable,"-m","src.report","--input","data/research.json","--verification","data/verification.json","--output","index.html"],cwd=ROOT,check=True)
shutil.copy2(ROOT/"index.html",ROOT/"report.html")
subprocess.run([sys.executable,"scripts/build_human_review.py"],cwd=ROOT,check=True)
print("Public report and private local review inbox built.")
