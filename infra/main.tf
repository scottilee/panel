terraform {
  required_version = ">= 1.6"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# APIs this stack needs. Enabled here so a fresh project works from
# `terraform apply` alone; disable_on_destroy = false so tearing the stack
# down doesn't disable APIs other things in the project might be using.
resource "google_project_service" "services" {
  for_each = toset([
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "secretmanager.googleapis.com",
  ])
  service            = each.value
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "panel" {
  location      = var.region
  repository_id = var.service_name
  format        = "DOCKER"
  description   = "Container images for Panel"

  depends_on = [google_project_service.services]
}

# The Token Factory key goes in Secret Manager rather than a plain env var
# so it never lands in the service's revision config, where it would be
# readable by anyone with Cloud Run viewer access and visible in the
# console's revision details.
#
# It DOES land in Terraform state: Terraform records every attribute of
# every resource it manages, and `sensitive = true` only suppresses CLI output
# -- so secret_data below is stored verbatim in infra/terraform.tfstate.
# That file is gitignored, but it is a plaintext secret on disk and should
# be treated as one (back it up like a key, not like config).
#
# To keep it out of state entirely, create the version out of band and
# stop managing it here:
#
#   printf '%s' "$TF_API_KEY" | gcloud secrets versions add panel-tf-api-key --data-file=-
#
# then delete the google_secret_manager_secret_version resource below and
# point the service's secret_key_ref at version "latest". Left as-is
# because one-command setup and teardown was the explicit goal, and the
# tradeoff is worth stating rather than hiding.
resource "google_secret_manager_secret" "tf_api_key" {
  secret_id = "${var.service_name}-tf-api-key"
  replication {
    auto {}
  }
  depends_on = [google_project_service.services]
}

resource "google_secret_manager_secret_version" "tf_api_key" {
  secret      = google_secret_manager_secret.tf_api_key.id
  secret_data = var.tf_api_key
}

# A dedicated identity for the service, rather than the Compute Engine
# default service account.
#
# The default SA was the original choice and it is a trap on a fresh
# project: PROJECT_NUMBER-compute@developer.gserviceaccount.com only
# exists once the Compute Engine API has been enabled, which this stack
# never enables. The IAM binding then attaches to a principal that is not
# there, the revision comes up without TF_API_KEY, and every run fails on
# an auth error -- a miserable thing to diagnose, and it would only ever
# have shown up on the very first real deploy.
#
# Explicit is also least-privilege: this account holds exactly one
# permission, read this one secret. The default compute SA is Editor on
# the whole project in most setups.
resource "google_service_account" "runtime" {
  account_id   = "${var.service_name}-runtime"
  display_name = "Panel Cloud Run runtime"
  depends_on   = [google_project_service.services]
}

resource "google_secret_manager_secret_iam_member" "runtime_access" {
  secret_id = google_secret_manager_secret.tf_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime.email}"
}

# Optional Tavily key for URL input. Without it the app fetches pages
# itself (requests + readability); with it, Tavily Extract is tried first
# and the direct fetch is the fallback. Same Secret Manager handling, and
# the same Terraform-state caveat, as the Token Factory key above.
resource "google_secret_manager_secret" "tavily_api_key" {
  count     = var.tavily_api_key == "" ? 0 : 1
  secret_id = "${var.service_name}-tavily-api-key"
  replication {
    auto {}
  }
  depends_on = [google_project_service.services]
}

resource "google_secret_manager_secret_version" "tavily_api_key" {
  count       = var.tavily_api_key == "" ? 0 : 1
  secret      = google_secret_manager_secret.tavily_api_key[0].id
  secret_data = var.tavily_api_key
}

resource "google_secret_manager_secret_iam_member" "runtime_tavily_access" {
  count     = var.tavily_api_key == "" ? 0 : 1
  secret_id = google_secret_manager_secret.tavily_api_key[0].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_cloud_run_v2_service" "panel" {
  name     = var.service_name
  location = var.region

  # Traffic goes to the latest revision; no gradual rollout to manage for
  # a single-instance demo service.
  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_ALL"

  template {
    # The identity above, not the project default -- see the comment on
    # google_service_account.runtime.
    service_account = google_service_account.runtime.email

    scaling {
      # BOTH limits are load-bearing, not tuning knobs.
      #
      # max = 1: run state (the RUNS dict and its SSE subscriber queues)
      # lives in process memory. A second instance would answer requests
      # that know nothing about a run started on the first, so an SSE
      # reconnect would silently never deliver its events.
      #
      # min: 1 keeps the service warm so a judge's first click isn't
      # waiting on a cold start (~$10-15/month). 0 scales to zero and is
      # effectively free, at the cost of a slow first request. Set via
      # var.min_instances.
      min_instance_count = var.min_instances
      max_instance_count = 1
    }

    containers {
      image = var.image

      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
        # Without this, CPU is throttled outside request handling, which
        # would stall the background task that drives the fan-out and
        # synthesis between SSE writes.
        cpu_idle = false
      }

      env {
        name  = "RUNS_DIR"
        value = "/tmp/runs"
      }

      env {
        name = "TF_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.tf_api_key.secret_id
            version = "latest"
          }
        }
      }

      dynamic "env" {
        for_each = google_secret_manager_secret.tavily_api_key
        content {
          name = "TAVILY_API_KEY"
          value_source {
            secret_key_ref {
              secret  = env.value.secret_id
              version = "latest"
            }
          }
        }
      }

      env {
        name  = "DAILY_PERSONA_BUDGET"
        value = tostring(var.daily_persona_budget)
      }

      env {
        name  = "MAX_CONCURRENCY"
        value = tostring(var.max_concurrency)
      }

      # Spend limits. This service is invokable by allUsers (judges need a
      # link, not credentials) and every run spends real Token Factory
      # credits, so these bound what one caller can do. Set here rather
      # than left to the code defaults so the ceiling is visible in the
      # infrastructure, where someone deciding how much to risk will look.
      env {
        name  = "MAX_PERSONA_COUNT"
        value = tostring(var.max_persona_count)
      }

      env {
        name  = "MAX_CONCURRENT_RUNS"
        value = tostring(var.max_concurrent_runs)
      }

      env {
        name  = "RATE_LIMIT_RUNS"
        value = tostring(var.rate_limit_runs)
      }

      # Rate limits are keyed per client. Every request reaches the
      # container from Google's front end, so the socket peer is the same
      # for everyone; Cloud Run appends the real client address as the
      # last X-Forwarded-For entry, which is the one hop to trust. Check
      # the "client=" field of the "run ... started" log line after a
      # deploy: it should show visitors' public IPs, not one 169.254.x.x.
      env {
        name  = "TRUSTED_PROXY_HOPS"
        value = "1"
      }

      ports {
        container_port = 8080
      }
    }

    # One long-lived SSE connection per run, held open for the whole run:
    # ~40-90s at the default 100 personas (100 Nano calls, then one Ultra
    # call), and roughly 8 minutes at the 1,000-persona maximum (1,000 Nano
    # calls, then 100 Super condensing calls in two waves of 50, then
    # Ultra). The default 5-minute request timeout would cut the large
    # end off mid-stream.
    #
    # 1800s rather than 900s: 900 left only ~4 minutes of headroom over a
    # full-size run, and the digest stage's per-call latency is the part
    # of that estimate with the least measurement behind it. A timeout
    # costs nothing until it fires, and when it fires here it discards a
    # run whose every model call has already been paid for.
    timeout = "1800s"
  }

  depends_on = [
    google_project_service.services,
    google_secret_manager_secret_version.tf_api_key,
    google_secret_manager_secret_version.tavily_api_key,
    google_secret_manager_secret_iam_member.runtime_tavily_access,
  ]
}

# Public: judges open the URL without a Google account.
resource "google_cloud_run_v2_service_iam_member" "public" {
  name     = google_cloud_run_v2_service.panel.name
  location = google_cloud_run_v2_service.panel.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}
