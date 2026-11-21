import Link from 'next/link';
import copy from '../content/fr.json';
export default function NotFound() {
  return (
    <main id="main" className="catalog-page" tabIndex={-1}>
      <h1>{copy.application.notFoundTitle}</h1>
      <p>{copy.application.notFoundDescription}</p>
      <Link className="button" href="/">
        {copy.application.backToChat}
      </Link>
    </main>
  );
}
