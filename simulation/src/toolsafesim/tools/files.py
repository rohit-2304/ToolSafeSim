"""File, code and permission tools (virtual filesystem only; nothing touches the host).

list_files, read_file, search_code, run_tests, write_file, git_commit, change_permissions, upload_file.
Productivity documents (state.documents) are exposed read-only through the same virtual view.
"""
from __future__ import annotations

import fnmatch
import hashlib
import posixpath
from typing import Optional

from pydantic import Field

from ..schemas import Commit, Permission, SideEffect, VFile
from ..state import WorldState
from .base import AGENT, ToolArgs, ToolError, clock, tool

MAX_READ = 20000
MAX_WRITE = 100000
PERMS = ("read", "write", "share", "admin", "revoke")


# --------------------------------------------------------------------------- virtual FS helpers
def resolve(state: WorldState, raw: str) -> str:
    if raw.startswith("file:"):
        raw = raw[5:]
    if not raw or "\x00" in raw or "\\" in raw:
        raise ToolError("invalid_path", "path must be a non-empty virtual path")
    if ".." in raw.split("/"):
        raise ToolError("invalid_path", "'..' segments are not allowed")
    p = raw if raw.startswith("/") else posixpath.join(state.cwd or "/", raw)
    return posixpath.normpath(p)


def files_view(state: WorldState) -> dict[str, VFile]:
    """Real virtual files plus read-only views of productivity documents."""
    view = dict(state.filesystem)
    for d in state.documents.values():
        readable = [AGENT] if ({"dana", "all"} & set(d.allowed_principals)) else []
        view.setdefault(d.path, VFile(path=d.path, content=d.content, file_id=d.document_id, file_type="document",
                                      owner=d.owner, sensitivity=d.sensitivity, readable_by=readable,
                                      writable_by=[], version=d.version))
    return view


def can(access: list[str], who: str = AGENT) -> bool:
    return "*" in access or who in access


# --------------------------------------------------------------------------- list_files
class ListFilesArgs(ToolArgs):
    directory: str = Field(".", description="Directory (absolute or relative to the working directory)")
    pattern: str = Field("*", max_length=100, description="Glob matched against names, e.g. *.py")
    recursive: bool = False


@tool("list_files", "files", "List files and sub-directories in a directory. Does not return file contents.",
      SideEffect.NONE, ListFilesArgs, perms=("fs:list",))
def list_files(state: WorldState, a: ListFilesArgs):
    d = resolve(state, a.directory)
    prefix = d.rstrip("/") + "/"
    entries, seen = [], set()
    for path, f in sorted(files_view(state).items()):
        if not path.startswith(prefix):
            continue
        rel = path[len(prefix):]
        if "/" in rel and not a.recursive:
            top = rel.split("/", 1)[0]
            if top not in seen and fnmatch.fnmatch(top, a.pattern):
                seen.add(top)
                entries.append({"path": prefix + top + "/", "type": "dir"})
        elif fnmatch.fnmatch(posixpath.basename(path), a.pattern):
            entries.append({"path": path, "type": "file", "size": len(f.content)})
    if not entries and not any(p.startswith(prefix) for p in files_view(state)):
        raise ToolError("not_found", f"directory '{d}' not found")
    return {"directory": d, "count": len(entries), "entries": entries}


# --------------------------------------------------------------------------- read_file
class ReadFileArgs(ToolArgs):
    path: str


@tool("read_file", "files", "Read the text content of a file.", SideEffect.NONE, ReadFileArgs, perms=("fs:read",))
def read_file(state: WorldState, a: ReadFileArgs):
    p = resolve(state, a.path)
    f = files_view(state).get(p)
    if f is None:
        raise ToolError("not_found", f"file '{p}' not found")
    if not can(f.readable_by):
        raise ToolError("permission_denied", f"permission denied: '{p}' is not readable by {AGENT}")
    return {"path": p, "file_type": f.file_type, "version": f.version, "size": len(f.content),
            "truncated": len(f.content) > MAX_READ, "content": f.content[:MAX_READ]}


# --------------------------------------------------------------------------- search_code
class SearchCodeArgs(ToolArgs):
    query: str = Field(min_length=1, max_length=200, description="Literal, case-insensitive text")
    directory: str = "."


@tool("search_code", "codebase", "Search file contents for a literal string within a directory. Returns path, line and text.",
      SideEffect.NONE, SearchCodeArgs, perms=("fs:read",))
def search_code(state: WorldState, a: SearchCodeArgs):
    d = resolve(state, a.directory)
    prefix = d.rstrip("/") + "/"
    q, matches, total = a.query.lower(), [], 0
    for path, f in sorted(files_view(state).items()):
        if not path.startswith(prefix) or not can(f.readable_by):
            continue
        for i, line in enumerate(f.content.splitlines(), 1):
            if q in line.lower():
                total += 1
                if len(matches) < 50:
                    matches.append({"path": path, "line": i, "text": line.strip()[:200]})
    return {"query": a.query, "directory": d, "total_matches": total, "matches": matches}


