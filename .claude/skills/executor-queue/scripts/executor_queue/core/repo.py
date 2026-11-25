"""Repository queries: commits, branches, briefs and worktrees, through the PORT runner (CORE)."""

import os
from pathlib import Path
import shlex
import time

from . import seams
from .config import config
from .state import valid_branch
from .util import display_name, fail


def resolve_commit(root, ref):
    if ref.startswith('-'):
        fail('invalid commit "{}"'.format(display_name(ref[:120])))
    return seams.git('rev-parse', '--verify', ref + '^{commit}', cwd=root).strip()


def commit_known(root, sha):
    return not seams.run(['git', 'cat-file', '-e', sha + '^{commit}'], cwd=root).returncode


def ancestor(before, after, root):
    return seams.run(['git', 'merge-base', '--is-ancestor', before, after], cwd=root).returncode == 0


def origin_default(root):
    ref = seams.git('symbolic-ref', '--quiet', '--short', 'refs/remotes/origin/HEAD', check=False, cwd=root).strip()
    return ref[len('origin/'):] if ref.startswith('origin/') else None


def default_branch(root):
    ref = origin_default(root)
    if ref:
        return ref
    ref = seams.git('symbolic-ref', '--quiet', '--short', 'HEAD', check=False, cwd=root).strip()
    if not ref:
        fail('cannot determine default branch; set origin/HEAD or pass --main')
    return ref


def require_main(root, explicit=None):
    branch = explicit or config(root).get('main') or origin_default(root)
    if not branch:
        local = seams.git('symbolic-ref', '--quiet', '--short', 'HEAD', check=False, cwd=root).strip()
        if local:
            fail('origin/HEAD unset; detected local branch "{0}" — rerun with --main {0} or set '
                 '{{"main":"{0}"}} in .agents/config.json'.format(local))
        fail('origin/HEAD unset; pass --main <branch> or set "main" in .agents/config.json')
    if not isinstance(branch, str):
        fail('config main must be a branch string')
    valid_branch(branch, root)
    return branch


def known_main(root, explicit=None):
    """The integration branch when it is known (--main, config main or origin/HEAD) and exists locally."""
    main = explicit or config(root).get('main') or origin_default(root)
    if not main or seams.run(['git', 'check-ref-format', '--branch', main], cwd=root).returncode:
        return None
    if seams.run(['git', 'rev-parse', '--verify', '--quiet', 'refs/heads/' + main], cwd=root).returncode:
        return None
    return main


def base_in_main(root, base):
    """True when every commit up to base is already on the known main branch (nothing unmerged to inherit)."""
    main = known_main(root)
    return bool(main) and ancestor(base, 'refs/heads/' + main, root)


def fetch_branch(branch, root):
    valid_branch(branch, root)
    # FETCH_HEAD is shared scratch state: concurrent unlocked fetches can
    # overwrite it between fetch and rev-parse. Resolve a unique private ref.
    ref = 'refs/xq-fetch/{}-{}'.format(os.getpid(), time.monotonic_ns())
    source = branch if branch.startswith('refs/heads/') else 'refs/heads/' + branch
    try:
        seams.git('fetch', '-q', '--no-tags', 'origin', '--', source + ':' + ref, cwd=root)
        return seams.git('rev-parse', '--verify', ref + '^{commit}', cwd=root).strip()
    finally:
        seams.git('update-ref', '-d', ref, check=False, cwd=root)


def brief_path(root, value):
    path = root / value
    if any(p.is_symlink() for p in (path, *path.parents)):
        fail('brief not found or unsafe (symlink): ' + value)
    path = path.resolve()
    try:
        rel = path.relative_to(root)
    except ValueError:
        fail('brief must be inside repository')
    if not path.is_file():
        fail('brief not found or unsafe: ' + value)
    return path, rel.as_posix()


def brief_relative(root, brief):
    try:
        return Path(brief).resolve().relative_to(Path(root).resolve()).as_posix()
    except (TypeError, ValueError, OSError):
        return None


def task_brief(root, task):
    brief = task.get('brief')
    return brief if isinstance(brief, str) and not os.path.isabs(brief) else brief_relative(root, brief)


def same_worktree(left, right):
    if not left or not right:
        return left == right
    return os.path.normcase(str(Path(left).resolve())) == os.path.normcase(str(Path(right).resolve()))


def checked_worktree(root, a):
    """Resolved --worktree, with refusals that echo the value and name the fix."""
    value = a.worktree
    shown = display_name(value)[:200]
    path = Path(value)
    if not path.is_absolute():
        fail('--worktree "{}" is relative; rerun with --worktree {}'.format(shown, shlex.quote(str(path.resolve()))))
    if not path.is_dir():
        fail('--worktree "{}" does not exist; create it with git -C {} worktree add -b xq/{} {} HEAD'.format(
            shown, shlex.quote(str(root)), a.id, shlex.quote(value)))
    return str(path.resolve())


def commit_parents(root, sha):
    return seams.git('rev-list', '--parents', '-n', '1', sha, cwd=root).split()[1:]


def commit_subjects(root, *revisions, limit=200):
    """(sha, sanitized subject) for rev-list revisions, newest first, bounded."""
    output = seams.git('log', '--format=%H %s', '--max-count={}'.format(limit), *revisions, '--', cwd=root)
    rows = []
    for line in output.splitlines():
        commit, _, subject = line.partition(' ')
        rows.append((commit, display_name(subject)[:80]))
    return rows


def integrating_commit(root, sha, head):
    """The oldest commit on sha's first-parent chain that contains head (the merge or fast-forward that
    brought the delivery in), or None when sha is head or does not contain it."""
    chain = seams.git('rev-list', '--first-parent', head + '..' + sha, cwd=root).split()
    if not chain:
        return None
    descendants = set(seams.git('rev-list', '--ancestry-path', head + '..' + sha, cwd=root).split())
    found = None
    for commit in chain:
        if commit not in descendants:
            break
        found = commit
    return found


def merged_extra_commits(root, sha, head):
    """[(delivered parent, commits)] for commits the integrating merge brought in beyond head.

    The integrating merge is found on sha's first-parent chain, so later unrelated merges on top do not
    count. Only its non-first parents that contain head are the delivered side; commits from the first
    parent (the target branch before the merge) and from unrelated parents are excluded. Anything else on
    the delivered side that the verified head does not contain was never reviewed.
    """
    merge = integrating_commit(root, sha, head)
    if not merge:
        return []
    parents = commit_parents(root, merge)
    if len(parents) < 2:
        return []
    extra = []
    for parent in parents[1:]:
        if not ancestor(head, parent, root):
            continue
        commits = commit_subjects(root, parent, '^' + head, '^' + parents[0])
        if commits:
            extra.append((parent, commits))
    return extra


def history_paths(root, base, head, *pathspec):
    # Separate merge-parent diffs and both sides of renames expose paths that
    # changed in any outgoing commit, even when the tip restores the old tree.
    output = seams.git('log', '--format=', '--raw', '--no-renames', '-m', '--full-history', '-z',
                 base + '..' + head, '--', *pathspec, cwd=root).split('\0')
    # special maps symlinks and gitlinks (submodule pointers) to their WARN reason.
    paths, special = set(), {}
    for metadata, path in zip(output[::2], output[1::2]):
        old_mode, new_mode, *_ = metadata.lstrip('\n:').split()
        paths.add(path)
        if '160000' in (old_mode, new_mode):
            special[path] = 'submodule pointer'
        elif '120000' in (old_mode, new_mode):
            special.setdefault(path, 'symlink')
    return paths, special
