"""Example 04 -- build a custom Docker image INSIDE the VM, then run it.

Demonstrates that `docker build` works in a Modal VM Sandbox: we write a small
build context, `docker build` an image from a Dockerfile, run the freshly built
image, and curl it from the sandbox host.

Run:  python examples/04_custom_image.py
"""
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import modal  # noqa: E402
import vmtools  # noqa: E402

DOCKERFILE = """FROM node:20-alpine
WORKDIR /app
COPY server.js .
EXPOSE 3000
CMD ["node", "server.js"]
"""

SERVER_JS = r"""const http = require('http');
const os = require('os');
http.createServer((req, res) => {
  res.setHeader('content-type', 'application/json');
  res.end(JSON.stringify({
    ok: true,
    msg: 'hello from an image built with docker build inside a Modal VM sandbox',
    container: os.hostname(),
  }));
}).listen(3000, () => console.log('listening on :3000'));
"""


def main():
    app = vmtools.get_app()
    with modal.enable_output():
        sb = vmtools.create_sandbox(app)
    print(f"sandbox: {sb.object_id}")

    try:
        vmtools.start_dockerd(sb)

        print("\n=== write build context + docker build ===")
        vmtools.run(sb, "mkdir", "-p", "/tmp/build", quiet=True)
        sb.filesystem.write_text(DOCKERFILE, "/tmp/build/Dockerfile")
        sb.filesystem.write_text(SERVER_JS, "/tmp/build/server.js")
        vmtools.run(sb, "docker", "build", "-t", "demo-api:latest", "/tmp/build")
        vmtools.run(sb, "docker", "images", "demo-api")

        print("\n=== run the freshly built image ===")
        vmtools.run(sb, "docker", "run", "-d", "--name", "api",
                    "-p", "3000:3000", "demo-api:latest")

        ok = False
        for _ in range(30):
            rc, body, _ = vmtools.run(sb, "curl", "-sf", "http://localhost:3000",
                                      check=False, quiet=True)
            if rc == 0 and body.strip():
                try:
                    ok = __import__("json").loads(body).get("ok") is True
                except ValueError:
                    ok = False
                if ok:
                    print(f"  response: {body.strip()}")
                    break
            if sb.poll() is not None:
                raise RuntimeError("sandbox died while waiting for the built image")
            time.sleep(1)
        assert ok, "the built image did not serve on :3000"

        print("\n" + "=" * 62)
        print("ALL CHECKS PASSED: built a custom image with `docker build` in the VM")
        print("=" * 62)
    finally:
        print("\nterminating sandbox ...")
        sb.terminate()


if __name__ == "__main__":
    main()