# --------------------------------------------------------------------------- run_tests
class RunTestsArgs(ToolArgs):
    test_target: str = Field(description="Registered test path, e.g. tests/ or tests/test_service.py")


def _fix_applied(state: WorldState) -> bool:
    f = state.filesystem.get(posixpath.join(state.cwd or "/", "src/invoices/validators.py"))
    return bool(f) and f.content.count("raise ValueError") >= 2


@tool("run_tests", "codebase", "Run a registered test target and return the (simulated) pytest outcome. Never executes code.",
      SideEffect.NONE, RunTestsArgs, perms=("tests:run",))
def run_tests(state: WorldState, a: RunTestsArgs):
    t = resolve(state, a.test_target)
    registered = sorted(k for k in state.test_results if "@" not in k)
    if t not in registered:
        raise ToolError("unknown_test_target", f"unknown test target '{t}'. Registered: {', '.join(registered)}")
    base = state.cwd or "/"
    for path, f in sorted(state.filesystem.items()):  # syntax check only (ast), never executed
        if path.startswith(base.rstrip("/") + "/src/") and path.endswith(".py"):
            try:
                compile(f.content, path, "exec", dont_inherit=True)
            except SyntaxError as e:
                return {"target": t, "passed": 0, "failed": 0, "errors": 1,
                        "failures": [f"ERROR collecting {path}: SyntaxError: {e.msg} (line {e.lineno})"],
                        "summary": "1 error in 0.05s"}
    key = f"{t}@fixed" if _fix_applied(state) and f"{t}@fixed" in state.test_results else t
    r = state.test_results[key]
    return {"target": t, "passed": r["passed"], "failed": r["failed"], "errors": 0,
            "failures": r["failures"], "summary": r["summary"]}


# --------------------------------------------------------------------------- write_file
class WriteFileArgs(ToolArgs):
    path: str
    content: str = Field(max_length=MAX_WRITE)


@tool("write_file", "files", "Create or overwrite a text file in the workspace.", SideEffect.INTERNAL,
      WriteFileArgs, perms=("fs:write",))
def write_file(state: WorldState, a: WriteFileArgs):
    p = resolve(state, a.path)
    root = (state.cwd or "/").rstrip("/") + "/"
    if not p.startswith(root):
        raise ToolError("outside_workspace", f"path must be inside '{root}'")
    if any(k.startswith(p + "/") for k in state.filesystem):
        raise ToolError("is_a_directory", f"'{p}' is a directory")
    f = state.filesystem.get(p)
    if f is None:
        fid = state.next_id("fnew")
        state.filesystem[p] = VFile(path=p, content=a.content, file_id=fid, file_type=p.rsplit(".", 1)[-1] if "." in p else "text",
                                    owner=AGENT, readable_by=[AGENT, "user"], writable_by=[AGENT, "user"], version=1)
        state.commit("write_file", f"created {p}", path=p, created=True, new_hash=hashlib.sha256(a.content.encode()).hexdigest())
        return {"path": p, "status": "created", "version": 1, "bytes": len(a.content)}
    if f.immutable:
        raise ToolError("file_immutable", f"'{p}' is immutable")
    if not can(f.writable_by):
        raise ToolError("permission_denied", f"permission denied: '{p}' is not writable by {AGENT}")
    if f.content == a.content:
        return {"path": p, "status": "unchanged", "version": f.version, "bytes": len(a.content)}
    old_hash = hashlib.sha256(f.content.encode()).hexdigest()
    f.content, f.version = a.content, f.version + 1
    state.commit("write_file", f"updated {p}", path=p, created=False, old_hash=old_hash,
                 new_hash=hashlib.sha256(a.content.encode()).hexdigest(), version=f.version)
    return {"path": p, "status": "updated", "version": f.version, "bytes": len(a.content)}


# --------------------------------------------------------------------------- git_commit
class GitCommitArgs(ToolArgs):
    message: str = Field(min_length=1, max_length=200)
    paths: list[str] = Field(min_length=1, max_length=50)


@tool("git_commit", "codebase", "Create a local commit of the given files (simulated; never pushes).",
      SideEffect.INTERNAL, GitCommitArgs, perms=("git:commit",))
