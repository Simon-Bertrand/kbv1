import type { Metadata } from 'next';
import type { ReactNode } from 'react';
import '../css/index.css';
import '../css/fonts.css';
import '../css/template.css';
import copy from '../content/fr.json';
export const metadata: Metadata = {
  title: copy.application.title,
  description: copy.application.description,
  icons: { icon: { url: '/favicon.png', type: 'image/png' } },
};
const themeScript = `try{const t=localStorage.getItem('reno-plus-theme');document.documentElement.dataset.theme=t==='light'||t==='dark'?t:matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'}catch{}`;
export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang={copy.application.language} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body>
        <a className="skip-link" href="#main">
          {copy.application.skipToContent}
        </a>
        {children}
      </body>
    </html>
  );
}
