const GITHUB_API = "https://api.github.com";
const OWNER = "yachen8915-sys";
const REPOSITORY = "Horizon";
const WORKFLOW_FILE = "daily-summary.yml";
const PRIMARY_CRON = "15 1 * * *";
const MAX_DAILY_DISPATCHES = 2;

export function decideDispatch({ isPrimary, workflowRuns }) {
  if (isPrimary) {
    return { dispatch: true, reason: "primary" };
  }

  if (workflowRuns.some((run) => run.status !== "completed")) {
    return { dispatch: false, reason: "active_run" };
  }
  if (workflowRuns.some((run) => run.conclusion === "success")) {
    return { dispatch: false, reason: "successful_run" };
  }
  if (workflowRuns.length >= MAX_DAILY_DISPATCHES) {
    return { dispatch: false, reason: "retry_limit_reached" };
  }
  return {
    dispatch: true,
    reason: workflowRuns.length ? "retry_after_failure" : "missing_primary",
  };
}

function githubHeaders(token) {
  return {
    Accept: "application/vnd.github+json",
    Authorization: `Bearer ${token}`,
    "User-Agent": "pangmen-daily-watchdog",
    "X-GitHub-Api-Version": "2022-11-28",
  };
}

function primaryStart(date) {
  const start = new Date(date);
  start.setUTCHours(1, 15, 0, 0);
  return start;
}

async function listTodayRuns(token, now) {
  const response = await fetch(
    `${GITHUB_API}/repos/${OWNER}/${REPOSITORY}/actions/workflows/${WORKFLOW_FILE}/runs?event=workflow_dispatch&per_page=30`,
    { headers: githubHeaders(token) },
  );
  if (!response.ok) {
    throw new Error(`GitHub workflow query failed with HTTP ${response.status}`);
  }

  const payload = await response.json();
  const since = primaryStart(now).getTime();
  return (payload.workflow_runs ?? []).filter(
    (run) => new Date(run.created_at).getTime() >= since,
  );
}

async function dispatchWorkflow(token) {
  const response = await fetch(
    `${GITHUB_API}/repos/${OWNER}/${REPOSITORY}/actions/workflows/${WORKFLOW_FILE}/dispatches`,
    {
      method: "POST",
      headers: { ...githubHeaders(token), "Content-Type": "application/json" },
      body: JSON.stringify({ ref: "main", inputs: { run_mode: "full" } }),
    },
  );
  if (!response.ok && response.status !== 204) {
    throw new Error(`GitHub workflow dispatch failed with HTTP ${response.status}`);
  }
}

export async function runScheduled(cron, token, now = new Date()) {
  if (!token) {
    throw new Error("GITHUB_DISPATCH_TOKEN is not configured");
  }

  const workflowRuns = await listTodayRuns(token, now);
  const decision = decideDispatch({
    isPrimary: cron === PRIMARY_CRON,
    workflowRuns,
  });
  if (decision.dispatch) {
    await dispatchWorkflow(token);
  }
  return decision;
}

export default {
  async scheduled(controller, env, ctx) {
    ctx.waitUntil(runScheduled(controller.cron, env.GITHUB_DISPATCH_TOKEN));
  },
};
