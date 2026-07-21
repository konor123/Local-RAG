# OSL AI Assistant v1.5.1

## Ollama Startup Reliability

- Uses the configured Ollama server URL consistently for readiness checks and model requests.
- Adds bounded startup retries and handles delayed external Ollama server handoff.
- Shows actionable startup errors for invalid server addresses, missing executables, launch failures, early process exits, incorrect endpoints, and readiness timeouts.
- Captures a sanitized diagnostic tail for startup failures without exposing user prompts or document content.

## Verification

- Added isolated Ollama runtime lifecycle tests covering startup ownership, delayed handoff, failure categories, and diagnostic redaction.
