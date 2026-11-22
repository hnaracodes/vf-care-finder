// The /api/sim contract (app/design/SPEC.md 3.3 and 3.4). A owns types.ts,
// where the interfaces live; the simulator re-exports them from here so its
// modules have one import point.

export type { FacStatus, SimFacility, SimLabel, SimPayload, SimSummary } from '../types';
