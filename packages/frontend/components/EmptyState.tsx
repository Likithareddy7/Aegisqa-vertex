export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="empty-state">
      <p style={{ fontWeight: 600, color: "var(--text)", margin: "0 0 4px" }}>{title}</p>
      {description && <p className="muted">{description}</p>}
      {action && <div style={{ marginTop: 12 }}>{action}</div>}
    </div>
  );
}
