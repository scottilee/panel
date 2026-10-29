variable "project_id" {
  description = "Google Cloud project ID to deploy into."
  type        = string
}

variable "region" {
  description = "Cloud Run region."
  type        = string
  default     = "us-central1"
}

variable "service_name" {
  description = "Cloud Run service name, also used for the Artifact Registry repo."
  type        = string
  default     = "panel"
}

variable "image" {
  description = <<-EOT
    Full image URI to deploy, e.g.
    us-central1-docker.pkg.dev/PROJECT/panel/panel:latest
    Build and push it first (`make push`); Terraform does not build images.
  EOT
  type        = string
}

variable "tf_api_key" {
  description = "Nebius Token Factory API key. Stored in Secret Manager, not in the service config."
  type        = string
  sensitive   = true
}

variable "tavily_api_key" {
  description = "Optional Tavily key: URL extraction and the market comparison. Empty = pages are fetched directly and the market comparison is hidden."
  type        = string
  default     = ""
  sensitive   = true
}

variable "min_instances" {
  description = <<-EOT
    0 scales to zero (effectively free, but a cold start on the first
    request). 1 keeps one instance warm so a judge's first click is
    instant; with cpu_idle = false that is ~$45-55/month at list prices. max_instance_count is pinned at 1 regardless -- run
    state is in process memory.
  EOT
  type        = number
  default     = 1
}

variable "max_concurrency" {
  description = "In-flight Nano calls per run. 50 measured as the highest level with zero 429s."
  type        = number
  default     = 50
}

# --- Spend limits on a public, unauthenticated service --------------------
#
# The pool is 1,000 personas and one request can ask for all of them, so a
# single POST is up to 1,000 Nano calls plus ~100 Super digests plus one
# Ultra call. These limits are what stop a loop against the demo URL from
# draining the hackathon credits before judging ends on Dec 15.

variable "max_persona_count" {
  description = "Largest panel one request may ask for. Also capped by the pool size."
  type        = number
  default     = 1000
}

variable "max_concurrent_runs" {
  description = "Runs allowed in flight at once, service-wide. Further requests get a 429."
  type        = number
  default     = 3
}

variable "rate_limit_runs" {
  description = "Runs one client IP may start per hour."
  type        = number
  default     = 20
}

variable "daily_persona_budget" {
  description = "Persona reactions all live runs and market comparisons together may use per rolling 24h. 10,000 is a hundred default-size runs."
  type        = number
  default     = 10000
}

variable "daily_client_persona_budget" {
  description = "Persona reactions one client may use per rolling 24h, so one caller can't spend the whole daily budget."
  type        = number
  default     = 2000
}

# --- Magic-link sign-in (optional) -----------------------------------------
#
# Set all three to gate live runs behind an emailed sign-in link; leave them
# empty (the default) and the app stays fully open, which is how it shipped
# before auth existed. Half-configuring is treated as off, deliberately:
# with a project id but no web API key the frontend could not sign anyone
# in, so gating would lock out every live run.
#
# These are the public Firebase web config values, readable by anyone who
# opens the page. They are not credentials and do not belong in Secret
# Manager.

variable "firebase_project_id" {
  description = "Firebase/Identity Platform project id. Empty disables the sign-in gate."
  type        = string
  default     = ""
}

variable "firebase_api_key" {
  description = "Firebase Web API key (public). Empty disables the sign-in gate."
  type        = string
  default     = ""
}

variable "firebase_auth_domain" {
  description = "Firebase auth domain, usually PROJECT.firebaseapp.com. Empty disables the gate."
  type        = string
  default     = ""
}
