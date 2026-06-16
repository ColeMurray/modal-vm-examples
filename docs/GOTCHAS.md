# Modal VM Sandbox gotchas (and how these examples handle them)

Two non-obvious things must be right to run Docker inside a Modal VM Sandbox.
Both are handled in [`../vmtools.py`](../vmtools.py). This doc explains *why*,
with the evidence that pinned each one down.

---

## 1. Launch `dockerd` as a held-open foreground exec (never waited)

### Symptom

You start `dockerd` and within ~1 second the sandbox is gone:

```
modal.exception.NotFoundError: Modal Sandbox with container ID ta-... not found.
This means this Sandbox has already shut down.
```

`Sandbox.poll()` returns `124`.

### What actually triggers it

It is **not** a Docker/network problem — it's *how `dockerd` is launched*. Holding
everything else identical and varying only the launch method:

| launch method (everything else identical) | result |
|---|---|
| `nohup sleep 600 &` — exec **returns** (a *bare* background process) | sandbox **DIED ~1s**, `EXIT(124)` |
| `nohup dockerd &` — exec **returns** | sandbox **DIED**, `EXIT(124)` |
| `exec dockerd` — held in a **foreground exec, never waited** | **SURVIVED**, docker ready |

The death happens ~1s **after** the launching exec returns — and note it happens
even for a bare `sleep`, so this is not a Docker problem.

### Why

A Modal VM Sandbox is a long-lived VM whose PID 1 is Modal's agent (`runch-agent`).
Each `sb.exec()` is a *transient* process tree that Modal tears down when the exec
returns (like `docker exec`). A backgrounded process is still part of that exec's
tree (`nohup` only ignores `SIGHUP`; `&` doesn't move it out of the exec's process
group in a non-interactive shell). So if you background a process and let the
launching exec return, the exec finishes **with a still-running child** — and
tearing down that exec's scope takes the **whole sandbox** with it (`poll()` → 124,
~1s later).

This is **general, not a Docker problem**: it reproduces with a bare
`nohup sleep 600 &` just as readily as with `dockerd`. Holding the process in a
foreground `exec` you never wait on means the exec never "finishes", so nothing is
torn down.

> The precise reaping scope (process-group vs cgroup) wasn't fully isolated, but it
> doesn't change the fix.

### The fix (see `start_dockerd` in `vmtools.py`)

```python
# held-open FOREGROUND exec -- deliberately NOT waited on
dockerd = sb.exec("bash", "-lc", "exec dockerd >/var/log/dockerd.log 2>&1")
# ...poll readiness from SEPARATE execs (docker info), then run compose, etc.
```

Note: **stopping** `dockerd` later via a *separate* exec (`pkill -TERM dockerd`) is
safe and does **not** kill the sandbox — only the launching-exec-returns case is
fatal. (The snapshot example relies on this.)

---

## 2. Move Docker's networks out of `172.16.0.0/12`

### Symptom

With stock Docker config, the sandbox dies as soon as Docker touches the network
(creating `docker0`, or `docker compose` creating a network).

### Why

The VM lives **inside** Modal's control network:

- the VM's own `eth0` is `172.20.x/16` (gateway `172.20.0.1`)
- the in-VM DNS resolver is `172.21.0.1`

i.e. Modal uses `172.16.0.0/12`. Docker's defaults sit on the **same block**:

- `docker0` defaults to `172.17.0.0/16`
- `default-address-pools` span `172.16.0.0/12`

When Docker installs bridge routes / addresses over that space it overlaps Modal's
plumbing and breaks the VM's path to Modal.

### The fix (see `DAEMON_JSON` in `vmtools.py`)

Relocate Docker entirely into free `10.x`, leaving iptables on (so published ports
work):

```json
{
  "storage-driver": "overlay2",
  "bip": "10.200.0.1/24",
  "default-address-pools": [ { "base": "10.201.0.0/16", "size": 24 } ]
}
```

---

## Other things to know

- **Exit code 124 / "already shut down"** is how an abruptly-terminated VM surfaces
  (here: heartbeat lost after the dockerd reap). The default sandbox `timeout` is
  also 300s ("max lifetime"), unrelated to the dockerd case (that fires in seconds).
- **GPUs are not supported**; **memory is static** (you get exactly the `memory` you
  request, default 1 GiB) while CPU is elastic (you can burst above the request).
- **`Sandbox.reload_volumes()` isn't supported**, and **root images ≥ 512 GiB won't
  start.**
- **Filesystem Snapshots are supported** (and include `/var/lib/docker` — see
  example 05); **Memory Snapshots are not yet supported** on VM Sandboxes.
- **`setuid` bits ARE preserved** (verified `mount`/`su`/`passwd` keep `-rwsr-xr-x`) —
  an earlier Modal limitation that has since been resolved.
