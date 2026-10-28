// Replaces the first occurrence of currentText with proposedText, or
// returns null when it can't be found. Ultra's current_text sometimes
// differs from the artifact only in whitespace (a phrase spanning a line
// break), so an exact miss falls back to matching with every whitespace
// run treated as \s+. Returning null rather than the unedited text keeps
// a failed match from rerunning the same copy and reporting ~0 flips.
export function applyEdit(artifactText, currentText, proposedText) {
  if (!currentText) return null
  const exact = artifactText.indexOf(currentText)
  if (exact !== -1) {
    return artifactText.slice(0, exact) + proposedText + artifactText.slice(exact + currentText.length)
  }
  const tokens = currentText.split(/\s+/).filter(Boolean)
  if (!tokens.length) return null
  const pattern = new RegExp(tokens.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('\\s+'))
  const match = pattern.exec(artifactText)
  if (!match) return null
  return artifactText.slice(0, match.index) + proposedText + artifactText.slice(match.index + match[0].length)
}
