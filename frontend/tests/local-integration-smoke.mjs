import assert from "node:assert/strict";

const apiBaseUrl =
  process.env.API_BASE_URL ||
  process.env.NEXT_PUBLIC_API_BASE_URL ||
  "http://127.0.0.1:8000";
const frontendBaseUrl = process.env.FRONTEND_BASE_URL || "http://127.0.0.1:3000";

async function fetchJson(name, url, init) {
  const response = await fetch(url, {
    ...init,
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      ...init?.headers
    }
  });
  assert.equal(
    response.status,
    200,
    `${name} expected HTTP 200 but received ${response.status}: ${await response.text()}`
  );
  return response.json();
}

async function fetchPage(name, url) {
  const response = await fetch(url);
  assert.equal(
    response.status,
    200,
    `${name} expected HTTP 200 but received ${response.status}`
  );
  return response.text();
}

const dashboardHtml = await fetchPage(
  "frontend dashboard",
  `${frontendBaseUrl}/dashboard`
);
assert.match(dashboardHtml, /Dashboard|dashboard/i);

const calls = await fetchJson("calls API", `${apiBaseUrl}/internal/v1/calls?limit=5`);
assert.ok(Array.isArray(calls.items), "calls API returns an items array");

const bookings = await fetchJson(
  "bookings API",
  `${apiBaseUrl}/internal/v1/bookings?limit=5`
);
assert.ok(Array.isArray(bookings.items), "bookings API returns an items array");

const settings = await fetchJson(
  "settings persistence",
  `${apiBaseUrl}/internal/v1/settings/business`,
  {
    method: "PATCH",
    body: JSON.stringify({
      business_name: "Local Smoke Clinic",
      business_type: "clinic",
      services: ["Dental cleaning", "Braces treatment"],
      default_language: "kannada",
      receptionist_tone: "calm and concise",
      greeting_prompt: "Namaskara, how can I help you book an appointment?",
      refusal_policy: "Receptionist and booking questions only."
    })
  }
);
assert.equal(settings.business_name, "Local Smoke Clinic");
assert.deepEqual(settings.services, ["Dental cleaning", "Braces treatment"]);
assert.equal(settings.default_language, "kannada");

if (calls.items[0]?.call_id) {
  const transcripts = await fetchJson(
    "transcripts API",
    `${apiBaseUrl}/internal/v1/calls/${encodeURIComponent(
      calls.items[0].call_id
    )}/transcripts`
  );
  assert.ok(Array.isArray(transcripts.items), "transcripts API returns an items array");
}

console.log("local integration smoke passed");
