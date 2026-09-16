.PHONY: demo test up down logs clean

## Full cutover demo: up -> seed -> replicate -> load + cutover -> assert zero drops
demo:
	bash scripts/run_demo.sh

## Runs the tenant isolation and RBAC test suites against a running stack.
## Tests exercise app-blue/db-blue only: blue and green run the identical
## image against structurally identical, RLS-enforced databases (see
## db/init/01-schema.sql), so the isolation/RBAC guarantee is the same on
## either side by construction -- the demo target is what proves the two
## sides actually stay in sync via replication.
test: up
	bash scripts/wait_healthy.sh
	docker compose exec -T app-blue python -m app.seed --db-url postgresql://postgres:postgres_pw@db-blue:5432/appdb --with-demo-data
	docker compose exec -T app-blue python -m pytest tests -v

up:
	docker compose up -d --build

down:
	docker compose down -v

logs:
	docker compose logs -f

clean: down
	rm -rf demo-output
