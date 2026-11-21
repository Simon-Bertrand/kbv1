import React, { useEffect, useId, useRef } from "react";

export default function Modal({title,onClose,children}) {
  const ref=useRef(null), heading=useId(), opener=useRef(document.activeElement);
  useEffect(()=>{
    const dialog=ref.current, previous=opener.current;
    dialog.showModal();
    return()=>{dialog.close();if(previous?.isConnected)previous.focus();};
  },[]);
  return <dialog ref={ref} className="km-modal" aria-labelledby={heading} onCancel={e=>{e.preventDefault();onClose();}}
    onClick={e=>{if(e.target===e.currentTarget){const box=e.currentTarget.getBoundingClientRect();if(e.clientX<box.left||e.clientX>box.right||e.clientY<box.top||e.clientY>box.bottom)onClose();}}}>
    <header className="km-modal-heading"><h2 id={heading}>{title}</h2><button type="button" className="secondary" onClick={onClose} autoFocus>Fermer</button></header>
    <div className="km-modal-body">{children}</div>
  </dialog>;
}
