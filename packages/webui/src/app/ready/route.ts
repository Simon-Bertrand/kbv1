import {readiness} from '../../backend/readiness';
export const runtime='nodejs';
export const dynamic='force-dynamic';
export const GET=()=>readiness();
