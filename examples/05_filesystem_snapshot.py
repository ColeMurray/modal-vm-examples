"""Example 05 -- Docker layer caching across sandboxes via Filesystem Snapshots.

Modal Filesystem Snapshots capture the sandbox's filesystem -- including
`/var/lib/docker` -- into a reusable Image. So you can pull (or build) images
once, snapshot, and boot future VM sandboxes with those layers already present:
no re-pull, fast cold starts.

  builder  : start dockerd -> pull images -> stop dockerd -> snapshot_filesystem()
  restored : boot from the snapshot -> start dockerd -> images already there

(Memory Snapshots are not yet supported on VM sandboxes; Filesystem Snapshots are.)

Run:  python examples/05_filesystem_snapshot.py
"""
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import modal  # noqa: E402
import vmtools  # noqa: E402

CACHE = ["nginx:alpine", "postgres:16-alpine"]


def main():
    app = vmtools.get_app()

    print("=== builder: pull images, then snapshot /var/lib/docker ===")
    with modal.enable_output():
        b = vmtools.create_sandbox(app)
    try:
        vmtools.start_dockerd(b)
        for img in CACHE:
            print(f"  pulling {img} ...")
            vmtools.run(b, "docker", "pull", img, quiet=True)
        _, out, _ = vmtools.run(b, "docker", "images", "--format",
                                "{{.Repository}}:{{.Tag}}", quiet=True)
        print(f"  builder images: {' '.join(sorted(out.split()))}")

        # Stop dockerd cleanly so /var/lib/docker is consistent for the snapshot.
        print("  stopping dockerd for a consistent snapshot ...")
        vmtools.run(b, "bash", "-lc", "pkill -TERM dockerd 2>/dev/null || true",
                    check=False, quiet=True)
        for _ in range(20):
            _, st, _ = vmtools.run(b, "bash", "-lc",
                                   "pgrep dockerd >/dev/null && echo up || echo down",
                                   check=False, quiet=True)
            if st.strip() == "down":
                break
            if b.poll() is not None:
                raise RuntimeError("sandbox died while stopping dockerd")
            time.sleep(1)
        vmtools.run(b, "sync", check=False, quiet=True)
        print(f"  sandbox is {vmtools.poll(b)}; taking filesystem snapshot ...")
        snapshot = b.snapshot_filesystem()
        print(f"  snapshot image: {snapshot.object_id}")
    finally:
        b.terminate()

    print("\n=== restored: boot from snapshot, expect images already present ===")
    with modal.enable_output():
        r = vmtools.create_sandbox(app, image=snapshot)
    try:
        vmtools.start_dockerd(r)
        _, out, _ = vmtools.run(r, "docker", "images", "--format",
                                "{{.Repository}}:{{.Tag}}", quiet=True)
        present = set(out.split())
        print(f"  restored images (no pull performed): {' '.join(sorted(present))}")
        missing = [i for i in CACHE if i not in present]
        assert not missing, f"snapshot did not preserve docker images: missing {missing}"

        # Prove a cached image runs immediately (no pull).
        vmtools.run(r, "docker", "run", "-d", "--name", "web",
                    "-p", "8080:80", "nginx:alpine")
        served = False
        for _ in range(15):
            rc, html, _ = vmtools.run(r, "curl", "-sf", "http://localhost:8080",
                                      check=False, quiet=True)
            if rc == 0 and "nginx" in html.lower():
                served = True
                break
            time.sleep(1)
        assert served, "cached nginx image did not serve"

        print("\n" + "=" * 62)
        print("ALL CHECKS PASSED: /var/lib/docker persisted across sandboxes")
        print(f"  {len(CACHE)} images available instantly from the snapshot (zero re-pull)")
        print("=" * 62)
    finally:
        r.terminate()


if __name__ == "__main__":
    main()
