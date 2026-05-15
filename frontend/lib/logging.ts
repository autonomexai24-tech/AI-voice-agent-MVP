export type FrontendLogEvent =
  | "frontend_runtime_ready"
  | "frontend_api_connected"
  | "frontend_api_failed"
  | "frontend_settings_saved"
  | "frontend_env_loaded"
  | "transcript_loaded"
  | "bookings_loaded"
  | "calls_loaded";

let runtimeReadyLogged = false;
let envLoadedLogged = false;
let apiConnectedLogged = false;

export function frontendLog(
  event: FrontendLogEvent,
  fields: Record<string, unknown> = {}
): void {
  const payload = {
    ts: new Date().toISOString(),
    event,
    source: "frontend",
    ...fields
  };
  console.info(JSON.stringify(payload));
}

export function logFrontendRuntimeReady(): void {
  if (runtimeReadyLogged) {
    return;
  }
  runtimeReadyLogged = true;
  frontendLog("frontend_runtime_ready");
}

export function logFrontendEnvLoaded(fields: Record<string, unknown>): void {
  if (envLoadedLogged) {
    return;
  }
  envLoadedLogged = true;
  frontendLog("frontend_env_loaded", fields);
}

export function logFrontendApiConnected(fields: Record<string, unknown>): void {
  if (apiConnectedLogged) {
    return;
  }
  apiConnectedLogged = true;
  frontendLog("frontend_api_connected", fields);
}
