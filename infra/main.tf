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
# so it never lands in Terraform state or the service's revision config.
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

data "google_project" "current" {}

# Cloud Run's default compute service account needs to read the secret.
resource "google_secret_manager_secret_iam_member" "runtime_access" {
  secret_id = google_secret_manager_secret.tf_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${data.google_project.current.number}-compute@developer.gserviceaccount.com"
}

resource "google_cloud_run_v2_service" "panel" {
  name     = var.service_name
  location = var.region

  # Traffic goes to the latest revision; no gradual rollout to manage for
  # a single-instance demo service.
  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_ALL"

  template {
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

      env {
        name  = "MAX_CONCURRENCY"
        value = tostring(var.max_concurrency)
      }

      ports {
        container_port = 8080
      }
    }

    # A full run is ~40-90s of streaming (100 Nano calls, then one Ultra
    # call) on a single long-lived SSE connection, so the default 5-minute
    # request timeout is cut close on a slow synthesis.
    timeout = "900s"
  }

  depends_on = [
    google_project_service.services,
    google_secret_manager_secret_version.tf_api_key,
  ]
}

# Public: judges open the URL without a Google account.
resource "google_cloud_run_v2_service_iam_member" "public" {
  name     = google_cloud_run_v2_service.panel.name
  location = google_cloud_run_v2_service.panel.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}
