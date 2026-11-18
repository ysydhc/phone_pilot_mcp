"""Owned POSIX log pipeline: stop the producer, then drain filters to EOF."""

import os
import signal
import subprocess
import time


def start_pipeline(command, filters, fh):
    """Create a private process group; every stage is our direct child."""
    if os.name != "posix":
        raise OSError("log capture requires POSIX process groups")
    children = []
    upstream = None
    try:
        producer = subprocess.Popen(
            command,
            stdout=subprocess.PIPE if filters else fh,
            stderr=subprocess.DEVNULL,
            process_group=0,
        )
        children.append(producer)
        upstream = producer.stdout
        for index, argv in enumerate(filters):
            child = subprocess.Popen(
                argv,
                stdin=upstream,
                stdout=subprocess.PIPE if index < len(filters) - 1 else fh,
                stderr=subprocess.DEVNULL,
                process_group=producer.pid,
            )
            children.append(child)
            # The parent must not retain a pipe endpoint that prevents EOF/SIGPIPE.
            upstream.close()
            upstream = child.stdout
        return {"proc": producer, "children": children, "pgid": producer.pid, "fh": fh}
    except Exception:
        if upstream is not None:
            upstream.close()
        for child in children:
            try:
                child.kill()
            except OSError:
                pass
        for child in children:
            try:
                child.wait(timeout=3)
            except (OSError, subprocess.SubprocessError):
                pass
        raise


def _group_exists(info):
    if info.get("group_stopped"):
        return False
    pgid = info["pgid"]
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        info["group_stopped"] = True
        return False
    except PermissionError:
        pass  # macOS can return EPERM for a recently vanished group.
    snapshot = subprocess.run(
        ["ps", "-axo", "pgid=,stat="],
        capture_output=True,
        text=True,
        check=True,
        timeout=1,
    )
    try:
        members = [
            (int(group), state)
            for group, state in (line.split() for line in snapshot.stdout.splitlines())
        ]
        exists = any(
            group == pgid and not state.startswith("Z") for group, state in members
        )
    except ValueError as exc:
        raise OSError("cannot verify capture process group") from exc
    if not exists:
        info["group_stopped"] = True
    return exists


def _signal_group(info, sig):
    if info.get("group_stopped"):
        return
    try:
        os.killpg(info["pgid"], sig)
    except ProcessLookupError:
        info["group_stopped"] = True
    except PermissionError:
        if _group_exists(info):
            raise


def _failure_result(info, error):
    result = {"ok": False, "error": str(error)}
    if info.get("ownership_ended"):
        try:
            if not _group_exists(info):
                info["fh"].close()
                result.update(stopped=True, terminal=True)
        except (OSError, subprocess.SubprocessError):
            pass  # Unknown/live groups remain active; never signal after ownership ends.
    return result


def stop_pipeline(info):
    """Reap every owned stage. Return an error rather than claim a truncated drain."""
    producer = info["proc"]
    if (
        os.name != "posix"
        or info.get("pgid") != producer.pid
        or info["pgid"] == os.getpgrp()
    ):
        return {"ok": False, "error": "capture has no safe private process group"}
    children = info.get("children", [producer])
    if info.get("ownership_ended") or (
        info.get("drain_failed") and all(child.returncode is not None for child in children)
    ):
        return _failure_result(info, "capture ownership ended; cannot safely retry stop")
    try:
        # Resume stopped filters/producer so TERM can be handled and EOF can drain.
        # CONT never discards data, unlike terminating every reader in the group.
        _signal_group(info, signal.SIGCONT)
        producer.terminate()
        try:
            producer.wait(timeout=3)
        except subprocess.TimeoutExpired:
            producer.kill()
            producer.wait(timeout=3)
        deadline = time.monotonic() + 3
        try:
            for child in children[1:]:
                code = child.wait(timeout=max(0.01, deadline - time.monotonic()))
                if code not in (0, 1):  # grep 1 means no matching lines, not failure.
                    raise OSError(f"capture filter exited with status {code}")
        except (OSError, subprocess.TimeoutExpired):
            info["drain_failed"] = True
            if all(child.returncode is not None for child in children):
                info["ownership_ended"] = True
                raise
            _signal_group(info, signal.SIGTERM)
            # Escalate while at least one directly owned child is still alive,
            # before reaping every PID could allow the PGID to be recycled.
            try:
                for child in children:
                    child.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                _signal_group(info, signal.SIGKILL)
            for child in children:
                child.wait(timeout=3)
            info["ownership_ended"] = True
            raise
        info["ownership_ended"] = True
        if _group_exists(info):
            # All owned PIDs have been reaped. A remaining group is untrusted:
            # descendants or a recycled PGID; do not signal it after ownership ends.
            info["drain_failed"] = True
            raise OSError("capture has surviving descendants or an untrusted group")
        if info.get("drain_failed"):
            raise OSError("capture pipeline did not drain completely")
        info["fh"].close()
        return {"ok": True}
    except (OSError, subprocess.SubprocessError) as exc:
        return _failure_result(info, exc)
