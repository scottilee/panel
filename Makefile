# Deployment helpers. Terraform owns the infrastructure; image building is
# a separate explicit step, because Terraform is bad at building images and
# you want to rebuild far more often than you change infra.
#
# First time:   make bootstrap && make push && make deploy
# After a code change:  make push && make deploy
# When judging is over: make destroy

PROJECT ?= $(shell cd infra && grep -E '^\s*project_id' terraform.tfvars 2>/dev/null | sed 's/.*= *"\(.*\)"/\1/')
REGION  ?= us-central1
SERVICE ?= panel
IMAGE   = $(REGION)-docker.pkg.dev/$(PROJECT)/$(SERVICE)/$(SERVICE):latest

.PHONY: test build run-local bootstrap push deploy url destroy

## Run the test suite (no API calls, no credits)
test:
	python3 -m pytest tests/ -q
	@node scripts/check_precheck.mjs
	@node scripts/check_mock.mjs
	@node scripts/check_dev_proxy.mjs
	@node scripts/check_edit.mjs

## Build the container locally
build:
	docker build -t $(SERVICE):local .

## Run the container locally on :8081 (reads TF_API_KEY from .env)
## --env-file, not -e TF_API_KEY=$(TF_API_KEY), and @-prefixed: make echoes
## every recipe line it runs, so the old form printed the real key into
## terminal scrollback and any CI log, and left it visible in `ps` for the
## container's lifetime. Passing the file means the value never appears on
## a command line.
run-local: build
	@test -f .env || (echo ".env not found -- it must contain TF_API_KEY" && exit 1)
	@docker run --rm -p 8081:8080 -e PORT=8080 --env-file .env $(SERVICE):local

## One-time: create the Artifact Registry repo and enable APIs, so `push` has
## somewhere to push to. Safe to re-run.
bootstrap:
	cd infra && terraform init && terraform apply \
		-target=google_project_service.services \
		-target=google_artifact_registry_repository.panel

## Build the image in Cloud Build and push it to Artifact Registry.
## Uses Cloud Build rather than a local docker push so it works regardless
## of your machine's architecture (Cloud Run needs linux/amd64).
push:
	@test -n "$(PROJECT)" || (echo "PROJECT not set and not found in infra/terraform.tfvars" && exit 1)
	gcloud builds submit --project $(PROJECT) --tag $(IMAGE) .
	@echo "pushed: $(IMAGE)"

## Apply the full stack. Prints the public URL when done.
deploy:
	cd infra && terraform apply
	@echo
	@echo "Live at: $$(cd infra && terraform output -raw url)"

## Print the live URL
url:
	@cd infra && terraform output -raw url

## Tear everything down (after judging). Leaves the APIs enabled.
destroy:
	cd infra && terraform destroy
