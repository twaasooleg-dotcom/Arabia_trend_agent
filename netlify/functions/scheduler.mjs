import { getStore } from "@netlify/blobs";

export default async () => {
  const executedAt = new Date();

  const cairoTime = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Africa/Cairo",
    dateStyle: "full",
    timeStyle: "long"
  }).format(executedAt);

  const store = getStore("arabia-trend-agent", {
    region: "eu-central-1"
  });

  const status = {
    ok: true,
    executedAtUtc: executedAt.toISOString(),
    cairoTime,
    message: "Scheduler test completed. Publishing is disabled."
  };

  await store.set(
    "health/scheduler-last-run.json",
    JSON.stringify(status, null, 2),
    {
      metadata: {
        type: "scheduler-health-check"
      }
    }
  );

  console.log("Scheduler health check:", status);
};

export const config = {
  schedule: "0 7,8,17,18 * * *"
};
