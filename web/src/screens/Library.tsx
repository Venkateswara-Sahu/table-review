import { useMemo, useRef, useState, type DragEvent } from "react";
import { useNavigate } from "react-router-dom";
import {
  ArrowUpRight,
  FilePlus2,
  MoreHorizontal,
  Search,
  Trash2,
  UploadCloud,
} from "lucide-react";
import {
  useDeleteDocument,
  useDocuments,
  useOpenDocument,
  useUploadDocument,
} from "../api/hooks";
import { useConfirm } from "../app/confirm";
import { useNotices } from "../app/notices";
import { FileIcon } from "../ui/FileIcon";
import { Menu } from "../ui/Menu";
import {
  Badge,
  Button,
  EmptyState,
  IconButton,
  InlineNote,
  Input,
  Spinner,
} from "../ui/primitives";
import {
  formatBytes,
  formatRelative,
  preparationLabel,
  retentionLabel,
} from "../ui/format";

export function LibraryScreen() {
  const documents = useDocuments();
  const upload = useUploadDocument();
  const open = useOpenDocument();
  const remove = useDeleteDocument();
  const confirm = useConfirm();
  const { notify, notifyError } = useNotices();
  const navigate = useNavigate();
  const [dragging, setDragging] = useState(false);
  const [filter, setFilter] = useState("");
  const [pendingName, setPendingName] = useState("");
  const input = useRef<HTMLInputElement>(null);

  const items = documents.data?.items ?? [];
  const filtered = useMemo(() => {
    const query = filter.trim().toLowerCase();
    return query
      ? items.filter((item) => item.filename.toLowerCase().includes(query))
      : items;
  }, [filter, items]);

  async function uploadFile(file?: File) {
    if (!file) return;
    setPendingName(file.name);
    try {
      const document = await upload.mutateAsync(file);
      notify(`Uploaded ${document.filename}`, "success");
      navigate(`/reports/${document.id}/pages/1`);
    } catch (error) {
      notifyError(error);
    } finally {
      setPendingName("");
      if (input.current) input.current.value = "";
    }
  }

  async function openDocument(documentId: string) {
    try {
      await open.mutateAsync(documentId);
      navigate(`/reports/${documentId}/pages/1`);
    } catch (error) {
      notifyError(error);
    }
  }

  async function deleteDocument(documentId: string, filename: string) {
    const ok = await confirm({
      title: `Delete ${filename}?`,
      body: (
        <div className="confirm-copy">
          <p>
            Its tables, review decisions and exports become inaccessible
            immediately. Active files leave storage within 24 hours; backups
            expire within 7 days.
          </p>
          <p>This cannot be undone from the interface.</p>
        </div>
      ),
      confirmLabel: "Delete report",
      tone: "danger",
    });
    if (!ok) return;
    try {
      await remove.mutateAsync(documentId);
      notify("Report deleted", "success");
    } catch (error) {
      notifyError(error);
    }
  }

  function onDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setDragging(false);
    const file = event.dataTransfer.files?.[0];
    if (file) void uploadFile(file);
  }

  return (
    <main className="page library-page">
      <div className="page-head library-head">
        <div>
          <h1>Reports</h1>
          <p className="page-description">
            Open a PDF, choose the pages, and check every figure against the
            original.
          </p>
        </div>
        <div className="library-meta">
          <span className="num">{items.length}</span>{" "}
          {items.length === 1 ? "report" : "reports"}
          <span className="library-meta-sep" />
          30-day retention
        </div>
      </div>

      <label
        className={`drop-zone ${dragging ? "drop-zone-active" : ""} ${
          upload.isPending ? "drop-zone-busy" : ""
        }`}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
      >
        <input
          ref={input}
          type="file"
          accept=".pdf,application/pdf"
          className="sr-only"
          disabled={upload.isPending}
          onChange={(event) => void uploadFile(event.target.files?.[0])}
        />
        <span className="drop-icon" aria-hidden="true">
          {upload.isPending ? (
            <Spinner size={22} />
          ) : dragging ? (
            <UploadCloud size={22} />
          ) : (
            <FilePlus2 size={22} />
          )}
        </span>
        <span className="drop-copy">
          <strong>
            {upload.isPending
              ? `Uploading ${pendingName || "report"}…`
              : "Drop a PDF to start"}
          </strong>
          <span>
            {upload.isPending
              ? "Keeping the original file and preparing page previews."
              : "or choose a file · up to 50 MiB and 500 pages"}
          </span>
        </span>
        <span className="drop-action">
          {upload.isPending ? "Working…" : "Choose PDF"}
        </span>
      </label>

      <section className="reports-section" aria-label="Reports">
        <div className="section-bar">
          <h2>All reports</h2>
          <div className="search-field">
            <Search size={15} aria-hidden="true" />
            <Input
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
              placeholder="Filter by name"
              aria-label="Filter reports by name"
            />
          </div>
        </div>

        {documents.isError && (
          <InlineNote tone="danger" title="Reports could not be loaded">
            <p>{documents.error.message}</p>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => void documents.refetch()}
            >
              Try again
            </Button>
          </InlineNote>
        )}

        {documents.isPending && (
          <div className="report-list" aria-hidden="true">
            {[0, 1, 2].map((index) => (
              <div className="report-row report-row-skeleton" key={index}>
                <span className="skeleton report-skeleton-icon" />
                <span className="skeleton report-skeleton-title" />
                <span className="skeleton report-skeleton-meta" />
              </div>
            ))}
          </div>
        )}

        {!documents.isPending && !documents.isError && !items.length && (
          <div className="panel">
            <EmptyState
              icon={<FileIcon />}
              title="No reports yet"
              actions={
                <Button
                  variant="primary"
                  onClick={() => input.current?.click()}
                  icon={<FilePlus2 size={15} />}
                >
                  Choose a PDF
                </Button>
              }
            >
              <p>
                Start with an annual report or financial statement. You choose
                the pages to extract.
              </p>
            </EmptyState>
          </div>
        )}

        {!!items.length && !filtered.length && (
          <div className="panel">
            <EmptyState
              compact
              title="No matching reports"
              actions={
                <Button variant="ghost" onClick={() => setFilter("")}>
                  Clear filter
                </Button>
              }
            >
              <p>Nothing matches “{filter}”.</p>
            </EmptyState>
          </div>
        )}

        {!!filtered.length && (
          <div className="report-list" role="list">
            <div className="report-row report-row-head" aria-hidden="true">
              <span />
              <span>Report</span>
              <span>Status</span>
              <span>Last opened</span>
              <span />
            </div>
            {filtered.map((item) => (
              <div className="report-row" role="listitem" key={item.id}>
                <FileIcon />
                <div className="report-main">
                  <h3 title={item.filename}>{item.filename}</h3>
                  <p className="report-meta">
                    <span className="num">{item.page_count}</span> pages
                    <span className="dot-sep" />
                    {formatBytes(item.byte_count)}
                    <span className="dot-sep" />
                    <span>{retentionLabel(item.last_foreground_at)}</span>
                  </p>
                </div>
                <div className="report-status">
                  <Badge
                    tone={
                      item.preparation_state === "ready"
                        ? "success"
                        : item.preparation_state === "failed"
                          ? "danger"
                          : "warning"
                    }
                    dot
                  >
                    {preparationLabel(item.preparation_state)}
                  </Badge>
                </div>
                <div className="report-opened">
                  <span>{formatRelative(item.last_foreground_at)}</span>
                </div>
                <div className="report-actions">
                  <Button
                    variant="secondary"
                    size="sm"
                    aria-label="Open"
                    icon={<ArrowUpRight size={14} />}
                    loading={open.isPending && open.variables === item.id}
                    onClick={() => void openDocument(item.id)}
                  >
                    Open
                  </Button>
                  <Menu
                    label={`Actions for ${item.filename}`}
                    trigger={
                      <IconButton label={`More actions for ${item.filename}`}>
                        <MoreHorizontal size={16} />
                      </IconButton>
                    }
                    items={[
                      {
                        label: "Delete report",
                        icon: <Trash2 size={15} />,
                        tone: "danger",
                        onSelect: () =>
                          void deleteDocument(item.id, item.filename),
                      },
                    ]}
                  />
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </main>
  );
}
