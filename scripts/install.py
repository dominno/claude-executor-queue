#!/usr/bin/env python3
"""Shared offline installer for the POSIX and PowerShell entry points."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'skill/executor-queue'
PAYLOAD = ('SKILL.md', 'scripts/xq.py')


def hashes(folder):
    """Include extra files and symlinks so neither is silently overwritten."""
    result = {}
    if folder.exists():
        for path in sorted(folder.rglob('*')):
            name = path.relative_to(folder).as_posix()
            if path.is_symlink():
                result[name] = 'symlink:' + os.readlink(str(path))
            elif path.is_file():
                result[name] = hashlib.sha256(path.read_bytes()).hexdigest()
            elif path.is_dir():
                result[name + '/'] = 'directory'
            else:
                result[name] = 'special'
    return result


def installed_version(folder):
    script = folder / 'scripts/xq.py'
    # Never execute the installed copy just to report its version.
    if (folder / 'scripts').is_symlink() or not script.is_file() or script.is_symlink():
        return 'unknown'
    match = re.search(r"^VERSION = ['\"]([^'\"]+)['\"]", script.read_text(encoding='utf-8', errors='replace'), re.M)
    return match.group(1) if match else 'unknown (legacy copy)'


def main():
    if sys.version_info < (3, 8):
        raise SystemExit('install: Python 3.8+ is required')
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true', help='compare installed hashes with repo; exit 1 on drift/missing')
    mode.add_argument('--uninstall', action='store_true', help='remove installed skill')
    parser.add_argument('--force', action='store_true', help='discard installed edits on install/uninstall')
    args = parser.parse_args()
    skills = Path(os.environ.get('CLAUDE_SKILLS_DIR') or str(Path.home() / '.claude/skills')).expanduser().absolute()
    target = skills / 'executor-queue'
    receipt = skills / '.executor-queue-install.json'
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        raise SystemExit('install: destination must be a real directory: ' + str(target))
    if receipt.is_symlink():
        raise SystemExit('install: receipt must not be a symlink: ' + str(receipt))
    expected = {'scripts/': 'directory'}
    for name in PAYLOAD:
        expected[name] = hashlib.sha256((SOURCE / name).read_bytes()).hexdigest()
    current = hashes(target)
    version = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
    print('repo version: ' + version)
    print('installed version: ' + (installed_version(target) if target.exists() else 'not installed'))
    print('destination: ' + str(target))
    if args.check:
        if target.exists() and current == expected:
            print('OK: SHA-256 hashes match the repo')
            return 0
        print('MISMATCH: installed copy differs from the repo' if target.exists() else 'MISSING: skill is not installed')
        for name in sorted(set(current) | set(expected)):
            if current.get(name) != expected.get(name):
                print('  ' + name)
        return 1
    baseline = None
    if receipt.exists():
        try:
            data = json.loads(receipt.read_text(encoding='utf-8'))
            if isinstance(data, dict) and data.get('schema') == 1 and isinstance(data.get('hashes'), dict):
                baseline = data['hashes']
        except (ValueError, OSError):
            pass
    # Unmanaged/legacy copies are safe only when identical to the repo.
    clean = current == (baseline if baseline is not None else expected)
    if target.exists() and not clean and not args.force:
        raise SystemExit('install: local edits or unmanaged copy detected; back up first, then use --force to discard')
    if args.uninstall:
        existed = target.exists()
        if existed:
            shutil.rmtree(str(target))
        if receipt.exists():
            receipt.unlink()
        print('uninstalled executor-queue' if existed else 'executor-queue is not installed')
        return 0
    skills.mkdir(parents=True, exist_ok=True)
    # Stage the complete payload before removing a clean/explicitly forced copy.
    with tempfile.TemporaryDirectory(prefix='.executor-queue-', dir=str(skills)) as stage:
        staged = Path(stage) / 'executor-queue'
        (staged / 'scripts').mkdir(parents=True)
        for name in PAYLOAD:
            shutil.copy2(str(SOURCE / name), str(staged / name))
        (staged / 'scripts/xq.py').chmod(0o755)
        staged_receipt = Path(stage) / 'receipt.json'
        staged_receipt.write_text(json.dumps({'schema': 1, 'version': version, 'hashes': expected}, indent=2) + '\n', encoding='utf-8')
        if target.exists():
            shutil.rmtree(str(target))
        staged.rename(target)
        os.replace(str(staged_receipt), str(receipt))
    print('installed executor-queue')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except OSError as exc:
        raise SystemExit('install: ' + str(exc))
