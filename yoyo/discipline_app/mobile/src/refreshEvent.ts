/** Visible panels add their read-only reloads to one user refresh gesture. */
export const REFRESH_EVENT='spike:refresh-visible';
export type RefreshRequest={tasks:Promise<unknown>[]};
export function refreshVisible(){const detail:RefreshRequest={tasks:[]};window.dispatchEvent(new CustomEvent(REFRESH_EVENT,{detail}));return Promise.all(detail.tasks)}
