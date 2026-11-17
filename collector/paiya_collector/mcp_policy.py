"""Deterministic boundaries shared by the restricted MCP browser service."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from paiya_collector.browser_adapter import RateLimitPause, _matches_group_path, parse_retry_after
from paiya_collector.browser_runtime import BrowserAdapterError, _http_origin
from paiya_collector.state import ensure_no_reparse_components


class RequestPolicy:
    def __init__(self, group_url: str, *, synthetic: bool = False, asset_origins=()):
        parsed = urlsplit(group_url)
        if synthetic:
            if parsed.scheme != "http" or parsed.hostname != "localhost" or not parsed.port:
                raise ValueError("synthetic MCP requires an explicit localhost loopback port")
        elif (
            parsed.scheme != "https"
            or parsed.hostname not in {"www.facebook.com", "facebook.com", "m.facebook.com"}
            or parsed.port is not None
        ):
            raise ValueError("MCP requires the configured plain HTTPS Facebook origin")
        if parsed.username or parsed.password or not parsed.path.startswith("/groups/"):
            raise ValueError("invalid MCP group URL")
        self.origin = _http_origin(group_url)
        if not isinstance(asset_origins, (list, tuple)) or len(asset_origins) > 8:
            raise ValueError("asset_origins must contain at most eight explicit origins")
        self.asset_origins = set()
        for origin in asset_origins:
            if not isinstance(origin, str):
                raise ValueError("asset origin must be text")
            asset = urlsplit(origin)
            safe_origin = (
                (
                    asset.scheme == "http"
                    and asset.hostname == "localhost"
                    and asset.port is not None
                )
                if synthetic
                else (asset.scheme == "https" and asset.hostname and asset.port is None)
            )
            if (
                not safe_origin
                or asset.username
                or asset.password
                or asset.path not in {"", "/"}
                or asset.query
                or asset.fragment
            ):
                raise ValueError("asset origin must be a plain explicit origin")
            self.asset_origins.add(_http_origin(origin))
        self.group_url = group_url
        self.failed = False
        self.limited = False
        self.deadline: datetime | None = None

    def allow(
        self,
        url: str,
        *,
        document: bool,
        resource_type: str = "",
        method: str = "GET",
        has_body: bool = False,
    ) -> bool:
        try:
            parsed = urlsplit(url)
            allowed = (
                not self.failed
                and not self.limited
                and not parsed.username
                and not parsed.password
                and (
                    _http_origin(url) == self.origin
                    or (
                        not document
                        and _http_origin(url) in self.asset_origins
                        and resource_type in {"Image", "Stylesheet", "Font", "Script"}
                        and method == "GET"
                        and not has_body
                    )
                )
            )
            if document:
                head = parsed.path.casefold().strip("/").split("/", 1)[0]
                allowed = (
                    allowed
                    and _http_origin(url) == self.origin
                    and (
                        _matches_group_path(url, self.group_url)
                        or head in {"login", "checkpoint", "challenge", "security"}
                    )
                )
            if not allowed and document:
                self.failed = True
            return bool(allowed)
        except (ValueError, TypeError):
            self.failed = True
            return False

    def is_external_asset(self, url: str) -> bool:
        return _http_origin(url) != self.origin

    def response(self, status: int, retry_after: str | None, received: datetime) -> None:
        if status != 429:
            return
        current = received if received.tzinfo else received.replace(tzinfo=UTC)
        deadline = parse_retry_after(retry_after, now=current)
        if not self.limited:
            self.deadline = deadline
        elif self.deadline is not None:
            self.deadline = None if deadline is None else max(self.deadline, deadline)
        self.limited = True

    def check(self) -> None:
        if self.limited:
            raise RateLimitPause(self.deadline)
        if self.failed:
            raise BrowserAdapterError("MCP browser guard failed; checkpoint is unchanged")


class ProfileLease:
    """OS byte lock held until the exact launched process exits; never delete profiles."""

    def __init__(self, path: Path, *, repository_root: Path):
        self.path = Path(os.path.abspath(path))
        self.repository_root = repository_root.resolve()
        self._file = None

    def __enter__(self):
        ensure_no_reparse_components(self.path, label="MCP profile")
        resolved = self.path.resolve()
        if resolved.is_relative_to(self.repository_root):
            raise ValueError("MCP profile must be outside the repository")
        if resolved.drive.casefold() != self.repository_root.drive.casefold():
            raise ValueError("MCP profile must be on the Collector volume")
        for vendor in ("Google/Chrome/User Data", "Microsoft/Edge/User Data"):
            normal = (Path.home() / "AppData/Local" / vendor).resolve()
            if resolved == normal or resolved.is_relative_to(normal):
                raise ValueError("everyday browser profiles are protected")
        marker = self.path / "PAIYA_COLLECTOR_PROFILE"
        ensure_no_reparse_components(marker, label="MCP profile marker")
        if not marker.is_file() or marker.read_text(encoding="utf-8").strip() != "owner-group-01":
            raise ValueError("MCP profile marker is missing or does not match")
        lock = self.path / "PAIYA_MCP_LOCK"
        ensure_no_reparse_components(lock, label="MCP profile lock")
        self._file = lock.open("a+b")
        try:
            self._file.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._file.close()
            self._file = None
            raise ValueError("MCP profile is locked by another owner") from exc
        try:
            if (self.path / "DevToolsActivePort").exists():
                raise ValueError(
                    "existing DevToolsActivePort is protected; close owner browser first"
                )
            # A fixed-port Chrome may have no DevToolsActivePort. Inspect only command
            # lines, never kill/profile-match any process, and fail on inaccessible Chrome.
            import psutil

            wanted = os.path.normcase(str(resolved))
            for proc in psutil.process_iter(["name"]):
                if (proc.info["name"] or "").lower() not in {
                    "chrome.exe",
                    "msedge.exe",
                    "chrome",
                    "chromium",
                }:
                    continue
                try:
                    args = proc.cmdline()
                except psutil.NoSuchProcess:
                    continue
                except psutil.AccessDenied as exc:
                    raise ValueError("browser profile ownership cannot be verified") from exc
                for index, arg in enumerate(args):
                    value = (
                        arg.split("=", 1)[1]
                        if arg.startswith("--user-data-dir=")
                        else (
                            args[index + 1]
                            if arg == "--user-data-dir" and index + 1 < len(args)
                            else None
                        )
                    )
                    if value and os.path.normcase(str(Path(value).resolve())) == wanted:
                        # A process snapshot can retain readable arguments after
                        # termination. Require a zero-time exit check before
                        # treating metadata as a live owner; never terminate it.
                        try:
                            # process_iter caches objects; use a fresh object so
                            # a cached exit code cannot hide a reused live PID.
                            psutil.Process(proc.pid).wait(timeout=0)
                        except psutil.NoSuchProcess:
                            break
                        except psutil.TimeoutExpired as exc:
                            raise ValueError(
                                f"MCP profile is owned by a running browser (PID {proc.pid})"
                            ) from exc
                        except psutil.AccessDenied as exc:
                            raise ValueError(
                                "browser profile ownership cannot be verified"
                            ) from exc
                        break
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        if self._file is not None:
            self._file.close()
            self._file = None
