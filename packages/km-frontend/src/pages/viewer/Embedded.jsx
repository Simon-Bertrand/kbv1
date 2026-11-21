import React from "react";
import IsolatedScreen from "../../shared/IsolatedScreen.jsx";
import View from "./view.jsx";
import { mountPage } from "./controller.js";
import css from "./style.css?inline";
import tokens from "../../styles/theme.css?inline";
import theme from "../../styles/embedded-theme.css?inline";

const scoped =
  css
    .replace(/body((?:\.[\w-]+)+)/g, ":host($1)")
    .replace(/\bbody\b|:root/g, ":host") +
  tokens +
  theme;
export default function EmbeddedViewer() {
  return <IsolatedScreen View={View} mount={mountPage} css={scoped} />;
}
