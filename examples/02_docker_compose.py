"""Example 02 -- docker compose inside a Modal VM Sandbox.

nginx `web` (publishes :8080) + a `client` that curls web by service name.
Proves: real dockerd, inter-container DNS, and a published host port.

Run:  python examples/02_docker_compose.py
"""
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import modal  # noqa: E402
import vmtools  # noqa: E402

COMPOSE_YML = """services:
  web:
    image: nginx:alpine
    ports:
      - "8080:80"
  client:
    image: curlimages/curl:latest
    depends_on: [web]
    entrypoint: ["sh", "-c"]
    command: ["until curl -sf http://web >/dev/null 2>&1; do echo waiting-for-web; sleep 1; done; echo INTER_CONTAINER_OK; sleep 3600"]
"""
C = ["docker", "compose", "-p", "web", "-f", "/tmp/app/docker-compose.yml"]


def main():
    app = vmtools.get_app()
    with modal.enable_output():
        sb = vmtools.create_sandbox(app)
    print(f"sandbox: {sb.object_id}")
    try:
        vmtools.start_dockerd(sb)

        print("\n=== docker compose up ===")
        vmtools.run(sb, "mkdir", "-p", "/tmp/app", quiet=True)
        sb.filesystem.write_text(COMPOSE_YML, "/tmp/app/docker-compose.yml")
        vmtools.run(sb, *C, "up", "-d")
        vmtools.run(sb, *C, "ps")

        print("\n=== inter-container DNS (client -> web) ===")
        inter = False
        for _ in range(60):
            _, logs, _ = vmtools.run(sb, *C, "logs", "client", quiet=True)
            if "INTER_CONTAINER_OK" in logs:
                inter = True
                break
            if sb.poll() is not None:
                raise RuntimeError("sandbox died during compose run")
            time.sleep(1)
        assert inter, "client never reached web by service name"
        print("  inter-container DNS: OK")

        print("\n=== published port localhost:8080 ===")
        rc, html, _ = vmtools.run(sb, "curl", "-sf", "http://localhost:8080",
                                  check=False, quiet=True)
        assert rc == 0 and "nginx" in html.lower(), "published port not serving nginx"
        print("  published port: OK")

        print("\n" + "=" * 60)
        print("ALL CHECKS PASSED: docker compose works in the Modal VM Sandbox")
        print("=" * 60)
    finally:
        sb.terminate()


if __name__ == "__main__":
    main()
