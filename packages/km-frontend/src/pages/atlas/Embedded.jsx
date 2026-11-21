import React from "react";
import IsolatedScreen from "../../shared/IsolatedScreen.jsx";
import View from "./view.jsx";
import { mountPage } from "./controller.js";
import css from "./style.css?inline";
import tokens from "../../styles/theme.css?inline";
import theme from "../../styles/embedded-theme.css?inline";

const scoped =
  css
    .replace(/\bbody\s*\{/g, ":host {")
    .replace(
      /@media\s*\((?:max-width:\s*600px|width\s*<=\s*600px)\)/g,
      "@container (max-width: 750px)",
    ) +
  "\n:host { container-type: inline-size; }" +
  tokens +
  theme;
export default function EmbeddedAtlas() {
  return <IsolatedScreen View={View} mount={mountPage} css={scoped} />;
}
