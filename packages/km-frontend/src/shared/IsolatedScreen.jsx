import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

function ScreenContent({ root, View, mount, css }) {
  useEffect(() => mount(root), [root, mount]);
  return (
    <>
      <style>{css}</style>
      <View />
    </>
  );
}

// A React portal isolates the existing canvas/viewer styles and DOM selectors
// from the application shell. No iframe or second browser application.
export default function IsolatedScreen({ View, mount, css }) {
  const host = useRef(null);
  const [root, setRoot] = useState(null);
  useLayoutEffect(() => {
    setRoot(
      host.current.shadowRoot || host.current.attachShadow({ mode: "open" }),
    );
  }, []);
  return (
    <div ref={host} className="isolated-screen">
      {root &&
        createPortal(
          <ScreenContent root={root} View={View} mount={mount} css={css} />,
          root,
        )}
    </div>
  );
}
