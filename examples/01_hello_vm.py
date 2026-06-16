"""Example 01 -- hello, VM: a real kernel you can mount filesystems on.

The simplest proof that a Modal VM Sandbox is a real virtual machine, not
gVisor: print the kernel and loopback-mount an ext4 image (which gVisor can't
do). No Docker required.

Run:  python examples/01_hello_vm.py
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import modal  # noqa: E402
import vmtools  # noqa: E402

MOUNT_SH = r"""set -euo pipefail
echo "== kernel (real, not the gVisor 4.4.0 fake) =="
uname -a
echo "== format + loopback-mount an ext4 image (needs a real kernel) =="
truncate -s 64M /tmp/disk.img
mkfs.ext4 -F -q /tmp/disk.img
mkdir -p /mnt/loop
mount -o loop /tmp/disk.img /mnt/loop
echo "hello from a Modal VM sandbox" > /mnt/loop/proof.txt
cat /mnt/loop/proof.txt
df -h /mnt/loop | tail -1
"""


def main():
    app = vmtools.get_app()
    # Minimal image: mkfs.ext4 (e2fsprogs) + mount (util-linux). As root in the
    # sandbox, mount does not need its setuid bit.
    image = modal.Image.debian_slim().apt_install("e2fsprogs", "util-linux")
    with modal.enable_output():
        sb = vmtools.create_sandbox(app, image=image)
    print(f"sandbox: {sb.object_id}")
    try:
        sb.filesystem.write_text(MOUNT_SH, "/tmp/mount.sh")
        rc, _, _ = vmtools.run(sb, "bash", "/tmp/mount.sh", check=False)
        assert rc == 0, "loopback mount failed -- is this really a VM sandbox?"
        print("\n" + "=" * 60)
        print("ALL CHECKS PASSED: real kernel + loopback ext4 mount in a VM sandbox")
        print("=" * 60)
    finally:
        sb.terminate()


if __name__ == "__main__":
    main()
