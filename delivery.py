"""安全交付 scratch/deliver 中的普通文件。
由 ai_worker 调用 deliver_files；不单独运行。
依赖 Python 标准库及 POSIX dir_fd / O_NOFOLLOW。
"""
from __future__ import annotations

import contextlib
import hashlib
import os
from pathlib import Path
import stat
import uuid


# pal src/computers/borrow.ts PRIVATE，另加 .pal-peer。
PRIVATE = ('.ssh', '.pal', '.pal-peer', '.gnupg', 'Library/Keychains',
           '.config/op', 'Library/Application Support/1Password')
PROTECTED = (*PRIVATE, 'Library/LaunchAgents', 'Library/LaunchDaemons',
             '.config/autostart', '.local/bin', '.claude', '.codex', '.config/aiw',
             '.zshrc', '.zshenv', '.zprofile', '.bashrc', '.bash_profile', '.profile')
MAX_BYTES = 2 * 1024 ** 3
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def validate_parts(parts):
    folded = tuple(part.casefold() for part in parts)
    if any(part.startswith('.') for part in parts):
        raise ValueError('交付路径不得包含以点开头的路径段')
    for protected in PROTECTED:
        blocked = tuple(protected.casefold().split('/'))
        if any(folded[i:i + len(blocked)] == blocked for i in range(len(folded))):
            raise ValueError('交付禁止写入私密或自动执行目录')


