"""Example 03 -- multi-service docker compose: Node.js API + Postgres.

  db   postgres:16-alpine, with a pg_isready healthcheck
  api  node:20-alpine; installs `pg`, talks to `db` by service name, and on each
       GET inserts a row + returns the count. depends_on db (service_healthy),
       publishes :3000.

Proves: healthcheck-gated startup, inter-service DB connection, a published
port, and a real API<->Postgres round-trip (the counter increments and the
value read straight from psql matches the API).

Run:  python examples/03_node_postgres.py
"""
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import modal  # noqa: E402
import vmtools  # noqa: E402

SERVER_JS = r"""
const http = require('http');
const { Client } = require('pg');

const cfg = {
  host: process.env.PGHOST || 'db',
  user: process.env.POSTGRES_USER,
  password: process.env.POSTGRES_PASSWORD,
  database: process.env.POSTGRES_DB,
  port: 5432,
};

async function withClient(fn) {
  const c = new Client(cfg);
  await c.connect();
  try { return await fn(c); } finally { await c.end(); }
}

async function init() {
  for (let i = 0; i < 30; i++) {
    try {
      await withClient(c => c.query(
        'CREATE TABLE IF NOT EXISTS visits (id SERIAL PRIMARY KEY, ts TIMESTAMPTZ DEFAULT now())'));
      console.log('db reachable; table ensured');
      return;
    } catch (e) {
      console.log('waiting for db:', e.code || e.message);
      await new Promise(r => setTimeout(r, 1000));
    }
  }
  throw new Error('db never became ready');
}

const server = http.createServer(async (req, res) => {
  try {
    const visits = await withClient(async c => {
      await c.query('INSERT INTO visits DEFAULT VALUES');
      const r = await c.query('SELECT count(*)::int AS n FROM visits');
      return r.rows[0].n;
    });
    res.setHeader('content-type', 'application/json');
    res.end(JSON.stringify({ ok: true, service: 'node-api', db: 'postgres', visits }));
  } catch (e) {
    res.statusCode = 500;
    res.end(JSON.stringify({ ok: false, error: String(e) }));
  }
});

init()
  .then(() => server.listen(3000, () => console.log('api listening on :3000')))
  .catch(e => { console.error(e); process.exit(1); });
"""

PACKAGE_JSON = json.dumps(
    {"name": "vm-node-api", "version": "1.0.0", "private": True,
     "dependencies": {"pg": "^8.11.3"}},
    indent=2,
)

COMPOSE_YML = """services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: app
      POSTGRES_PASSWORD: secret
      POSTGRES_DB: app
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U app -d app"]
      interval: 2s
      timeout: 3s
      retries: 20
  api:
    image: node:20-alpine
    working_dir: /app
    volumes:
      - ./app:/app
    environment:
      PGHOST: db
      POSTGRES_USER: app
      POSTGRES_PASSWORD: secret
      POSTGRES_DB: app
    command: ["sh", "-c", "npm install --no-audit --no-fund --loglevel=error && node server.js"]
    depends_on:
      db:
        condition: service_healthy
    ports:
      - "3000:3000"
"""

STACK = "/tmp/stack"
C = ["docker", "compose", "-p", "nodedb", "-f", f"{STACK}/docker-compose.yml"]


def main():
    app = vmtools.get_app()
    with modal.enable_output():
        sb = vmtools.create_sandbox(app)
    print(f"sandbox: {sb.object_id}")
    try:
        vmtools.start_dockerd(sb)

        print("\n=== write stack + compose up ===")
        vmtools.run(sb, "mkdir", "-p", f"{STACK}/app", quiet=True)
        sb.filesystem.write_text(COMPOSE_YML, f"{STACK}/docker-compose.yml")
        sb.filesystem.write_text(SERVER_JS, f"{STACK}/app/server.js")
        sb.filesystem.write_text(PACKAGE_JSON, f"{STACK}/app/package.json")
        vmtools.run(sb, *C, "up", "-d")
        vmtools.run(sb, *C, "ps")

        print("\n=== wait for db healthy ===")
        healthy = False
        for _ in range(30):
            _, out, _ = vmtools.run(sb, "docker", "inspect", "-f",
                                    "{{.State.Health.Status}}", "nodedb-db-1",
                                    check=False, quiet=True)
            if out.strip() == "healthy":
                healthy = True
                break
            if sb.poll() is not None:
                raise RuntimeError("sandbox died while waiting for db")
            time.sleep(2)
        assert healthy, "postgres never reported healthy"
        print("  db health: healthy")

        print("\n=== hit the API twice; expect the DB-backed counter to increment ===")
        first = None
        for _ in range(90):
            rc, body, _ = vmtools.run(sb, "curl", "-sf", "http://localhost:3000",
                                      check=False, quiet=True)
            if rc == 0 and body.strip():
                try:
                    if json.loads(body).get("ok") is True:
                        first = body
                        break
                except json.JSONDecodeError:
                    pass
            if sb.poll() is not None:
                raise RuntimeError("sandbox died while waiting for api")
            time.sleep(2)
        assert first, "api never answered on :3000"
        v1 = json.loads(first)["visits"]
        print(f"  GET / -> {first.strip()}")

        _, second, _ = vmtools.run(sb, "curl", "-sf", "http://localhost:3000",
                                   check=False, quiet=True)
        v2 = json.loads(second)["visits"]
        print(f"  GET / -> {second.strip()}")
        assert v2 == v1 + 1, f"counter did not increment ({v1} -> {v2})"

        print("\n=== cross-check the count straight from Postgres (psql) ===")
        _, n, _ = vmtools.run(sb, *C, "exec", "-T", "db",
                              "psql", "-U", "app", "-d", "app", "-tAc",
                              "select count(*) from visits", check=False, quiet=True)
        db_count = int(n.strip())
        print(f"  postgres visits count = {db_count} (api last reported {v2})")
        assert db_count == v2, "psql count disagrees with the API"

        print("\n" + "=" * 62)
        print("ALL CHECKS PASSED: Node.js API + Postgres compose stack works in VM")
        print("=" * 62)
    finally:
        sb.terminate()


if __name__ == "__main__":
    main()
