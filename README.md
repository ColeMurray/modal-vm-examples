# modal-vm-examples — Modal VM Sandbox examples (Docker, docker compose, snapshots)

Runnable, reproducible **Modal VM** examples. Each one boots a [Modal](https://modal.com)
**VM Sandbox** (`experimental_options={"vm_runtime": True}`) — a sandbox backed by a
**real Linux kernel (microVM)** instead of gVisor — and demonstrates a real workload:
Docker, `docker compose`, a custom `docker build`, a Node.js + Postgres stack, and
Docker layer caching with filesystem snapshots.

If you searched for a **Modal VM example**, a way to **run Docker on Modal**, or
**docker compose on Modal**, start here.

> **Status:** Modal VM Sandboxes are an **alpha** feature. They are **no longer
> restricted to allowlisted workspaces** — any Modal workspace can use `vm_runtime`.
> See [Prerequisites](#prerequisites).

---

## Why a Modal VM Sandbox?

A normal Modal Sandbox runs on gVisor (a userspace kernel). A **Modal VM Sandbox**
runs on a full virtual machine, which gives you a **real Linux kernel**. That unlocks
workloads gVisor can't run cleanly:

- **Docker / dockerd** (overlay2 storage, bridge networking, published ports)
- **`docker compose`** multi-service stacks with inter-container DNS
- **`docker build`** of your own images, inside the sandbox
- Custom init systems (e.g. `systemd`), **eBPF**, **cgroups v2**, loopback mounts
- **Filesystem Snapshots** that include `/var/lib/docker`

## Examples

| # | Example | What it shows |
|---|---------|---------------|
| 01 | [`hello_vm`](examples/01_hello_vm.py) | Real kernel + loopback **ext4 mount** (gVisor can't) — no Docker |
| 02 | [`docker_compose`](examples/02_docker_compose.py) | `docker compose` up: nginx + a client, **inter-container DNS**, published port |
| 03 | [`node_postgres`](examples/03_node_postgres.py) | **Node.js API + Postgres**, healthcheck-gated startup, verified DB round-trip |
| 04 | [`custom_image`](examples/04_custom_image.py) | **`docker build`** a custom image inside the VM, then run it |
| 05 | [`filesystem_snapshot`](examples/05_filesystem_snapshot.py) | **Docker layer caching** across sandboxes via a Filesystem Snapshot |

Each example is a standalone script with programmatic assertions (it prints
`ALL CHECKS PASSED` and exits non-zero on failure).

## Prerequisites

1. **A Modal account.** VM Sandboxes are an alpha feature but no longer require
   allowlisting. You can confirm `vm_runtime` works when
   `Sandbox.create(..., experimental_options={"vm_runtime": True})` succeeds and
   `uname -r` reports a modern kernel (e.g. `6.12.x`) rather than the gVisor `4.4.0`.
2. **Python 3.10+** and the **Modal SDK ≥ 1.4.0** (the Sandbox filesystem API used
   here needs 1.4.0+):
   ```bash
   pip install -r requirements.txt
   modal token new      # one-time auth
   ```

## Quickstart

```bash
git clone https://github.com/ColeMurray/modal-vm-examples.git
cd modal-vm-examples
pip install -r requirements.txt

python examples/01_hello_vm.py          # start here
python examples/02_docker_compose.py
python examples/05_filesystem_snapshot.py
```

All examples share `vmtools.py`, which encapsulates the two non-obvious things you
must get right to run Docker in a Modal VM Sandbox (see below).

## Gotchas (important)

Running Docker inside a Modal VM Sandbox needs two specific things. Both are handled
for you in [`vmtools.py`](vmtools.py); the full explanation with evidence is in
[docs/GOTCHAS.md](docs/GOTCHAS.md).

1. **Launch `dockerd` as a held-open foreground exec.** If you background it
   (`nohup dockerd &`) and let the launching exec return, Modal reaps that exec's
   process tree on return — which kills `dockerd` **and the sandbox**
   (`Sandbox.poll()` → 124). Keep `dockerd` in a foreground `sb.exec(...)` you never
   wait on; drive `docker` via separate execs.

2. **Move Docker's networks out of `172.16.0.0/12`.** The VM lives inside Modal's
   control network (its own `eth0` is `172.20.x`, resolver `172.21.0.1`). Docker's
   default `docker0` (`172.17.0.0/16`) and address pools (`172.16.0.0/12`) overlap
   it, which breaks connectivity. We relocate them into free `10.x` via
   `daemon.json`.

## References

- [Modal docs](https://modal.com/docs) ·
  [Sandboxes guide](https://modal.com/docs/guide/sandbox) ·
  [Sandbox Snapshots](https://modal.com/docs/guide/sandbox-snapshots)
- Access / questions: [Modal Slack](https://modal.com/slack) · support@modal.com

## License

[MIT](LICENSE)
