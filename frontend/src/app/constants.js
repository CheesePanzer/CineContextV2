export const AXIS_NAMES = {release: 'Release date', story: 'Story period', myth: 'Mythology', history: 'Historical context'};
export const TACC_STATUS_NAMES = {
  queued: 'Waiting for TACC GPUs', starting: 'Qwen is starting', ready: 'TACC ready',
  stopped: 'TACC job ended', unavailable: 'TACC status unavailable',
  restarting: 'Replacing TACC job',
};
export const TACC_WAITING_STATES = new Set(['queued', 'starting', 'stopped', 'restarting']);
export const STAGE_NAMES = {
  queued: 'Queued', planning: 'Planning the research', searching: 'Finding sources',
  assessing: 'Evaluating films', follow_up: 'Checking evidence', generating: 'Writing the guide',
  validating: 'Validating results', finished: 'Finished',
};
export const STATUS_NAMES = {
  queued: 'Queued', running: 'Running', completed: 'Completed', partial: 'Partial results',
  failed: 'Failed', interrupted: 'Interrupted',
};
