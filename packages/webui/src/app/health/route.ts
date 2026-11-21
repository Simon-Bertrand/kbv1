export const dynamic='force-dynamic';
export const GET=()=>Response.json({status:'alive'},{headers:{'Cache-Control':'no-store'}});
