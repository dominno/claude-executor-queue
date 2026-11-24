#!/usr/bin/env python3
"""Shared offline installer for the POSIX and PowerShell entry points."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'skills/executor-queue'
PAYLOAD = ('LICENSE', 'SKILL.md', 'scripts/xq.py')


def present(path):
    return path.is_symlink() or path.exists()


@contextmanager
def install_lock(path, *, writer):
    flags = os.O_RDWR | os.O_CREAT if writer else os.O_RDONLY
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise OSError('install lock must be a regular file: ' + str(path))
    fd = os.open(str(path), flags | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError('install lock must be a regular file: ' + str(path))
        if os.name == 'nt':
            import msvcrt
            mode = msvcrt.LK_LOCK if writer else msvcrt.LK_RLCK
            msvcrt.locking(fd, mode, 1)
            try:
                yield
            finally:
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX if writer else fcntl.LOCK_SH)
            yield
    finally:
        os.close(fd)


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hashes(folder):
    """Include extra files and symlinks so neither is silently overwritten."""
    result = {}
    if folder.exists():
        for path in sorted(folder.rglob('*')):
            name = path.relative_to(folder).as_posix()
            if path.is_symlink():
                result[name] = 'symlink:' + os.readlink(str(path))
            elif path.is_file():
                result[name] = file_hash(path)
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


def inspect_install(target, receipt):
    if target.is_symlink() or (present(target) and not target.is_dir()):
        raise SystemExit('install: destination must be a real directory: ' + str(target))
    if receipt.is_symlink() or (present(receipt) and not receipt.is_file()):
        raise SystemExit('install: receipt must be a regular file: ' + str(receipt))
    exists = target.is_dir()
    return exists, hashes(target), installed_version(target) if exists else 'not installed'


def print_install(target, version, current_version):
    print('repo version: ' + version)
    print('installed version: ' + current_version)
    print('destination: ' + str(target))


def legacy_backup(directory):
    for path in directory.glob('.executor-queue-????????'):
        if path.is_dir() and not path.is_symlink() and present(path / 'previous'):
            return path / 'previous'
    return None


def check_install(target, receipt, stage, lock, expected, version):
    def snapshot():
        previous = legacy_backup(target.parent)
        finished = stage / 'complete'
        if previous is None and present(stage) and (stage.is_symlink() or
                                                    finished.is_symlink() or not finished.is_file()):
            previous = stage
        return inspect_install(target, receipt), previous

    # Before the first writer there is nothing to lock. If one starts while
    # taking that first read-only snapshot, repeat the read under its lock.
    if not present(lock):
        try:
            state = snapshot()
        except OSError:
            if not present(lock):
                raise
    if present(lock):
        with install_lock(lock, writer=False):
            state = snapshot()

    (exists, current, current_version), previous = state
    print_install(target, version, current_version)
    if previous is not None:
        print('PENDING: interrupted installation data at ' + str(previous))
        return 1
    if exists and current == expected:
        print('OK: SHA-256 hashes match the repo')
        return 0
    print('MISMATCH: installed copy differs from the repo' if exists else 'MISSING: skill is not installed')
    for name in sorted(set(current) | set(expected)):
        if current.get(name) != expected.get(name):
            print('  ' + name)
    return 1


def clean_stage(stage):
    # Keep the marker until its contents are gone so interrupted cleanup can resume.
    for name in ('executor-queue', 'receipt.json', 'previous', 'previous-receipt.json', 'journal.tmp'):
        path = stage / name
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(str(path))
        elif present(path):
            path.unlink()
    marker = stage / 'complete'
    if marker.exists():
        marker.unlink()
    stage.rmdir()


def clean_finished_stage(stage):
    try:
        clean_stage(stage)
    except OSError as exc:
        # The transaction is settled. Move any remaining cleanup data aside
        # without changing permissions or removing anything from the live tree.
        retained = Path(tempfile.mkdtemp(prefix='.executor-queue-cleanup-', dir=str(stage.parent)))
        try:
            os.replace(str(stage), str(retained / 'stage'))
        except OSError as move_error:
            retained.rmdir()
            raise OSError('operation completed; clear the finished stage at {} once it is unlocked ({})'.format(
                stage, move_error)) from exc
        print('install: operation completed; cleanup data retained at {} ({})'.format(
            retained / 'stage', exc), file=sys.stderr)


def restore_path(live, staged, previous, had_previous, expected, fingerprint):
    if present(previous) or not had_previous:
        if present(live):
            if (expected is None or present(staged) or live.is_symlink()
                    or fingerprint(live) != expected):
                raise OSError('destination changed during the interrupted operation: ' + str(live))
            os.replace(str(live), str(staged))
        if present(previous):
            os.replace(str(previous), str(live))
    elif not present(live):
        raise OSError('previous installation is missing: ' + str(live))


def recover_stage(stage, target, receipt):
    if not present(stage):
        return
    journal = stage / 'journal.json'
    marker = stage / 'complete'
    try:
        if stage.is_symlink() or not stage.is_dir():
            raise OSError('transaction stage must be a real directory')
        if present(journal):
            if journal.is_symlink() or not journal.is_file() or present(marker):
                raise OSError('invalid transaction journal')
            data = json.loads(journal.read_text(encoding='utf-8'))
            if (not isinstance(data, dict) or set(data) != {
                    'had_target', 'had_receipt', 'new_hashes', 'new_receipt_hash'}
                    or type(data['had_target']) is not bool or type(data['had_receipt']) is not bool
                    or not isinstance(data['new_hashes'], (dict, type(None)))
                    or not isinstance(data['new_receipt_hash'], (str, type(None)))):
                raise OSError('invalid transaction journal')
            restore_path(target, stage / 'executor-queue', stage / 'previous',
                         data['had_target'], data['new_hashes'], hashes)
            restore_path(receipt, stage / 'receipt.json', stage / 'previous-receipt.json',
                         data['had_receipt'], data['new_receipt_hash'], file_hash)
            os.replace(str(journal), str(marker))
        elif marker.is_symlink() or (present(marker) and not marker.is_file()):
            raise OSError('invalid transaction marker')
        elif not marker.exists() and (present(stage / 'previous') or present(stage / 'previous-receipt.json')):
            raise OSError('backups exist without a transaction journal')
        if marker.exists():
            clean_finished_stage(stage)
        else:
            clean_stage(stage)
    except (OSError, ValueError) as exc:
        raise OSError('could not recover interrupted operation; inspect {} ({})'.format(stage, exc)) from exc


def publish(stage, target, receipt, expected, version, *, uninstall):
    stage.mkdir(mode=0o700)
    staged = stage / 'executor-queue'
    staged_receipt = stage / 'receipt.json'
    journal = stage / 'journal.json'
    complete = stage / 'complete'
    had_target = target.exists()
    had_receipt = receipt.exists()
    try:
        if not uninstall:
            (staged / 'scripts').mkdir(parents=True)
            for name in PAYLOAD:
                shutil.copy2(str(SOURCE / name), str(staged / name))
            (staged / 'scripts/xq.py').chmod(0o755)
            if hashes(staged) != expected:
                raise OSError('source changed during installation; retry from a stable checkout')
            data = {'schema': 1, 'version': version, 'hashes': expected}
            staged_receipt.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
        data = {'had_target': had_target, 'had_receipt': had_receipt,
                'new_hashes': None if uninstall else expected,
                'new_receipt_hash': None if uninstall else file_hash(staged_receipt)}
        scratch = stage / 'journal.tmp'
        scratch.write_text(json.dumps(data) + '\n', encoding='utf-8')
        os.replace(str(scratch), str(journal))
        if had_target:
            os.replace(str(target), str(stage / 'previous'))
        if had_receipt:
            os.replace(str(receipt), str(stage / 'previous-receipt.json'))
        if not uninstall:
            os.replace(str(staged), str(target))
            os.replace(str(staged_receipt), str(receipt))
        os.replace(str(journal), str(complete))
    except (OSError, KeyboardInterrupt):
        recover_stage(stage, target, receipt)
        raise

    try:
        clean_finished_stage(stage)
    except OSError as exc:
        print('install: operation completed; cleanup pending at {} ({})'.format(stage, exc), file=sys.stderr)


def uninstall_message(existed):
    print('Check project hooks: remove obsolete executor-queue hooks from project settings.')
    print('uninstalled executor-queue' if existed else 'executor-queue is not installed')


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    if sys.version_info < (3, 8):
        raise SystemExit('install: Python 3.8+ is required')
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true', help='compare installed hashes with repo; exit 1 on drift/missing')
    mode.add_argument('--uninstall', action='store_true', help='remove installed skill')
    parser.add_argument('--force', action='store_true', help='discard installed edits on install/uninstall')
    args = parser.parse_args()
    destination = os.environ.get('CLAUDE_SKILLS_DIR')
    if not destination:
        config = Path(os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude')
        destination = config / 'skills'
    skills = Path(destination).expanduser().absolute()
    target = skills / 'executor-queue'
    receipt = skills / '.executor-queue-install.json'
    lock = skills / '.executor-queue-install.lock'
    stage = skills / '.executor-queue-install-stage'
    expected = {'scripts/': 'directory'}
    for name in PAYLOAD:
        expected[name] = file_hash(SOURCE / name)
    version = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
    if args.check:
        return check_install(target, receipt, stage, lock, expected, version)
    if args.uninstall and not skills.exists():
        print_install(target, version, 'not installed')
        uninstall_message(False)
        return 0
    skills.mkdir(parents=True, exist_ok=True)
    with install_lock(lock, writer=True):
        previous = legacy_backup(skills)
        if previous is not None:
            raise OSError('a previous installation backup needs manual recovery: ' + str(previous))
        recover_stage(stage, target, receipt)
        exists, current, current_version = inspect_install(target, receipt)
        print_install(target, version, current_version)
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
        if exists and not clean and not args.force:
            raise SystemExit('install: local edits or unmanaged copy detected; back up first, then use --force to discard')
        if args.uninstall:
            if exists or receipt.exists():
                publish(stage, target, receipt, expected, version, uninstall=True)
            uninstall_message(exists)
        else:
            publish(stage, target, receipt, expected, version, uninstall=False)
            print('installed executor-queue')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except OSError as exc:
        raise SystemExit('install: ' + str(exc))
