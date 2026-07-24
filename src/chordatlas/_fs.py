from __future__ import annotations

import os
import secrets
import stat
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from weakref import finalize

CleanupReporter = Callable[[BaseException, OSError], None]
Prepublish = Callable[[int, str], None]
LeafGuard = Callable[[str, os.stat_result | None], None]
PublishObserver = Callable[[str], None]


class TargetOccupiedError(Exception):
    pass


class PublishedCleanupError(OSError):
    pass


class ProjectAnchor:
    """Pin one private project directory and resolve descendants with openat."""

    def __init__(self, root: Path) -> None:
        self.root = root
        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        self._descriptor = os.open(root, flags)
        info = os.fstat(self._descriptor)
        if not stat.S_ISDIR(info.st_mode):
            os.close(self._descriptor)
            raise NotADirectoryError(root)
        self._identity = (info.st_dev, info.st_ino, info.st_uid)
        self._finalizer = finalize(self, os.close, self._descriptor)

    def close(self) -> None:
        self._finalizer()

    def verify(self) -> None:
        try:
            named = self.root.lstat()
        except OSError:
            raise OSError("project storage root changed") from None
        current = os.fstat(self._descriptor)
        if (
            not stat.S_ISDIR(named.st_mode)
            or (named.st_dev, named.st_ino, named.st_uid) != self._identity
            or (current.st_dev, current.st_ino, current.st_uid) != self._identity
            or stat.S_IMODE(named.st_mode) != 0o700
            or stat.S_IMODE(current.st_mode) != 0o700
        ):
            raise OSError("project storage root changed")

    @contextmanager
    def directory(
        self,
        relative: Path | str = Path(),
        *,
        create: bool = False,
        mode: int = 0o700,
    ) -> Iterator[int]:
        self.verify()
        parts = _relative_parts(relative)
        descriptor = os.dup(self._descriptor)
        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            for part in parts:
                if create:
                    try:
                        os.mkdir(part, mode, dir_fd=descriptor)
                    except FileExistsError:
                        pass
                child = os.open(part, flags, dir_fd=descriptor)
                try:
                    info = os.fstat(child)
                except BaseException:
                    os.close(child)
                    raise
                if (
                    not stat.S_ISDIR(info.st_mode)
                    or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != mode
                ):
                    os.close(child)
                    raise OSError("project storage directory failed an integrity check")
                os.close(descriptor)
                descriptor = child
            yield descriptor
        finally:
            os.close(descriptor)

    @contextmanager
    def parent(
        self,
        path: Path,
        *,
        create: bool = False,
        mode: int = 0o700,
    ) -> Iterator[tuple[int, str]]:
        relative = self.relative(path)
        if not relative.name:
            raise IsADirectoryError(path)
        with self.directory(relative.parent, create=create, mode=mode) as descriptor:
            yield descriptor, relative.name

    def relative(self, path: Path) -> Path:
        try:
            relative = path.relative_to(self.root)
        except ValueError:
            raise OSError("path escapes project storage root") from None
        _relative_parts(relative)
        return relative


def create_text_exclusive(
    path: Path,
    content: str,
    *,
    stage_prefix: str,
    mode: int | None = None,
    cleanup_reporter: CleanupReporter | None = None,
    sync_directory: bool = False,
    anchor: ProjectAnchor | None = None,
) -> None:
    leaf = _target_leaf(path)
    if anchor is None:
        parent_fd = os.open(path.parent, _parent_open_flags(sync_directory))
        _create_text_exclusive_at(
            parent_fd,
            leaf,
            content,
            stage_prefix=stage_prefix,
            mode=mode,
            cleanup_reporter=cleanup_reporter,
            sync_directory=sync_directory,
            close_parent=True,
        )
        return
    with anchor.parent(path) as (parent_fd, anchored_leaf):
        if anchored_leaf != leaf:
            raise OSError("anchored target changed")
        _create_text_exclusive_at(
            parent_fd,
            leaf,
            content,
            stage_prefix=stage_prefix,
            mode=mode,
            cleanup_reporter=cleanup_reporter,
            sync_directory=sync_directory,
            close_parent=False,
        )


