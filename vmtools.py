"""Shared helpers for running Docker / docker compose inside Modal VM Sandboxes.

Modal VM Sandboxes (`experimental_options={"vm_runtime": True}`) give each
sandbox a real Linux kernel, so `dockerd` and `docker compose` run for real
(unlike gVisor sandboxes). Two non-obvious things are required to make Docker
work, and both are handled here:

  1. dockerd MUST be launched as a *held-open foreground exec that is never
     waited on* (see ``start_dockerd``). If you background it (``nohup dockerd &``)
     and let the launching exec return, Modal reaps that exec's process tree on
     return -- which kills dockerd AND the sandbox (``Sandbox.poll()`` -> 124,
     "already shut down").

  2. Docker's default networks (docker0 = ``172.17.0.0/16``, address pools across
     ``172.16.0.0/12``) overlap Modal's in-VM control network (the VM's own eth0
     is ``172.20.x`` and the resolver is ``172.21.0.1``). We relocate Docker's
     address space into free ``10.x`` via ``DAEMON_JSON``.

Requires modal >= 1.4.0. VM Sandboxes are an alpha feature but no longer require
an allowlisted workspace. See docs/GOTCHAS.md for the full story.
"""
import sys
import time

import modal

APP_NAME = "modal-vm-examples"

# Debian + Docker Engine (CE) + compose v2 plugin, from Docker's official apt repo.
docker_image = (
    modal.Image.debian_slim()
    .apt_install("ca-certificates", "curl", "gnupg", "iptables")
    .run_commands(
        "install -m 0755 -d /etc/apt/keyrings",
        "curl -fsSL https://download.docker.com/linux/debian/gpg "
        "  -o /etc/apt/keyrings/docker.asc",
        "chmod a+r /etc/apt/keyrings/docker.asc",
        'ARCH="$(dpkg --print-architecture)"; . /etc/os-release; '
        'echo "deb [arch=$ARCH signed-by=/etc/apt/keyrings/docker.asc] '
        'https://download.docker.com/linux/debian $VERSION_CODENAME stable" '
        "> /etc/apt/sources.list.d/docker.list",
        "apt-get update",
        "apt-get install -y --no-install-recommends "
        "  docker-ce docker-ce-cli containerd.io "
        "  docker-buildx-plugin docker-compose-plugin",
    )
)

# Relocate Docker's address space out of Modal's 172.16.0.0/12 control network.
DAEMON_JSON = """{
  "storage-driver": "overlay2",
  "bip": "10.200.0.1/24",
  "default-address-pools": [ { "base": "10.201.0.0/16", "size": 24 } ]
}
"""


def get_app(name=APP_NAME):
    """Look up (or create) the shared Modal App for these examples."""
    return modal.App.lookup(name, create_if_missing=True)


def create_sandbox(app, *, image=None, cpu=2, memory=4096, **kwargs):
    """Create a VM-runtime sandbox (`experimental_options={"vm_runtime": True}`)."""
    return modal.Sandbox.create(
        app=app,
        image=image if image is not None else docker_image,
        cpu=cpu,
        memory=memory,
        experimental_options={"vm_runtime": True},
        **kwargs,
    )


def poll(sb):
    """Return 'RUN' if the sandbox is alive, else 'EXIT(<code>)'."""
    rc = sb.poll()
    return "RUN" if rc is None else f"EXIT({rc})"


def run(sb, *cmd, check=True, quiet=False):
    """Exec argv in the sandbox; print + return (returncode, stdout, stderr)."""
    p = sb.exec(*cmd)
    p.wait()
    out, err = p.stdout.read(), p.stderr.read()
    if not quiet:
        if out.strip():
            print(out.rstrip())
        if err.strip():
            print(err.rstrip(), file=sys.stderr)
    if check and p.returncode != 0:
        raise RuntimeError(f"FAILED rc={p.returncode}: {' '.join(cmd)}")
    return p.returncode, out, err


def start_dockerd(sb):
    """Start dockerd correctly for a Modal VM sandbox and wait until ready.

    dockerd is launched as a held-open FOREGROUND exec whose handle is
    deliberately never waited on -- backgrounding it and letting the launching
    exec return kills the sandbox. Readiness is polled from SEPARATE execs.
    Returns the long-lived dockerd handle (keep a reference; ``sb.terminate()``
    cleans it up).
    """
    sb.filesystem.write_text(DAEMON_JSON, "/etc/docker/daemon.json")
    run(sb, "mkdir", "-p", "/var/log", quiet=True)
    dockerd = sb.exec("bash", "-lc", "exec dockerd >/var/log/dockerd.log 2>&1")
    for i in range(60):
        if sb.poll() is not None:
            raise RuntimeError("sandbox died during dockerd startup (poll != None)")
        rc, _, _ = run(sb, "docker", "info", check=False, quiet=True)
        if rc == 0:
            _, info, _ = run(
                sb, "docker", "info", "--format",
                "server={{.ServerVersion}} driver={{.Driver}} cgroup={{.CgroupVersion}}",
                quiet=True,
            )
            print(f"  dockerd ready after ~{i}s: {info.strip()}")
            return dockerd
        time.sleep(1)
    _, log, _ = run(sb, "tail", "-n", "80", "/var/log/dockerd.log", check=False, quiet=True)
    raise RuntimeError(f"dockerd did not become ready in 60s:\n{log}")
