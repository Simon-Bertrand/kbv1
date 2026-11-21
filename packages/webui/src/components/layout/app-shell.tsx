'use client';
import { useEffect, useState, type CSSProperties, type ReactNode } from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import copy from '../../content/fr.json';
import { useRenoActions, useChatSelector } from '../chat/provider';
import { Icon } from '../ui/icon';
import { IconButton } from '../ui/button';
import { Dialog } from '../ui/dialog';
import { useTheme } from './use-theme';
import { useSidebar } from './use-sidebar';
import { Sidebar } from './sidebar';
export function AppShell({ children }: { children: ReactNode }) {
  const { chat } = useRenoActions();
  const persistent = useChatSelector((state) => state.persistent);
  const sidebar = useSidebar();
  const { theme, setTheme } = useTheme();
  const router = useRouter();
  const pathname = usePathname();
  const [drawer, setDrawer] = useState(false);
  const [settings, setSettings] = useState(false);
  const navigate = () => {
    setDrawer(false);
    router.push('/');
  };
  const newChat = () => {
    chat.newConversation();
    navigate();
  };
  useEffect(() => {
    if (location.hash === '#catalog') router.replace('/catalog');
    else if (location.hash.startsWith('#knowledge')) router.replace('/');
  }, [router]);
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey) || document.querySelector('dialog[open]')) return;
      if (event.key.toLowerCase() === 'k') {
        event.preventDefault();
        chat.newConversation();
        router.push('/');
      }
    };
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
  }, [chat, router]);
  return (
    <div
      className={`app-shell ${sidebar.dragging ? 'is-resizing-sidebar' : ''}`}
      style={{ '--sidebar-width': `${sidebar.collapsed ? 60 : sidebar.width}px` } as CSSProperties}
    >
      <Sidebar
        state={sidebar}
        open={drawer}
        onOpenChange={setDrawer}
        onSettings={() => {
          setDrawer(false);
          setSettings(true);
        }}
        onNew={newChat}
        onSelect={(id) => {
          chat.select(id);
          navigate();
        }}
      />
      <div className="workspace">
        <header className="topbar">
          <div className="topbar-left">
            <IconButton
              className="mobile-menu"
              id="mobile-menu"
              icon="menu"
              label={copy.navigation.ouvrirLaNavigation}
              aria-expanded={drawer}
              aria-controls="sidebar-drawer"
              onClick={() => setDrawer(true)}
            />
            <span className="breadcrumb">
              {copy.navigation.espacePersonnel}
              <Icon name="chevron" />
              <strong id="page-label">
                {pathname === '/catalog' ? copy.common.catalog : pathname === '/search' ? copy.documentSearch.title : pathname === '/status' ? copy.serviceStatus.title : copy.common.conversation}
              </strong>
            </span>
          </div>
          <nav className="workspace-views" aria-label={copy.documentSearch.navigation}>
            <Link href="/" aria-current={pathname === '/' ? 'page' : undefined}>{copy.documentSearch.agent}</Link>
            <Link href="/search" aria-current={pathname === '/search' ? 'page' : undefined}>{copy.documentSearch.title}</Link>
          </nav>
          <div className="topbar-right">
            <IconButton
              id="theme-toggle"
              icon={theme === 'dark' ? 'sun' : 'moon'}
              label={theme === 'dark' ? copy.common.light : copy.common.dark}
              onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
            />
          </div>
        </header>
        <main id="main" tabIndex={-1}>
          {children}
        </main>
        <footer className="app-footer">
          <span>
            {copy.common.brand}
            <span className="footer-dot">{copy.navigation.footerSeparator}</span>
            {copy.navigation.assistant}
          </span>
          <Link href="/status" prefetch={false} aria-current={pathname === '/status' ? 'page' : undefined}>{copy.serviceStatus.title}</Link>
          <span className="footer-signature">{copy.navigation.developpeParLEquipeReno}</span>
        </footer>
      </div>
      <Dialog
        id="settings-dialog"
        title={copy.common.settings}
        closeLabel={copy.navigation.fermerLesParametres}
        open={settings}
        onOpenChange={setSettings}
      >
        <label className="field">
          {copy.navigation.apparence}
          <select
            id="theme-select"
            className="input"
            value={theme}
            onChange={(event) => setTheme(event.target.value as 'light' | 'dark')}
          >
            <option value="light">{copy.common.light}</option>
            <option value="dark">{copy.common.dark}</option>
          </select>
        </label>
        <p id="storage-status" className="muted">
          {persistent
            ? copy.common.conversationsEtBrouillonsEnregistresLocalement
            : copy.chatMessages.stockageLocalIndisponibleLesConversationsRestentDisponibles}
        </p>
        <p className="muted">{copy.navigation.leThemeLaLargeurDuMenuLes}</p>
      </Dialog>
    </div>
  );
}