def _create_text_exclusive_at(
    parent_fd: int,
    leaf: str,
    content: str,
    *,
    stage_prefix: str,
    mode: int | None,
    cleanup_reporter: CleanupReporter | None,
    sync_directory: bool,
    close_parent: bool,
) -> None:
    published = False
    active_error: BaseException | None = None
    try:
        try:
            os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise TargetOccupiedError()

        staged = _stage_text(
            parent_fd,
            leaf,
            content,
            stage_prefix=stage_prefix,
            target_mode=mode,
            cleanup_reporter=cleanup_reporter,
        )
        try:
            os.link(
                staged,
                leaf,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
                follow_symlinks=False,
            )
        except FileExistsError as error:
            occupied = TargetOccupiedError()
            _unlink_stage(parent_fd, staged, occupied, cleanup_reporter)
            raise occupied from error
        except BaseException as error:
            _unlink_stage(parent_fd, staged, error, cleanup_reporter)
            raise
        published = True
        try:
            os.unlink(staged, dir_fd=parent_fd)
        except OSError as error:
            raise PublishedCleanupError(f"temporary file {staged}: {error}") from error
    except BaseException as error:
        active_error = error
        raise
    finally:
        _close_parent(
            parent_fd,
            active_error,
            published,
            cleanup_reporter,
            sync_directory=sync_directory,
            close_descriptor=close_parent,
        )


def replace_text(
    path: Path,
    content: str,
    *,
    stage_prefix: str,
    prepublish: Prepublish | None = None,
    cleanup_reporter: CleanupReporter | None = None,
    sync_directory: bool = False,
    anchor: ProjectAnchor | None = None,
) -> None:
    leaf = _target_leaf(path)
    if anchor is None:
        parent_fd = os.open(path.parent, _parent_open_flags(sync_directory))
        _replace_text_with_parent(
            parent_fd,
            leaf,
            content,
            stage_prefix=stage_prefix,
            prepublish=prepublish,
            cleanup_reporter=cleanup_reporter,
            sync_directory=sync_directory,
            close_parent=True,
        )
        return
    with anchor.parent(path) as (parent_fd, anchored_leaf):
        if anchored_leaf != leaf:
            raise OSError("anchored target changed")
        _replace_text_with_parent(
            parent_fd,
            leaf,
            content,
            stage_prefix=stage_prefix,
            prepublish=prepublish,
            cleanup_reporter=cleanup_reporter,
            sync_directory=sync_directory,
            close_parent=False,
        )


def _replace_text_with_parent(
    parent_fd: int,
    leaf: str,
    content: str,
    *,
    stage_prefix: str,
    prepublish: Prepublish | None,
    cleanup_reporter: CleanupReporter | None,
    sync_directory: bool,
    close_parent: bool,
) -> None:
    published = False
    active_error: BaseException | None = None
    try:
        if prepublish is not None:
            prepublish(parent_fd, leaf)
        _replace_text_at(
            parent_fd,
            leaf,
            content,
            stage_prefix=stage_prefix,
            cleanup_reporter=cleanup_reporter,
        )
        published = True
    except BaseException as error:
        active_error = error
        raise
    finally:
        _close_parent(
            parent_fd,
            active_error,
            published,
            cleanup_reporter,
            sync_directory=sync_directory,
            close_descriptor=close_parent,
        )


def replace_text_batch(
    parent: Path,
    entries: Iterable[tuple[str, str]],
    *,
    stage_prefix: str,
    leaf_guard: LeafGuard | None = None,
    on_published: PublishObserver | None = None,
    cleanup_reporter: CleanupReporter | None = None,
    sync_directory: bool = False,
    anchor: ProjectAnchor | None = None,
) -> None:
    ordered = tuple(entries)
    for leaf, _ in ordered:
        if not leaf or Path(leaf).name != leaf:
            raise ValueError(f"invalid output leaf: {leaf!r}")
    if anchor is None:
        parent_fd = os.open(parent, _parent_open_flags(sync_directory))
        _replace_text_batch_with_parent(
            parent_fd,
            ordered,
            stage_prefix=stage_prefix,
            leaf_guard=leaf_guard,
            on_published=on_published,
            cleanup_reporter=cleanup_reporter,
            sync_directory=sync_directory,
            close_parent=True,
        )
        return
    with anchor.directory(anchor.relative(parent)) as parent_fd:
        _replace_text_batch_with_parent(
            parent_fd,
            ordered,
            stage_prefix=stage_prefix,
            leaf_guard=leaf_guard,
            on_published=on_published,
            cleanup_reporter=cleanup_reporter,
            sync_directory=sync_directory,
            close_parent=False,
        )


