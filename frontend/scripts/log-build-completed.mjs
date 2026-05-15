const payload = {
  ts: new Date().toISOString(),
  event: "frontend_build_completed",
  source: "frontend"
};

console.info(JSON.stringify(payload));
