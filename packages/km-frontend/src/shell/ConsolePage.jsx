import "katex/dist/katex.min.css";
import React, { useEffect } from "react";
import ConsoleLayout from "./view.jsx";
import { mountConsole } from "../features/console/controller.js";
import { mountEvaluation } from "../features/evaluation/controller.js";
import "../styles/console.css";
import "../styles/evaluation.css";
import "../styles/application.css";
import "../styles/theme.css";
import "../styles/workbench.css";
import { initialDestination } from "./navigation.js";

export default function ConsolePage() {
  useEffect(() => {
    const console = mountConsole();
    const disposeEvaluation = mountEvaluation(console);
    const destination = initialDestination(window.location);
    console.show(destination.view, { updateUrl: false });
    return () => {
      disposeEvaluation();
      console.dispose();
    };
  }, []);
  return <ConsoleLayout />;
}
