import React, { Suspense, lazy, useEffect, useState } from "react";
import { initialDestination } from "./navigation.js";

const Sources = lazy(() => import("../features/documents/Sources.jsx"));
const Atlas = lazy(() => import("../pages/atlas/Embedded.jsx"));
const Viewer = lazy(() => import("../pages/viewer/Embedded.jsx"));
const Ingestion = lazy(() => import("../features/ingestion/IngestionView.jsx"));

export default function Workspaces() {
  const [opened, setOpened] = useState(() => {
    if (typeof window !== "undefined") {
      const dest = initialDestination(window.location);
      if (["sources", "atlas", "viewer", "ingestion"].includes(dest.view)) {
        return { [dest.view]: true };
      }
    }
    return {};
  });
  useEffect(() => {
    const onView = (e) => {
      if (["sources", "atlas", "viewer", "ingestion"].includes(e.detail)) {
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
      <section
        id="ingestion"
        className="view integrated-view"
        aria-label="Orchestrateur Ingestion"
      >
        <Suspense fallback={<p role="status">Chargement de l'ingestion…</p>}>
          {opened.ingestion && <Ingestion />}
        </Suspense>
      </section>
    </>
  );
}