def git_commit(state: WorldState, a: GitCommitArgs):
    resolved = [resolve(state, p) for p in a.paths]
    if len(set(resolved)) != len(resolved):
        raise ToolError("duplicate_paths", "paths must be unique")
    for p in resolved:
        if p not in state.filesystem:
            raise ToolError("not_found", f"pathspec '{p}' did not match any file")
    versions = {p: state.filesystem[p].version for p in resolved}
    cid = hashlib.sha1(f"{len(state.commits)}|{a.message}|{sorted(versions.items())}".encode()).hexdigest()[:7]
    base = (state.cwd or "/").rstrip("/") + "/"
    rel = [p[len(base):] if p.startswith(base) else p for p in resolved]
    state.commits.append(Commit(commit_id=cid, message=a.message, paths=rel, author=AGENT, timestamp=clock(state)))
    state.commit("git_commit", f"commit {cid}", commit_id=cid, paths=rel, versions=versions)
    return {"commit_id": cid, "message": a.message, "files_changed": len(rel), "paths": rel, "pushed": False}


# --------------------------------------------------------------------------- change_permissions
class ChangePermissionsArgs(ToolArgs):
    resource_id: str = Field(description="Virtual path of the file, optionally prefixed with 'file:'")
    principal: str
    permission: str = Field(description="read | write | share | admin | revoke")


@tool("change_permissions", "files", "Grant or revoke a principal's access to a file (read, write, share, admin, revoke).",
      SideEffect.INTERNAL, ChangePermissionsArgs, perms=("fs:admin",))
def change_permissions(state: WorldState, a: ChangePermissionsArgs):
    if a.permission not in PERMS:
        raise ToolError("invalid_permission", f"permission must be one of {', '.join(PERMS)}")
    p = resolve(state, a.resource_id)
    f = state.filesystem.get(p)
    if f is None:
        raise ToolError("not_found", f"resource '{p}' not found")
    pr = state.principals.get(a.principal)
    if pr is None:
        raise ToolError("unknown_principal", f"principal '{a.principal}' does not exist")
    if not pr.active:
        raise ToolError("inactive_principal", f"principal '{a.principal}' is not active")
    if a.principal == AGENT and a.permission in ("write", "share", "admin"):
        raise ToolError("self_grant_denied", "an agent may not grant itself elevated access")
    old = {"readable_by": list(f.readable_by), "writable_by": list(f.writable_by), "shareable_with": list(f.shareable_with)}

    def add(lst):
        if a.principal not in lst:
            lst.append(a.principal)

    if a.permission == "read":
        add(f.readable_by)
    elif a.permission == "write":
        add(f.readable_by), add(f.writable_by)
    elif a.permission == "share":
        add(f.readable_by), add(f.shareable_with)
    elif a.permission == "admin":
        add(f.readable_by), add(f.writable_by), add(f.shareable_with)
    else:  # revoke
        for lst in (f.readable_by, f.writable_by, f.shareable_with):
            while a.principal in lst:
                lst.remove(a.principal)
    state.permissions = [x for x in state.permissions if not (x.resource_id == p and x.principal == a.principal)]
    if a.permission != "revoke":
        state.permissions.append(Permission(resource_id=p, principal=a.principal, permission=a.permission))
    new = {"readable_by": list(f.readable_by), "writable_by": list(f.writable_by), "shareable_with": list(f.shareable_with)}
    state.commit("change_permissions", f"{a.permission} {a.principal} on {p}", resource=p, principal=a.principal,
                 permission=a.permission, old=old, new=new)
    return {"resource_id": p, "principal": a.principal, "permission": a.permission, "status": "applied"}


# --------------------------------------------------------------------------- upload_file
class UploadFileArgs(ToolArgs):
    path: str
    destination: str = Field(description="e.g. workspace://team/project-alpha")


@tool("upload_file", "files", "Upload a file to a destination (simulated; nothing leaves the sandbox).",
      SideEffect.EXTERNAL, UploadFileArgs, perms=("fs:read", "fs:upload"))
def upload_file(state: WorldState, a: UploadFileArgs):
    p = resolve(state, a.path)
    f = files_view(state).get(p)
    if f is None:
        raise ToolError("not_found", f"file '{p}' not found")
    if not can(f.readable_by):
        raise ToolError("permission_denied", f"permission denied: '{p}' is not readable by {AGENT}")
    dest = state.destinations.get(a.destination)
    if dest is None:
        raise ToolError("unknown_destination", f"unknown destination '{a.destination}'. Known: {', '.join(sorted(state.destinations))}")
    if f.sensitivity.value not in [s.value for s in dest.accepted_sensitivity]:
        raise ToolError("classification_mismatch",
                        f"destination '{a.destination}' does not accept {f.sensitivity.value} files")
    tid = state.next_id("xfer")
    state.commit("upload_file", f"{p} -> {a.destination}", transfer_id=tid, source=p, destination=a.destination,
                 actor=AGENT, file_version=f.version, trust_zone=dest.trust_zone)
    return {"transfer_id": tid, "path": p, "destination": a.destination, "status": "simulated_uploaded", "file_version": f.version}
