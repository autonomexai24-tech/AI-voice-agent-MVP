import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));

function read(path) {
  return readFileSync(join(root, path), "utf8");
}

const tests = [];

function test(name, fn) {
  tests.push({ name, fn });
}

test("dashboard rendering uses backend data and operational metric cards", () => {
  const page = read("app/dashboard/page.tsx");
  const dashboardService = read("services/dashboard.ts");

  assert.match(page, /getDashboardData/);
  assert.match(page, /StatCard/);
  assert.match(dashboardService, /Total calls/);
  assert.match(dashboardService, /Answered calls/);
  assert.match(dashboardService, /Missed calls/);
  assert.match(dashboardService, /Successful bookings/);
  assert.match(dashboardService, /Avg duration/);
});

test("calls page loads calls through the typed API service", () => {
  const page = read("app/calls/page.tsx");
  const table = read("components/calls/CallTable.tsx");

  assert.match(page, /listCalls\(100\)/);
  assert.match(page, /CallTable/);
  assert.match(table, /caller_phone/);
  assert.match(table, /booking_outcome/);
  assert.match(table, /href=\{`\/calls\/\$\{call\.call_id\}`\}/);
});

test("bookings page loads bookings through the typed API service", () => {
  const page = read("app/bookings/page.tsx");
  const table = read("components/bookings/BookingTable.tsx");

  assert.match(page, /listBookings\(100\)/);
  assert.match(page, /BookingTable/);
  assert.match(table, /customer_name/);
  assert.match(table, /service_type/);
  assert.match(table, /confirmation_status/);
});

test("call detail renders transcript timeline in timestamp order", () => {
  const page = read("app/calls/[id]/page.tsx");
  const timeline = read("components/calls/TranscriptTimeline.tsx");
  const format = read("lib/format.ts");

  assert.match(page, /getCallTranscripts/);
  assert.match(page, /TranscriptTimeline/);
  assert.match(timeline, /compareDateTime\(a\.timestamp, b\.timestamp\)/);
  assert.match(format, /function toTimestamp/);
  assert.match(timeline, /speaker/);
  assert.match(timeline, /whitespace-pre-wrap/);
});

test("settings page persists editable business settings", () => {
  const page = read("app/settings/page.tsx");
  const action = read("app/settings/actions.ts");
  const form = read("components/settings/SettingsForm.tsx");

  assert.match(page, /saveBusinessSettings/);
  assert.match(action, /updateBusinessSettings/);
  assert.match(action, /services: textAreaList/);
  assert.match(form, /default_language/);
  assert.match(form, /Background ambience/);
  assert.match(form, /disabled/);
});

test("typed API integration and failure logging are centralized", () => {
  const api = read("services/api.ts");
  const types = read("types/api.ts");
  const logging = read("lib/logging.ts");

  assert.match(api, /listCalls/);
  assert.match(api, /listBookings/);
  assert.match(api, /getCallTranscripts/);
  assert.match(api, /getBusinessSettings/);
  assert.match(api, /updateBusinessSettings/);
  assert.match(api, /API_TIMEOUT_MS/);
  assert.match(api, /api_malformed_response/);
  assert.match(api, /frontend_api_failed/);
  assert.match(logging, /frontend_runtime_ready/);
  assert.match(logging, /frontend_api_connected/);
  assert.match(logging, /frontend_settings_saved/);
  assert.match(types, /interface CallRecord/);
  assert.match(types, /interface BookingRecord/);
  assert.match(types, /interface TranscriptEntry/);
  assert.match(types, /interface BusinessSettings/);
});

test("frontend toolchain and local env are reproducible", () => {
  const packageJson = JSON.parse(read("package.json"));
  const envExample = read(".env.example");
  const eslintConfig = read("eslint.config.mjs");
  const buildLogger = read("scripts/log-build-completed.mjs");

  for (const dependency of ["next", "react", "react-dom"]) {
    assert.ok(packageJson.dependencies[dependency], `${dependency} is installed`);
  }
  for (const dependency of [
    "tailwindcss",
    "typescript",
    "eslint",
    "postcss",
    "autoprefixer"
  ]) {
    assert.ok(packageJson.devDependencies[dependency], `${dependency} is installed`);
  }
  assert.equal(packageJson.scripts.lint, "eslint .");
  assert.match(envExample, /NEXT_PUBLIC_API_BASE_URL=http:\/\/127\.0\.0\.1:8000/);
  assert.match(envExample, /FRONTEND_API_TIMEOUT_MS=8000/);
  assert.match(eslintConfig, /next\/core-web-vitals/);
  assert.match(buildLogger, /frontend_build_completed/);
});

test("local startup workflow covers the MVP stack", () => {
  const root = dirname(dirname(fileURLToPath(import.meta.url)));
  const repoRoot = dirname(root);
  const startScript = readFileSync(
    join(repoRoot, "scripts", "start-local-stack.ps1"),
    "utf8"
  );
  const checkScript = readFileSync(
    join(repoRoot, "scripts", "check-local-stack.ps1"),
    "utf8"
  );
  const dbInitScript = readFileSync(
    join(repoRoot, "scripts", "initialize_local_db.py"),
    "utf8"
  );

  assert.match(startScript, /uvicorn api\.main:app/);
  assert.match(startScript, /npm run dev/);
  assert.match(startScript, /python -m voice_agent\.worker dev/);
  assert.match(startScript, /initialize_local_db\.py/);
  assert.match(startScript, /DATABASE_URL/);
  assert.match(checkScript, /internal\/v1\/calls/);
  assert.match(checkScript, /internal\/v1\/bookings/);
  assert.match(checkScript, /internal\/v1\/settings\/business/);
  assert.match(dbInitScript, /initialize_schema/);
});

let passed = 0;
for (const entry of tests) {
  entry.fn();
  passed += 1;
  console.log(`ok ${passed} - ${entry.name}`);
}

console.log(`${passed} frontend foundation tests passed`);
