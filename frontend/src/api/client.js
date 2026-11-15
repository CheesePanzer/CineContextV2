const API_ROOT = '/api';

export async function request(path, options = {}) {
  let response;
  try {
    const timeout = path === '/requests/parse' ? 900000 : path === '/tacc/jobs' ? 330000 : 15000;
    response = await fetch(API_ROOT + path, {...options, signal: AbortSignal.timeout(timeout)});
  } catch (problem) {
    throw new Error(problem.name === 'TimeoutError'
      ? 'The request timed out. Try again or check the run later.'
      : 'Cannot reach the server. Check that it is running.');
  }
  const data = await response.json().catch(() => null);
  if (!response.ok || data === null) {
    const detail = typeof data?.detail === 'string'
      ? data.detail
      : typeof data?.title === 'string' ? data.title : 'The request failed. Check your input or try again later.';
    const problem = new Error(detail);
    problem.status = response.status;
    problem.problem = data;
    throw problem;
  }
  return data;
}

export function post(path, body) {
  return request(path, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body),
  });
}

export const api = {
  health: () => request('/health', {cache: 'no-store'}),
  submitTacc: (previousJobId, restart = false) => post('/tacc/jobs', {previous_job_id: previousJobId, restart}),
  listRuns: () => request('/runs'),
  parseRequest: prompt => post('/requests/parse', {prompt}),
  createRun: (submissionKey, constraints) => post('/runs', {submission_key: submissionKey, constraints}),
  getRun: runId => request(`/runs/${runId}`),
  deleteRun: runId => request(`/runs/${runId}`, {method: 'DELETE'}),
  retryGuide: runId => post(`/runs/${runId}/retry-guide`, {}),
  getFilm: (runId, filmId) => request(`/runs/${runId}/films/${filmId}`),
  getFilmReadings: (runId, filmId) => request(`/runs/${runId}/films/${filmId}/readings`),
  getRatings: filmId => request(`/films/${filmId}/ratings`),
};
