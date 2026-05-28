.PHONY: help up down logs ps build run-api run-compute run-ingest \
        demo-up demo-down synth-state ws-stress backfill-plan jetstream-setup smoke-e2e \
        test test-v bench lint fmt vet tidy check \
        migrate-up migrate-down migrate-create clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "%-20s %s\n", $$1, $$2}'

# ─── Local stack ───
up: ## Start docker stack (postgres, redis, nats)
	docker compose -f deploy/docker-compose.yml up -d

down: ## Stop docker stack
	docker compose -f deploy/docker-compose.yml down

logs: ## Tail docker stack logs
	docker compose -f deploy/docker-compose.yml logs -f --tail=100

ps: ## Show docker stack status
	docker compose -f deploy/docker-compose.yml ps

# ─── Build & run ───
build: ## Build all binaries to bin/
	@mkdir -p bin
	go build -o bin/api     ./cmd/api
	go build -o bin/ingest  ./cmd/ingest
	go build -o bin/compute ./cmd/compute
	go build -o bin/replay  ./cmd/replay
	@echo "Built bin/api bin/ingest bin/compute bin/replay"

run-api: ## Run api binary directly
	go run ./cmd/api

run-compute: ## Run compute binary directly
	go run ./cmd/compute

run-ingest: ## Run ingest binary directly (needs DATABENTO_API_KEY)
	go run ./cmd/ingest

replay-dbn: ## Replay a day of DBN files. Usage: make replay-dbn DIR=data/databento/2026-02-02
	go run ./cmd/replay_dbn -dir $(DIR)

# ─── Demo / dev helpers ───
demo-up: ## Start demo stack (api + synth_state, no databento)
	docker compose -f deploy/docker-compose.yml --profile demo up -d

demo-down: ## Stop demo stack
	docker compose -f deploy/docker-compose.yml --profile demo down

synth-state: ## Run synthetic state publisher locally
	go run ./scripts/synth_state

ws-stress: ## Stress /ws/live: make ws-stress N=1000 D=60s
	go run ./scripts/ws_stress -clients $(or $(N),100) -duration $(or $(D),30s)

backfill-plan: ## Plan a backfill (dry run): make backfill-plan FROM=2026-05-01 TO=2026-05-02
	go run ./scripts/backfill -from $(FROM) -to $(TO)

jetstream-setup: ## Idempotently create / update TICKS, STATE, FLOW streams
	go run ./scripts/jetstream_setup

smoke-e2e: ## End-to-end smoke against demo stack: make smoke-e2e [URL=...]
	go run ./scripts/smoke/e2e -url $(or $(URL),http://localhost:8080)

# ─── Code quality ───
test: ## Run all tests
	go test -race -timeout 60s ./...

test-v: ## Run all tests verbose
	go test -race -v -timeout 60s ./...

bench: ## Run benchmarks
	go test -bench=. -benchmem -run=^$$ ./...

lint: ## Run golangci-lint
	golangci-lint run ./...

fmt: ## Format all code
	go fmt ./...

vet: ## Run go vet
	go vet ./...

tidy: ## Tidy go.mod
	go mod tidy

check: fmt vet lint test ## Full quality pass

# ─── Migrations ───
migrate-up: ## Apply pending DB migrations
	@migrate -path scripts/migrations -database "postgres://$$POSTGRES_USER:$$POSTGRES_PASSWORD@$$POSTGRES_HOST:$$POSTGRES_PORT/$$POSTGRES_DB?sslmode=disable" up

migrate-down: ## Rollback last DB migration
	@migrate -path scripts/migrations -database "postgres://$$POSTGRES_USER:$$POSTGRES_PASSWORD@$$POSTGRES_HOST:$$POSTGRES_PORT/$$POSTGRES_DB?sslmode=disable" down 1

migrate-create: ## Create new migration: make migrate-create NAME=add_something
	@migrate create -ext sql -dir scripts/migrations -seq $(NAME)

# ─── Cleanup ───
clean: ## Remove build artifacts
	rm -rf bin/ coverage.out coverage.html
