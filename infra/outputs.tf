output "url" {
  description = "Public URL of the deployed service -- this is the live demo link."
  value       = google_cloud_run_v2_service.panel.uri
}

output "image_repo" {
  description = "Artifact Registry path to push images to (see make push)."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.panel.repository_id}"
}
