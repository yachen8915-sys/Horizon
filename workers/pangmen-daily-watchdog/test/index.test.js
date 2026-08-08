import test from "node:test";
import assert from "node:assert/strict";

import { decideDispatch } from "../src/index.js";

test("primary time dispatches the daily workflow", () => {
  const decision = decideDispatch({
    isPrimary: true,
    workflowRuns: [],
  });

  assert.deepEqual(decision, { dispatch: true, reason: "primary" });
});

test("fallback time does not dispatch while a run is active", () => {
  const decision = decideDispatch({
    isPrimary: false,
    workflowRuns: [{ status: "in_progress", conclusion: null }],
  });

  assert.deepEqual(decision, { dispatch: false, reason: "active_run" });
});

test("fallback time retries only after the previous run failed", () => {
  const decision = decideDispatch({
    isPrimary: false,
    workflowRuns: [{ status: "completed", conclusion: "failure" }],
  });

  assert.deepEqual(decision, { dispatch: true, reason: "retry_after_failure" });
});

test("fallback time does not dispatch after a successful run", () => {
  const decision = decideDispatch({
    isPrimary: false,
    workflowRuns: [{ status: "completed", conclusion: "success" }],
  });

  assert.deepEqual(decision, { dispatch: false, reason: "successful_run" });
});
