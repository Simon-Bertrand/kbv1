import React, { memo } from "react";
import AthenaView from "../features/athena/view.jsx";
import RagView from "../features/rag/view.jsx";
import TracesView from "../features/traces/view.jsx";
import EvaluationView from "../features/evaluation/view.jsx";
import Workspaces from "./Workspaces.jsx";
import Indexation from "../features/documents/Indexation.jsx";
function NavigationIcon({name}) {
  const paths = {
    athena: "M4 4h16v12H9l-5 4V4Z",
    sources: "M6 3h9l4 4v14H6V3Zm9 0v5h4M9 12h7m-7 4h7",
    rag: "m16 16 5 5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0Z",
    traces: "M12 3v6M5 15V9h14v6M2 15h6v6H2Zm14 0h6v6h-6Z",
    library: "M4 5h16v4H4Zm0 6h16v4H4Zm0 6h16v4H4Z",
    evaluation: "M4 3h16v18H4Zm4 5 1 1 2-2m2 1h4M8 14l1 1 2-2m2 1h4",
    atlas: "m12 2 9 5v10l-9 5-9-5V7Zm0 10 9-5M12 12 3 7m9 5v10",
  };
  return <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]}/></svg>;
}
const ConsoleLayout = memo(function ConsoleLayout() {
  return (
    <>
      <aside className="rail">
        <a className="brand" href="/" aria-label="Accueil KM">KM</a>
        <nav aria-label="Navigation principale">
          {[["athena","Agent"],["sources","Documents"],["rag","Recherche"],["traces","Suivi"],["library","Indexation"],["evaluation","Évaluation"],["atlas","Atlas"]].map(([id,label])=>
            <button key={id} className={"nav"+(id==="athena"?" active":"")} data-view={id}><NavigationIcon name={id}/>{label}</button>
          )}
        </nav>
        <div className="rail-bottom">
          <div className="status-line">
            <b id="agentDot" className="dot"></b>
            <span id="agentState">{"Connexion à l’agent KM…"}</span>
          </div>
          <div className="status-line">
            <b id="ragDot" className="dot"></b>
            <span id="ragState">{"Connexion au RAG…"}</span>
          </div>

        </div>
      </aside>
      {"\n"}
      <main>
        <div className="sr" aria-hidden="true"><span id="viewTitle">Agent</span><span id="clock"/></div>
        <AthenaView />
        <Workspaces />
        {"\n"}
        <RagView />
        {"\n"}
        <TracesView />
        {"\n"}
        <Indexation />
        {"\n"}
        <EvaluationView />
        {"\n"}
      </main>
      <div id="toast" role="status"></div>
    </>
  );
});
export default ConsoleLayout;