def _replace_text_batch_with_parent(
    parent_fd: int,
    ordered: tuple[tuple[str, str], ...],
    *,
    stage_prefix: str,
    leaf_guard: LeafGuard | None,
    on_published: PublishObserver | None,
    cleanup_reporter: CleanupReporter | None,
    sync_directory: bool,
    close_parent: bool,
) -> None:
    published = False
    active_error: BaseException | None = None
    try:
        if leaf_guard is not None:
            for leaf, _ in ordered:
                leaf_guard(leaf, _target_stat(parent_fd, leaf))
        for leaf, content in ordered:
            _replace_text_at(
                parent_fd,
                leaf,
                content,
                stage_prefix=stage_prefix,
                cleanup_reporter=cleanup_reporter,
            )
            published = True
            if on_published is not None:
                on_published(leaf)
    except BaseException as error:
        active_error = error
        raise
    finally:
        _close_parent(
            parent_fd,
            active_error,
            published,
            cleanup_reporter,
            sync_directory=sync_directory,
            close_descriptor=close_parent,
        )


def _replace_text_at(
    parent_fd: int,
    leaf: str,
    content: str,
    *,
    stage_prefix: str,
    cleanup_reporter: CleanupReporter | None,
) -> None:
    target_mode: int | None = None
    target_stat = _target_stat(parent_fd, leaf)
    if target_stat is not None:
        if stat.S_ISDIR(target_stat.st_mode):
            raise IsADirectoryError(f"destination is a directory: {leaf}")
        if stat.S_ISREG(target_stat.st_mode):
            target_mode = stat.S_IMODE(target_stat.st_mode)

    staged = _stage_text(
        parent_fd,
        leaf,
        content,
        stage_prefix=stage_prefix,
        target_mode=target_mode,
        cleanup_reporter=cleanup_reporter,
    )
    try:
        os.replace(
            staged,
            leaf,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
    except BaseException as error:
        _unlink_stage(parent_fd, staged, error, cleanup_reporter)
        raise


def _target_stat(parent_fd: int, leaf: str) -> os.stat_result | None:
    try:
        return os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _stage_text(
    parent_fd: int,
    target_leaf: str,
    content: str,
    *,
    stage_prefix: str,
    target_mode: int | None,
    cleanup_reporter: CleanupReporter | None,
) -> str:
    final_mode = target_mode
    if final_mode is None:
        final_mode = _effective_new_mode(
            parent_fd,
            target_leaf,
            stage_prefix=stage_prefix,
            cleanup_reporter=cleanup_reporter,
        )

    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    for _ in range(16):
        staged = f"{stage_prefix}{secrets.token_hex(8)}.tmp"
        if staged == target_leaf:
            continue
        try:
            handle = _open_stage(parent_fd, staged, flags, cleanup_reporter)
        except FileExistsError:
            continue
        try:
            with handle:
                handle.write(content)
                handle.flush()
                os.fchmod(handle.fileno(), final_mode)
                # Persist record bytes and final permissions before publishing the
                # staged inode through a link or atomic replacement.
                os.fsync(handle.fileno())
        except BaseException as error:
            _unlink_stage(parent_fd, staged, error, cleanup_reporter)
            raise
        return staged
    raise OSError("unable to allocate a private staging file")


def _effective_new_mode(
    parent_fd: int,
    target_leaf: str,
    *,
    stage_prefix: str,
    cleanup_reporter: CleanupReporter | None,
) -> int:
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    for _ in range(16):
        probe = f"{stage_prefix}mode-{secrets.token_hex(8)}.tmp"
        if probe == target_leaf:
            continue
        try:
            descriptor = os.open(probe, flags, 0o666, dir_fd=parent_fd)
        except FileExistsError:
            continue

        try:
            mode = stat.S_IMODE(os.fstat(descriptor).st_mode)
        except BaseException as error:
            try:
                os.close(descriptor)
            except OSError as cleanup_error:
                _report_cleanup(error, cleanup_error, cleanup_reporter)
            _unlink_stage(parent_fd, probe, error, cleanup_reporter)
            raise

        try:
            os.close(descriptor)
        except BaseException as error:
            _unlink_stage(parent_fd, probe, error, cleanup_reporter)
            raise

        try:
            os.unlink(probe, dir_fd=parent_fd)
        except OSError as error:
            raise OSError(
                f"Temporary cleanup failed: temporary file {probe}: {error}"
            ) from error
        return mode
    raise OSError("unable to allocate a private permission probe")


def _open_stage(
    parent_fd: int,
    staged: str,
    flags: int,
    cleanup_reporter: CleanupReporter | None,
):
    descriptor = os.open(staged, flags, 0o600, dir_fd=parent_fd)
    try:
        return os.fdopen(descriptor, "w", encoding="utf-8")
    except BaseException as error:
        try:
            os.close(descriptor)
        except OSError as cleanup_error:
            _report_cleanup(error, cleanup_error, cleanup_reporter)
        _unlink_stage(parent_fd, staged, error, cleanup_reporter)
        raise


def _unlink_stage(
    parent_fd: int,
    staged: str,
    error: BaseException,
    cleanup_reporter: CleanupReporter | None,
) -> None:
    try:
        os.unlink(staged, dir_fd=parent_fd)
    except FileNotFoundError:
        pass
    except OSError as cleanup_error:
        cleanup_error = OSError(f"temporary file {staged}: {cleanup_error}")
        _report_cleanup(error, cleanup_error, cleanup_reporter)


def _close_parent(
    parent_fd: int,
    active_error: BaseException | None,
    published: bool,
    cleanup_reporter: CleanupReporter | None,
    *,
    sync_directory: bool,
    close_descriptor: bool = True,
) -> None:
    if published and sync_directory:
        try:
            # Persist link/rename/unlink metadata before acknowledging publication.
            os.fsync(parent_fd)
        except OSError as cleanup_error:
            if active_error is not None:
                _report_cleanup(active_error, cleanup_error, cleanup_reporter)
            else:
                active_error = PublishedCleanupError(
                    f"unable to synchronize destination directory: {cleanup_error}"
                )
    if close_descriptor:
        try:
            os.close(parent_fd)
        except OSError as cleanup_error:
            if active_error is not None:
                _report_cleanup(active_error, cleanup_error, cleanup_reporter)
            elif published:
                raise PublishedCleanupError(
                    f"unable to close destination directory: {cleanup_error}"
                ) from None
            else:
                raise
    if active_error is not None and isinstance(active_error, PublishedCleanupError):
        raise active_error


def _report_cleanup(
    error: BaseException,
    cleanup_error: OSError,
    cleanup_reporter: CleanupReporter | None,
) -> None:
    if cleanup_reporter is None:
        error.add_note(f"Temporary cleanup failed: {cleanup_error}")
    else:
        cleanup_reporter(error, cleanup_error)


def _target_leaf(path: Path) -> str:
    leaf = path.name
    if not leaf:
        raise IsADirectoryError(f"destination is a directory: {path}")
    return leaf


def _parent_open_flags(sync_directory: bool = False) -> int:
    # Durable callers use a descriptor that supports fsync. Generic atomic
    # writers retain O_PATH traversal compatibility on platforms without
    # O_SEARCH, including searchable-but-unreadable destination directories.
    if sync_directory:
        access_flag = getattr(os, "O_SEARCH", os.O_RDONLY)
    else:
        access_flag = getattr(os, "O_SEARCH", getattr(os, "O_PATH", os.O_RDONLY))
    return access_flag | getattr(os, "O_DIRECTORY", 0)


@contextmanager
def _open_parent(path: Path, *, sync_directory: bool) -> Iterator[int]:
    descriptor = os.open(path, _parent_open_flags(sync_directory))
    try:
        yield descriptor
    finally:
        os.close(descriptor)


def _relative_parts(value: Path | str) -> tuple[str, ...]:
    path = Path(value)
    if path.is_absolute():
        raise OSError("anchored path must be relative")
    parts = tuple(part for part in path.parts if part not in {"", "."})
    if any(part == ".." or Path(part).name != part for part in parts):
        raise OSError("anchored path is invalid")
    return parts
