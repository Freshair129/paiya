"""External scrypt verifier storage and persistent unlock throttling."""

import base64
import csv
import getpass
import hashlib
import hmac
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_KDF_NAME = "scrypt-v1"
_SCRYPT_N = 2**15
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SCRYPT_MAXMEM_BYTES = 64 * 1024 * 1024
_SALT_BYTES = 16
_MIN_PASSPHRASE_LENGTH = 12
_FAILURE_WINDOW_SECONDS = 15 * 60
_MAX_FAILURES_PER_WINDOW = 5
_auth_file_lock = threading.Lock()


class AuthConfigurationError(RuntimeError):
    """The owner authentication verifier is unavailable or invalid."""


class AuthNotConfigured(AuthConfigurationError):
    """No owner passphrase verifier has been configured."""


class AuthenticationThrottled(RuntimeError):
    """Too many unlock failures occurred within the rolling window."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("Unlock attempts are temporarily throttled")
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True, slots=True)
class ScryptVerifier:
    """Verifier parameters persisted outside the repository."""

    salt: bytes
    digest: bytes
    n: int = _SCRYPT_N
    r: int = _SCRYPT_R
    p: int = _SCRYPT_P
    dklen: int = _SCRYPT_DKLEN


def auth_security_directory() -> Path:
    """Return the platform's per-user security directory outside the checkout."""
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA")
        if not root:
            raise AuthConfigurationError("LOCALAPPDATA is required for owner authentication")
        directory = Path(root).expanduser() / "Paiya" / "security"
    else:
        root = os.environ.get("XDG_DATA_HOME")
        data_root = Path(root).expanduser() if root else Path.home() / ".local" / "share"
        directory = data_root / "Paiya" / "security"
    resolved = directory.resolve()
    repository = Path(__file__).resolve().parents[3]
    if resolved == repository or resolved.is_relative_to(repository):
        raise AuthConfigurationError("Owner authentication data must be outside the repository")
    return resolved


def auth_verifier_path() -> Path:
    return auth_security_directory() / "owner-auth.json"


def auth_attempts_path() -> Path:
    return auth_security_directory() / "owner-auth-attempts.json"


def _current_user_sid() -> str:
    result = subprocess.run(
        ["whoami.exe", "/user", "/fo", "csv", "/nh"],
        check=True,
        capture_output=True,
        text=True,
    )
    rows = list(csv.reader(result.stdout.splitlines()))
    if not rows or not rows[0] or not rows[0][-1].startswith("S-1-"):
        raise AuthConfigurationError("Could not determine the owner account SID")
    return rows[0][-1]


