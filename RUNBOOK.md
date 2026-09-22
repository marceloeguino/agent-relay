# Homework 3 — local runbook

Everything Claude could write and verify without Docker (the code, tests,
Dockerfile, compose.yaml, k8s manifests, CI workflow) is committed and
pushed. The steps below need a real Docker daemon, which only exists on
your Mac, not in the cloud sandbox — run these yourself in a normal
Terminal window, in this repo's folder.

Repo: https://github.com/marceloeguino/agent-relay

## Prerequisites (once)

```bash
brew install kind kubectl act
```

## Question 3 — Containerize

```bash
docker build -t agent-relay:local .
docker run -d --name agent-relay -p 8000:8000 agent-relay:local

curl -sS http://127.0.0.1:8000/health
open http://127.0.0.1:8000/            # dashboard

# repeat the Q2 flow against the container:
alice=$(curl -sS -X POST http://127.0.0.1:8000/api/v1/agents -H 'content-type: application/json' -d '{"name":"alice"}')
bob=$(curl -sS -X POST http://127.0.0.1:8000/api/v1/agents -H 'content-type: application/json' -d '{"name":"uppercase"}')
echo "$alice"; echo "$bob"
# ...same claim/complete calls as in the README, or just use the dashboard/curl by hand

docker rm -f agent-relay
```

**Q3 answer:** `-p` publishes a container's port to the host.

## Question 4 — Compose + PostgreSQL

```bash
docker compose up --build
```

In another terminal, run the integration test against the Compose stack
(it talks to the API over real HTTP on localhost:8000, same as Q2, so no
changes needed):

```bash
uv run pytest test_integration_flow.py -v
```

Then open http://127.0.0.1:8000/ and repeat the task flow. To confirm data
is really in Postgres (not SQLite):

```bash
docker compose exec postgres psql -U agent_relay -d agent_relay -c "select id, status from tasks;"
```

Stop with `docker compose down` (add `-v` to also drop the Postgres volume).

**Q4 answer:** `postgres` — that's the Compose service name in `compose.yaml`; Compose's embedded DNS resolves service names as hostnames on the Compose network.

## Question 5 — Deploy to kind

```bash
kind create cluster --name agent-relay --config k8s/kind-config.yaml

docker build -t agent-relay:local .
kind load docker-image agent-relay:local --name agent-relay

kubectl apply -f k8s/postgres-secret.yaml
kubectl apply -f k8s/postgres-pvc.yaml
kubectl apply -f k8s/postgres-deployment.yaml
kubectl apply -f k8s/api-deployment.yaml

kubectl rollout status deployment/postgres
kubectl rollout status deployment/agent-relay-api
kubectl get pods

kubectl port-forward svc/agent-relay-api 8000:8000
```

Open http://127.0.0.1:8000/ in another terminal tab and repeat the Q2 task
flow against it.

**Q5 answer:** `Deployment` — it's the resource that keeps the requested
replica count running and manages rolling updates (what `Service`,
`ConfigMap`, and `Secret` don't do).

## Question 6 — CI with act

`act` runs GitHub Actions workflows in Docker containers on your machine.
For the `build-and-deploy` job to reach *your* Docker daemon and *your*
kind cluster (not an isolated one inside act's own container), it needs
your Docker socket and a kubeconfig that points at a host your Mac and the
act container can both resolve — `127.0.0.1` inside the act container is
the container itself, not your Mac, so the kubeconfig needs
`host.docker.internal` instead.

```bash
# One-time: a kubeconfig act's container can actually reach
kind get kubeconfig --name agent-relay \
  | sed 's/127.0.0.1/host.docker.internal/' > /tmp/kind-kubeconfig-for-act.yaml

act push \
  --container-options "-v /var/run/docker.sock:/var/run/docker.sock -v /tmp/kind-kubeconfig-for-act.yaml:/root/.kube/config" \
  --container-architecture linux/amd64
```

If `act` can't find `kind`/`kubectl` inside its runner image, use a fuller
image (`-P ubuntu-latest=catthehacker/ubuntu:full-latest`) or install them
in the job via a setup step — the default `act` runner image is minimal.

Watch the run: the `test` job should pass (starter tests + the Postgres
integration test), then `build-and-deploy` builds a uniquely-tagged image,
loads it into kind, and waits for the rollout.

Then, to prove the pipeline actually gates on tests:

1. Edit `dashboard.html`, change the heading to `Agent Relay v2`.
2. Commit.
3. Re-run `act push` with the same command above.
4. Confirm tests still pass and `kubectl port-forward svc/agent-relay-api 8000:8000` now shows the v2 heading.

**Q6 answer:** *Keep the existing version running and stop the
deployment* — that's exactly what the workflow does: `build-and-deploy`
only runs `needs: test`, so a failing `test` job never reaches the build/
load/deploy steps, and whatever was already running in the cluster is
left untouched.
