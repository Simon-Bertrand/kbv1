'use client';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useRef, useState } from 'react';
import { Dialog } from '../ui/dialog';
import { Icon } from '../ui/icon';
import { Button, IconButton } from '../ui/button';
import { useChatSelector, useRenoActions } from '../chat/provider';
import { canDeleteEmptyConversation } from '../../store/chat/model';
import { defaultSidebarWidth } from '../../store/ui-state';
import type { SidebarState } from './use-sidebar';
import copy from '../../content/fr.json';
import { formatText } from '../../content/text';
export function Sidebar({
  state,
  agentAvailable,
  open,
  onOpenChange,
  onSettings,
  onNew,
  onSelect,
}: {
  state: SidebarState;
  agentAvailable: boolean;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSettings: () => void;
  onNew: () => void;
  onSelect: (id: string) => void;
}) {
  const pathname = usePathname();
  const {chat}=useRenoActions();
  const activeId = useChatSelector((state) => state.activeId);
  const [deleting, setDeleting] = useState<string | null>(null);
  const history = useRef<HTMLElement>(null);
  const conversations = useChatSelector(
    (state) => state.conversations.map((item) => ({
      id: item.id, title: item.title,
      removable: canDeleteEmptyConversation(item) &&
        (item.id !== state.activeId || (!state.busy && !state.attachments.length)),
    })),
    (previous, next) =>
      previous.length === next.length &&
      previous.every(
        (item, index) => item.id === next[index]?.id && item.title === next[index]?.title && item.removable === next[index]?.removable,
      ),
  );
  const popover = useRef<HTMLDivElement>(null);
  const navigation = (
    <aside
      id="sidebar"
      className={`sidebar ${state.collapsed && !state.mobile ? 'is-collapsed' : ''}`}
      aria-label={copy.navigation.navigationPrincipale}
      data-agent-unavailable={!agentAvailable || undefined}
    >
      <div className="sidebar-heading">
        <Link
          href="/"
          className="brand"
          aria-label={copy.navigation.renoAccueil}
          title={copy.navigation.renoAccueil}
          onClick={() => onOpenChange(false)}
        >
          <img className="brand-logo" src="/logo.svg" width={1536} height={1024} alt="" />
        </Link>
        {state.mobile ? (
          <form method="dialog">
            <IconButton
              id="sidebar-close"
              type="submit"
              icon="close"
              label={copy.navigation.fermerLaNavigation}
            />
          </form>
        ) : (
          <IconButton
            id="sidebar-collapse"
            icon={state.collapsed ? 'panelExpand' : 'panelCollapse'}
            label={state.collapsed ? copy.sidebar.deplierLeMenu : copy.common.replierLeMenu}
            title={state.collapsed ? copy.sidebar.deplierLeMenu : copy.common.replierLeMenu}
            aria-expanded={!state.collapsed}
            aria-controls="sidebar"
            onClick={state.toggle}
          />
        )}
      </div>
      <Button
        id="new-chat"
        className="new-chat"
        aria-label={copy.navigation.nouveauChat}
        title={copy.navigation.nouveauChat}
        disabled={!agentAvailable}
        onClick={onNew}
      >
        <Icon name="plus" />
        <span className="sidebar-label">{copy.navigation.nouveauChat}</span>
        <kbd>{copy.navigation.k}</kbd>
      </Button>
      <nav className="primary-nav">
        <Link href="/search" className="nav-item" aria-current={pathname === '/search' ? 'page' : undefined}
          title={copy.documentSearch.title} aria-label={copy.documentSearch.title} onClick={() => onOpenChange(false)}>
          <Icon name="file" /><span className="sidebar-label">{copy.documentSearch.title}</span>
        </Link>
      </nav>
      <div className="history-heading eyebrow">
        {copy.navigation.vosConversations}
        <span>{copy.navigation.recentes}</span>
      </div>
      <nav
        className="history"
        ref={history}
        id="conversation-list"
        aria-disabled={!agentAvailable || undefined}
        aria-label={copy.navigation.historiqueDesConversations}
      >
        {chat.recoveredConversations.map(item=>(
          <button key={'recovery-'+item.id} className="history-item" title={item.title} disabled={!agentAvailable}
            onClick={()=>{const id=chat.restoreDeletedAsNew(item.id);if(id)onSelect(id);}}>
            <Icon name="chat" />
            <span>{copy.chatMessages.recupererCopieLocale} {item.title}</span>
          </button>
        ))}
        {conversations.map((item) => (
          <div className="history-row" key={item.id}>
            <button
              className="history-item"
              data-conversation={item.id}
              disabled={!agentAvailable}
              title={item.title}
              aria-label={item.title}
              aria-current={pathname === '/' && item.id === activeId ? 'page' : undefined}
              onClick={() => onSelect(item.id)}
            >
              <Icon name="chat" />
              <span>{item.title}</span>
            </button>
            {conversations.length > 1 && item.removable && (!state.collapsed || state.mobile) && (
              <IconButton
                className="history-delete"
                icon="trash"
                label={formatText(copy.common.deleteEmptyConversation, { title: item.title })}
                title={formatText(copy.common.deleteEmptyConversation, { title: item.title })}
                disabled={!agentAvailable || deleting !== null}
                onClick={async () => {
                  setDeleting(item.id);
                  try {
                    if (await chat.deleteEmptyConversation(item.id)) {
                      requestAnimationFrame(() => {
                        const target = history.current?.querySelector<HTMLElement>('[data-conversation][aria-current="page"]')
                          ?? history.current?.querySelector<HTMLElement>('[data-conversation]')
                          ?? document.getElementById('new-chat');
                        target?.focus();
                      });
                    }
                  } finally { setDeleting(null); }
                }}
              />
            )}
          </div>
        ))}
      </nav>
      <div className="sidebar-bottom">
        <div className="profile">
          <span className="avatar avatar--user">{copy.common.userInitials}</span>
          <div>
            <strong>{copy.navigation.jamieDupont}</strong>
            <span>{copy.navigation.espacePersonnel}</span>
          </div>
          <IconButton
            id="settings-button"
            icon="settings"
            label={copy.common.settings}
            title={copy.common.settings}
            onClick={onSettings}
          />
        </div>
      </div>
      {!state.mobile && !state.collapsed && (
        <>
          <div
            id="sidebar-resize"
            className="sidebar-resize"
            role="separator"
            aria-orientation="vertical"
            aria-label={copy.navigation.largeurDuMenu}
            aria-controls="sidebar"
            aria-valuemin={200}
            aria-valuemax={state.maximum}
            aria-valuenow={state.width}
            aria-valuetext={formatText(copy.sidebar.widthPixels, { width: state.width })}
            tabIndex={0}
            onPointerDown={(event) => {
              if (event.button !== 0) return;
              state.pointer.current = { id: event.pointerId, start: event.clientX, moved: false };
              event.currentTarget.setPointerCapture(event.pointerId);
              event.currentTarget.focus();
            }}
            onPointerMove={(event) => {
              const pointer = state.pointer.current;
              if (!pointer || pointer.id !== event.pointerId) return;
              if (Math.abs(event.clientX - pointer.start) > 3) pointer.moved = true;
              if (pointer.moved) {
                state.setDragging(true);
                state.setWidth(
                  event.clientX - event.currentTarget.parentElement!.getBoundingClientRect().left,
                );
              }
            }}
            onPointerUp={() => state.setDragging(false)}
            onPointerCancel={() => {
              state.pointer.current = null;
              state.setDragging(false);
            }}
            onLostPointerCapture={() => state.setDragging(false)}
            onClick={() => {
              if (!state.pointer.current?.moved) popover.current?.togglePopover();
              state.pointer.current = null;
            }}
            onDoubleClick={() => {
              state.setWidth(defaultSidebarWidth);
              popover.current?.hidePopover();
            }}
            onKeyDown={(event) => {
              if (['ArrowLeft', 'ArrowRight', 'Home', 'End', 'Enter', ' '].includes(event.key))
                event.preventDefault();
              if (event.key === 'ArrowLeft') state.setWidth(state.width - 8);
              if (event.key === 'ArrowRight') state.setWidth(state.width + 8);
              if (event.key === 'Home') state.setWidth(200);
              if (event.key === 'End') state.setWidth(state.maximum);
              if (event.key === 'Enter' || event.key === ' ') popover.current?.togglePopover();
            }}
          />
          <div
            ref={popover}
            id="sidebar-size-popover"
            className="sidebar-size-popover card"
            popover="auto"
          >
            <label className="field" htmlFor="sidebar-size">
              {copy.navigation.largeurDuMenu}
              <output id="sidebar-size-value" htmlFor="sidebar-size">
                {formatText(copy.sidebar.widthUnit, { width: state.width })}
              </output>
            </label>
            <input
              id="sidebar-size"
              type="range"
              autoFocus
              min={200}
              max={state.maximum}
              value={state.width}
              onChange={(event) => state.setWidth(event.target.valueAsNumber)}
            />
            <Button id="sidebar-size-reset" variant="ghost" onClick={() => state.setWidth(defaultSidebarWidth)}>
              {copy.common.reset}
            </Button>
          </div>
        </>
      )}
    </aside>
  );
  return state.mobile ? (
    <Dialog
      id="sidebar-drawer"
      className="sidebar-drawer"
      title={copy.navigation.navigationPrincipale}
      open={open}
      onOpenChange={onOpenChange}
      frame={false}
      lightDismiss
    >
      {navigation}
    </Dialog>
  ) : (
    <div className="desktop-sidebar">{navigation}</div>
  );
}
