import React, { Component, Suspense, lazy } from "react";
import { createRoot } from "react-dom/client";
import "./base.css";

const Application = lazy(() => import("./shell/ConsolePage.jsx"));

class LoadBoundary extends Component {
  state = { error: null };
  static getDerivedStateFromError(error) {
    return { error };
  }
  render() {
    return this.state.error ? (
      <p role="alert">
        Chargement impossible : {this.state.error.message}. Actualiser pour
        réessayer.
      </p>
    ) : (
      this.props.children
    );
  }
}

document.title = "KM";
createRoot(document.getElementById("root")).render(
  <LoadBoundary>
    <Suspense fallback={<p role="status">Chargement…</p>}>
      <Application />
    </Suspense>
  </LoadBoundary>,
);
