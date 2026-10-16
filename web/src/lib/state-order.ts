/** A poll started before/during a queued save cannot replace its result. */
export function createStateOrder() {
  let revision = 0;
  let latestRead = 0;
  let pendingWrites = 0;
  return {
    beginRead: () => ({ revision, read: ++latestRead }),
    accepts: (ticket: { revision: number; read: number }) =>
      pendingWrites === 0 && ticket.revision === revision && ticket.read === latestRead,
    beginWrite: () => { pendingWrites++; revision++; },
    finishWrite: () => { pendingWrites = Math.max(0, pendingWrites - 1); revision++; },
  };
}
