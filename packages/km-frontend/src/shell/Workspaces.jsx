import React, { Suspense, lazy, useEffect, useState } from "react";

const Sources = lazy(() => import("../features/documents/Sources.jsx"));
const Atlas = lazy(() => import("../pages/atlas/Embedded.jsx"));
const Viewer = lazy(() => import("../pages/viewer/Embedded.jsx"));

export default function Workspaces() {
  const [opened, setOpened] = useState({});
  useEffect(() => {
    const onView = (e) => {
      if (["sources", "atlas", "viewer"].includes(e.detail)) {
        setOpened((previous) =>
          previous[e.detail] ? previous : { ...previous, [e.detail]: true },
        );
      }
    };
    window.addEventListener("console:view", onView);
    return () => window.removeEventListener("console:view", onView);
  }, []);
  return (
    <>
      <section id="sources" className="view integrated-view" aria-label="Documents">
        <Suspense fallback={<p role="status">Chargement des documents…</p>}>
          {opened.sources && <Sources />}
        </Suspense>
      </section>
      <section
        id="atlas"
        className="view integrated-view"
        aria-label="Atlas des métadonnées"
      >
        <Suspense fallback={<p role="status">Chargement de l’atlas…</p>}>
          {opened.atlas && <Atlas />}
        </Suspense>
      </section>
      <section
        id="viewer"
        className="view integrated-view"
        aria-label="Pages sources"
      >
        <Suspense fallback={<p role="status">Chargement des pages…</p>}>
          {opened.viewer && <Viewer />}
        </Suspense>
      </section>
    </>
  );
}