def validate_target(raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute() or '\0' in raw:
        raise ValueError('--deliver 必须是 home 内的绝对目录')
    requested = Path(os.path.abspath(path))
    home = Path.home()
    root = home.resolve()
    real = requested.resolve()
    if (real == root or not real.is_relative_to(root)
            or not (requested.is_relative_to(home) or requested.is_relative_to(root))):
        raise ValueError('--deliver 目标必须在用户 home 下')
    for candidate, base in ((requested, home), (requested, root), (real, root)):
        if candidate.is_relative_to(base):
            validate_parts(candidate.relative_to(base).parts)
    # 不允许借助路径中的软链写入；也拒绝不存在目录后的 ..。
    if '..' in path.parts:
        raise ValueError('--deliver 不接受 .. 路径')
    return requested


@contextlib.contextmanager
def directory(path: Path, create=False, journal=None):
    """从根目录逐段打开，避免校验后软链替换重定向文件写入。"""
    fd = os.open(path.anchor, DIR_FLAGS)
    try:
        for name in path.parts[1:]:
            if create:
                try:
                    os.mkdir(name, mode=0o700, dir_fd=fd)
                    remember(journal, fd, name, True)
                except FileExistsError:
                    pass
            child = os.open(name, DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


@contextlib.contextmanager
def subdirectory(root_fd, parts, create=False, journal=None):
    fd = os.dup(root_fd)
    try:
        for name in parts:
            if create:
                try:
                    os.mkdir(name, mode=0o700, dir_fd=fd)
                    remember(journal, fd, name, True)
                except FileExistsError:
                    pass
            child = os.open(name, DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def regular_file(fd, name):
    result = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    info = os.fstat(result)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        os.close(result)
        raise ValueError(f'交付只接受无硬链接的普通文件：{name}')
    return os.fdopen(result, 'rb')


def digest(stream, size):
    stream.seek(0)
    return copy_digest(stream, None, size)


def inventory(fd, prefix=(), budget=None):
    budget = [0] if budget is None else budget
    result = []
    for name in sorted(os.listdir(fd)):
        validate_parts((*prefix, name))
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            with subdirectory(fd, [name]) as child:
                result.extend(inventory(child, (*prefix, name), budget))
        else:
            with regular_file(fd, name) as stream:
                size = os.fstat(stream.fileno()).st_size
                budget[0] += size
                if budget[0] > MAX_BYTES:
                    raise ValueError('交付总大小超过 2 GiB')
                result.append(((*prefix, name), size))
    return result


def remember(journal, parent, name, is_dir, info=None):
    if journal is not None:
        info = info or os.stat(name, dir_fd=parent, follow_symlinks=False)
        journal.append((os.dup(parent), name, is_dir, info.st_dev, info.st_ino))


def copy_digest(stream, out, limit):
    hasher = hashlib.sha256()
    size = 0
    while block := stream.read(1024 * 1024):
        size += len(block)
        if size > limit:
            raise ValueError('源文件复制期间增大或交付总大小超过 2 GiB')
        hasher.update(block)
        if out is not None:
            out.write(block)
    if size != limit:
        raise ValueError('源文件复制期间大小变化')
    return hasher.hexdigest()


def deliver_files(scratch: Path, target: str) -> dict:
    result = {'target': target, 'delivered': [], 'error': None}
    journal = []
    try:
        requested = validate_target(target)
        # Resolve only the trusted home prefix (e.g. macOS /var -> /private/var).
        home = Path.home()
        base = home if requested.is_relative_to(home) else home.resolve()
        destination = home.resolve() / requested.relative_to(base)
        with directory(scratch.parent.resolve()) as run_fd:
            with subdirectory(run_fd, [scratch.name, 'deliver']) as source:
                files = inventory(source)
                if not files:
                    raise ValueError('scratch/deliver 没有可交付文件')
                for parts, _ in files:
                    validate_target(str(requested.joinpath(*parts)))
                # 完整预检在创建任何目标目录前完成。源大小已统一限额。
                for parts, size in files:
                    with subdirectory(source, parts[:-1]) as src:
                        try:
                            with directory(destination.joinpath(*parts[:-1])) as parent:
                                with regular_file(parent, parts[-1]) as stream:
                                    with regular_file(src, parts[-1]) as original:
                                        if (os.fstat(stream.fileno()).st_size != size
                                                or digest(stream, size) != digest(original, size)):
                                            raise ValueError(f'目标已存在且内容不同：{Path(*parts)}')
                        except FileNotFoundError:
                            pass
                with directory(destination, create=True, journal=journal) as dest:
                    for parts, size in files:
                        with subdirectory(source, parts[:-1]) as src, subdirectory(dest, parts[:-1], True, journal) as dst:
                            temporary = '.aiw-deliver-' + uuid.uuid4().hex
                            try:
                                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                                with os.fdopen(os.open(temporary, flags, 0o600, dir_fd=dst), 'wb') as out:
                                    with regular_file(src, parts[-1]) as stream:
                                        expected = copy_digest(stream, out, size)
                                    out.flush()
                                    os.fsync(out.fileno())
                                    copied = os.fstat(out.fileno())
                                try:
                                    os.link(temporary, parts[-1], src_dir_fd=dst, dst_dir_fd=dst, follow_symlinks=False)
                                except FileExistsError:
                                    # 并发创建也必须核对，不覆盖。
                                    with regular_file(dst, parts[-1]) as stream:
                                        if os.fstat(stream.fileno()).st_size != size or digest(stream, size) != expected:
                                            raise ValueError(f'目标已存在且内容不同：{Path(*parts)}')
                                else:
                                    remember(journal, dst, parts[-1], False, copied)
                                    published = os.stat(parts[-1], dir_fd=dst, follow_symlinks=False)
                                    if (published.st_dev, published.st_ino) != (copied.st_dev, copied.st_ino):
                                        raise ValueError(f'目标发布后被替换：{Path(*parts)}')
                                os.unlink(temporary, dir_fd=dst)
                                result['delivered'].append({'path': str(Path(*parts)), 'sha256': expected})
                            finally:
                                try:
                                    os.unlink(temporary, dir_fd=dst)
                                except FileNotFoundError:
                                    pass
    except (OSError, ValueError, RuntimeError) as exc:
        result['error'] = str(exc)
        for parent, name, is_dir, device, inode in reversed(journal):
            try:
                current = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if (current.st_dev, current.st_ino) != (device, inode):
                    raise ValueError(f'回滚目标已被替换：{name}')
                (os.rmdir if is_dir else os.unlink)(name, dir_fd=parent)
            except FileNotFoundError:
                pass
            except (OSError, ValueError) as rollback_error:
                result['error'] += f'; 回滚失败：{rollback_error}'
        result['delivered'] = []
    finally:
        for parent, *_ in journal:
            os.close(parent)
    return result
