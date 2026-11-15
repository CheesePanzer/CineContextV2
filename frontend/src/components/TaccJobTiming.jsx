import React, {useEffect, useState} from 'react';

function formatDuration(seconds) {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = seconds % 60;
  return [hours > 0 && `${hours}h`, minutes > 0 && `${minutes}m`, `${remainder}s`].filter(Boolean).join(' ');
}

export default function TaccJobTiming({health}) {
  const timing = health?.tacc_job;
  const {receivedAt} = health || {};
  const [now, setNow] = useState(Date.now);
  const queued = timing?.queued_seconds;
  const remaining = timing?.remaining_seconds;
  const hasTiming = Number.isFinite(queued) || Number.isFinite(remaining);

  useEffect(() => {
    if (!hasTiming) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [hasTiming]);

  if (!hasTiming) return null;
  const sinceUpdate = Math.max(0, Math.floor((now - receivedAt) / 1000));
  if (Number.isFinite(queued)) return <span>In queue for {formatDuration(queued + sinceUpdate)}</span>;
  return <span>Job time left {formatDuration(Math.max(0, remaining - sinceUpdate))}</span>;
}
