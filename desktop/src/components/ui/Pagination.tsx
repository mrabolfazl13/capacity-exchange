import { Button } from '@/components/ui/Button';

export function Pagination({
  total,
  limit,
  offset,
  onChange,
  label,
}: {
  total: number;
  limit: number;
  offset: number;
  onChange: (nextOffset: number) => void;
  label?: string;
}) {
  const page = Math.floor(offset / Math.max(limit, 1)) + 1;
  const pages = Math.max(Math.ceil(total / Math.max(limit, 1)), 1);
  if (pages <= 1) return null;
  return (
    <nav className="pagination" aria-label={label ?? 'Pagination'}>
      <Button
        size="sm"
        disabled={offset <= 0}
        onClick={() => onChange(Math.max(0, offset - limit))}
      >
        ←
      </Button>
      <span className="small muted">
        {page} / {pages} · {total} results
      </span>
      <Button
        size="sm"
        disabled={offset + limit >= total}
        onClick={() => onChange(offset + limit)}
      >
        →
      </Button>
    </nav>
  );
}
