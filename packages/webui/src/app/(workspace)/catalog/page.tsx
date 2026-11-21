import { Catalog } from '../../../components/catalog/catalog';
import copy from '../../../content/fr.json';
export const metadata = { title: `${copy.common.catalog} — ${copy.common.brand}` };
export default function Page() {
  return <Catalog />;
}
