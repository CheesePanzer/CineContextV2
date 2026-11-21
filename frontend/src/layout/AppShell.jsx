import React, {useEffect, useState} from 'react';
import {Outlet, useLocation, useNavigate} from 'react-router-dom';
import {api} from '../api/client.js';
import {getResearchTitle} from '../app/researchTitle.js';
import PointerRipple from '../components/PointerRipple.jsx';
import TaccJobTiming from '../components/TaccJobTiming.jsx';
import ResearchSidebar from './ResearchSidebar.jsx';

const HEALTH_REFRESH_MS = 15000;

export default function AppShell() {
  const [health, setHealth] = useState(null);
  const [healthFailed, setHealthFailed] = useState(false);
  const [checkingHealth, setCheckingHealth] = useState(true);
  const [runs, setRuns] = useState([]);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [shellError, setShellError] = useState('');
  const [taccAction, setTaccAction] = useState(null);
  const submittingTacc = taccAction !== null;
  const [healthRefresh, setHealthRefresh] = useState(0);
  const location = useLocation();
  const navigate = useNavigate();
  const displayedHealth = taccAction === 'restart' && health ? {
    ...health, model_status: 'restarting',
    tacc_job: {...health.tacc_job, queued_seconds: null, remaining_seconds: null},
  } : health;

  async function refreshRuns() {
    try { setRuns((await api.listRuns()).runs); } catch { /* Page-level requests retain actionable errors. */ }
  }

  function refreshHealth() {
    setCheckingHealth(true);
    setHealthRefresh(value => value + 1);
  }

  useEffect(() => { refreshRuns(); }, []);

  useEffect(() => {
    let active = true;
    let timer;
    async function checkHealth() {
      setCheckingHealth(true);
      try {
        const result = await api.health();
        if (!active) return;
        setHealth({...result, receivedAt: Date.now()});
        setHealthFailed(false);
        if (result.model_mode === 'tacc') {
          const interval = result.tacc_job?.last_confirmed_status ? 7000 : HEALTH_REFRESH_MS;
          timer = setTimeout(checkHealth, interval);
        }
      } catch {
        if (active) {
          setHealthFailed(true);
          timer = setTimeout(checkHealth, HEALTH_REFRESH_MS);
        }
      } finally {
        if (active) setCheckingHealth(false);
      }
    }
    checkHealth();
    return () => { active = false; clearTimeout(timer); };
  }, [healthRefresh]);
  useEffect(() => { setDrawerOpen(false); }, [location.pathname]);

  async function removeRun(item) {
    if (!confirm(`Delete “${getResearchTitle(item)}”? This cannot be undone.`)) return;
    try {
      await api.deleteRun(item.run_id);
      await refreshRuns();
      if (location.pathname.includes(item.run_id)) navigate('/research/new');
    } catch (problem) { setShellError(problem.message); }
  }

  async function submitTacc(restart = false) {
    if (submittingTacc || healthFailed || !health?.tacc_job?.id) return;
    if (restart && !confirm(
      'End the current TACC job and request a new 1-hour allocation? Any ongoing model request will be interrupted. The new job will need to queue and load Qwen.'
    )) return;
    setTaccAction(restart ? 'restart' : 'submit');
    setShellError('');
    try {
      const result = await api.submitTacc(health.tacc_job.id, restart);
      setHealth(current => current ? {...current, model_status: 'queued',
        tacc_job: {id: result.job_id, queued_seconds: 0, remaining_seconds: null},
        receivedAt: Date.now()} : current);
    } catch (problem) {
      setShellError(problem.message);
    } finally {
      setTaccAction(null);
      refreshHealth();
    }
  }

  return <div className="app-shell">
    <ResearchSidebar health={displayedHealth} runs={runs} onDelete={removeRun} open={drawerOpen} onClose={() => setDrawerOpen(false)}
      onSubmitTacc={submitTacc} submittingTacc={submittingTacc}
      restartingTacc={taccAction === 'restart'}
      healthFailed={healthFailed} checkingHealth={checkingHealth} onRefreshHealth={refreshHealth} />
    <div className="app-main">
      <PointerRipple key={location.pathname} />
      <header className="mobile-header">
        <button aria-label="Open research history" onClick={() => setDrawerOpen(true)}>☰</button>
        <span>CineContext</span>
        {!healthFailed && <small className="mobile-model-timing"><TaccJobTiming health={displayedHealth} /></small>}
      </header>
      {shellError && <div className="shell-alert" role="alert">{shellError}<button aria-label="Dismiss" onClick={() => setShellError('')}>×</button></div>}
      <Outlet context={{health: displayedHealth, refreshRuns, removeRun}} />
      {health?.metadata_enabled && <footer className="tmdb-credit">
        <a href="https://www.themoviedb.org/" target="_blank" rel="noreferrer">TMDB</a>
        <span>This product uses the TMDB API but is not endorsed or certified by TMDB.</span>
      </footer>}
    </div>
  </div>;
}
