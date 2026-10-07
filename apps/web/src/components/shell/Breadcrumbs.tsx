export type Breadcrumb = {
  href: string;
  label: string;
};

type BreadcrumbsProps = {
  items: readonly Breadcrumb[];
  onNavigate: (href: string) => void;
};

export default function Breadcrumbs({ items, onNavigate }: BreadcrumbsProps) {
  return (
    <nav aria-label="Breadcrumb" className="breadcrumbs">
      <ol>
        {items.map((item) => (
          <li key={item.href}>
            <a
              href={item.href}
              onClick={(event) => {
                event.preventDefault();
                onNavigate(item.href);
              }}
            >
              <span>{item.label}</span>
            </a>
          </li>
        ))}
      </ol>
    </nav>
  );
}