def _set_windows_acl(path: Path, *, directory: bool) -> None:
    if shutil.which("icacls.exe") is None:
        raise AuthConfigurationError("icacls.exe is required to secure owner authentication data")
    try:
        sid = _current_user_sid()
        subprocess.run(
            ["icacls.exe", str(path), "/reset"],
            check=True,
            capture_output=True,
            text=True,
        )
        owner_permissions = f"*{sid}:(OI)(CI)F" if directory else f"*{sid}:F"
        system_permissions = "*S-1-5-18:(OI)(CI)F" if directory else "*S-1-5-18:F"
        subprocess.run(
            [
                "icacls.exe",
                str(path),
                "/inheritance:r",
                "/grant:r",
                owner_permissions,
                system_permissions,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError, AuthConfigurationError):
        raise AuthConfigurationError("Could not secure owner authentication permissions") from None


def _secure_directory(path: Path) -> None:
    if path.is_symlink():
        raise AuthConfigurationError("Owner authentication directory cannot be a symlink")
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        _set_windows_acl(path, directory=True)
    else:
        path.chmod(0o700)


def _secure_file(path: Path) -> None:
    if path.is_symlink():
        raise AuthConfigurationError("Owner authentication file cannot be a symlink")
    if os.name == "nt":
        _set_windows_acl(path, directory=False)
    else:
        path.chmod(0o600)


def _atomic_json_write(path: Path, value: dict[str, Any]) -> None:
    _secure_directory(path.parent)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(json.dumps(value, separators=(",", ":"), sort_keys=True))
            temporary.flush()
            os.fsync(temporary.fileno())
        _secure_file(temporary_path)
        os.replace(temporary_path, path)
        _secure_file(path)
    except OSError as error:
        raise AuthConfigurationError("Could not write owner authentication data") from error
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _derive(passphrase: str, salt: bytes, *, n: int, r: int, p: int, dklen: int) -> bytes:
    return hashlib.scrypt(
        passphrase.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=dklen,
        maxmem=_SCRYPT_MAXMEM_BYTES,
    )


def create_auth_verifier(passphrase: str) -> ScryptVerifier:
    """Persist a newly salted scrypt verifier without retaining the passphrase."""
    if len(passphrase) < _MIN_PASSPHRASE_LENGTH:
        raise ValueError("passphrase_too_short")
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = _derive(
        passphrase,
        salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    verifier = ScryptVerifier(salt=salt, digest=digest)
    _atomic_json_write(
        auth_verifier_path(),
        {
            "kdf": _KDF_NAME,
            "n": verifier.n,
            "r": verifier.r,
            "p": verifier.p,
            "dklen": verifier.dklen,
            "salt": base64.b64encode(verifier.salt).decode("ascii"),
            "verifier": base64.b64encode(verifier.digest).decode("ascii"),
        },
    )
    return verifier


def prompt_owner_passphrase() -> str:
    """Collect and confirm a passphrase without echoing it to the terminal."""
    if not sys.stdin.isatty():
        raise AuthConfigurationError(
            "Passphrase setup needs an interactive terminal; run paiya auth set-passphrase"
        )
    passphrase = getpass.getpass("Owner passphrase: ")
    if len(passphrase) < _MIN_PASSPHRASE_LENGTH:
        raise ValueError("passphrase_too_short")
    confirmation = getpass.getpass("Confirm owner passphrase: ")
    if not hmac.compare_digest(passphrase, confirmation):
        raise ValueError("passphrase_mismatch")
    return passphrase


def ensure_auth_verifier() -> None:
    """Create the initial verifier from a terminal, failing closed otherwise."""
    if auth_verifier_path().is_file():
        return
    create_auth_verifier(prompt_owner_passphrase())


def _load_verifier() -> ScryptVerifier:
    path = auth_verifier_path()
    if not path.is_file():
        raise AuthNotConfigured("Owner passphrase is not configured")
    _secure_file(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data["kdf"] != _KDF_NAME:
            raise ValueError
        verifier = ScryptVerifier(
            salt=base64.b64decode(data["salt"], validate=True),
            digest=base64.b64decode(data["verifier"], validate=True),
            n=int(data["n"]),
            r=int(data["r"]),
            p=int(data["p"]),
            dklen=int(data["dklen"]),
        )
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        raise AuthConfigurationError("Owner passphrase verifier is invalid") from None
    if len(verifier.salt) != _SALT_BYTES or len(verifier.digest) != verifier.dklen:
        raise AuthConfigurationError("Owner passphrase verifier is invalid")
    return verifier


@contextmanager
def _attempts_lock(path: Path) -> Iterator[None]:
    _secure_directory(path.parent)
    with _auth_file_lock:
        lock_path = path.with_name(f".{path.name}.lock")
        with lock_path.open("a+b") as handle:
            if sys.platform == "win32":
                import msvcrt

                handle.seek(0)
                if handle.read(1) == b"":
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl_module: Any = __import__("fcntl")

                fcntl_module.flock(handle.fileno(), fcntl_module.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl_module.flock(handle.fileno(), fcntl_module.LOCK_UN)


def _read_failure_times(path: Path) -> list[float]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        failures = data["failed_at"]
        if not isinstance(failures, list) or not all(
            isinstance(value, (int, float)) for value in failures
        ):
            raise ValueError
        return [float(value) for value in failures]
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        raise AuthConfigurationError("Unlock attempt state is invalid") from None


def verify_owner_passphrase(passphrase: str, *, now: float | None = None) -> bool:
    """Check the passphrase and enforce five failures per rolling fifteen minutes."""
    verifier = _load_verifier()
    path = auth_attempts_path()
    current = now if now is not None else time.time()
    with _attempts_lock(path):
        failures = [
            attempted_at
            for attempted_at in _read_failure_times(path)
            if attempted_at > current - _FAILURE_WINDOW_SECONDS
        ]
        if len(failures) >= _MAX_FAILURES_PER_WINDOW:
            retry_after = max(1, int(failures[0] + _FAILURE_WINDOW_SECONDS - current))
            _atomic_json_write(path, {"failed_at": failures})
            raise AuthenticationThrottled(retry_after)
        candidate = _derive(
            passphrase,
            verifier.salt,
            n=verifier.n,
            r=verifier.r,
            p=verifier.p,
            dklen=verifier.dklen,
        )
        if hmac.compare_digest(candidate, verifier.digest):
            if path.exists():
                _atomic_json_write(path, {"failed_at": []})
            return True
        failures.append(current)
        _atomic_json_write(path, {"failed_at": failures})
        return False
