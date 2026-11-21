import React, { useEffect } from "react";
import View from "./view.jsx";
import { mountPage } from "./controller.js";
import "./style.css";

export default function Page() {
  useEffect(mountPage, []);
  return <View />;
}
