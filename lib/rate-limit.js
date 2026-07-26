const attempts = new Map();
const WINDOW_MS = 15 * 60 * 1000;
const MAX_FAILURES = 5;

function currentRecord(key, now = Date.now()) {
  const record = attempts.get(key);
  if (!record || record.resetAt <= now) {
    const fresh = { failures: 0, resetAt: now + WINDOW_MS };
    attempts.set(key, fresh);
    return fresh;
  }
  return record;
}

export function rateLimitStatus(key, now = Date.now()) {
  const record = currentRecord(key, now);
  return {
    allowed: record.failures < MAX_FAILURES,
    retryAfter: Math.max(1, Math.ceil((record.resetAt - now) / 1000)),
  };
}

export function recordFailure(key, now = Date.now()) {
  const record = currentRecord(key, now);
  record.failures += 1;
  attempts.set(key, record);
  return rateLimitStatus(key, now);
}

export function clearFailures(key) {
  attempts.delete(key);
}
