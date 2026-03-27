import { useCallback, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import toast from "react-hot-toast";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  Check,
  ChevronLeft,
  ChevronRight,
  FileText,
  RefreshCw,
  X,
} from "lucide-react";
import { documentsApi } from "../services/api";
import type { BoundingBox, TextBlock } from "../services/api";
import { DocumentViewer } from "../components/document-viewer";
import { FieldEditor } from "../components/field-editor";

interface FieldData {
  key: string;
  label: string;
  value: unknown;
  confidence: number;
  bbox?: BoundingBox;
}

function formatFieldKey(key: string): string {
  return key
    .replace(/_/g, " ")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/^./, (str) => str.toUpperCase());
}

/**
 * Recursively flattens a nested data object into a flat list of FieldData entries.
 *
 * - Keys are joined with dots to form full paths (e.g. `"address.city"`).
 * - Arrays are treated as leaf values and are not recursed into.
 * - Confidence is looked up by full dot-path key, defaulting to 0.5.
 * - Bbox is looked up by full dot-path key first, then by the leaf key as fallback.
 */
function flattenFields(
  data: Record<string, unknown>,
  confidences: Record<string, number>,
  bboxEvidence: Record<string, TextBlock> = {},
  prefix = ""
): FieldData[] {
  const fields: FieldData[] = [];

  for (const [key, value] of Object.entries(data)) {
    const fullKey = prefix ? `${prefix}.${key}` : key;

    if (value !== null && typeof value === "object" && !Array.isArray(value)) {
      fields.push(...flattenFields(value as Record<string, unknown>, confidences, bboxEvidence, fullKey));
    } else {
      fields.push({
        key: fullKey,
        label: formatFieldKey(key),
        value,
        confidence: confidences[fullKey] ?? 0.5,
        bbox: bboxEvidence[fullKey]?.bbox ?? bboxEvidence[key]?.bbox,
      });
    }
  }

  return fields;
}

export function Review() {
  const { id } = useParams<{ id?: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [selectedField, setSelectedField] = useState<string | undefined>();
  const [selectedBbox, setSelectedBbox] = useState<BoundingBox | undefined>();
  const [currentPage, setCurrentPage] = useState(1);
  const [zoom, setZoom] = useState(1);
  const [panOffset, setPanOffset] = useState({ x: 0, y: 0 });
  const [isActioning, setIsActioning] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [savingField, setSavingField] = useState<string | null>(null);

  // Fetch both "review" and "manual_review" documents for the queue.
  // "manual_review" includes unknown documents routed there by the classifier.
  const { data: reviewList, refetch: refetchReview } = useQuery({
    queryKey: ["documents", "review"],
    queryFn: () => documentsApi.list(0, 50, "review"),
    enabled: !id,
  });
  const { data: manualList, refetch: refetchManual } = useQuery({
    queryKey: ["documents", "manual_review"],
    queryFn: () => documentsApi.list(0, 50, "manual_review"),
    enabled: !id,
  });

  const documentList = reviewList && manualList
    ? { ...reviewList, items: [...reviewList.items, ...manualList.items] }
    : reviewList ?? manualList;

  const refetchList = useCallback(() => {
    void Promise.allSettled([refetchReview(), refetchManual()]);
  }, [refetchReview, refetchManual]);

  // Fetch specific document
  const {
    data: documentResult,
    isLoading,
    error,
    refetch: refetchDoc,
  } = useQuery({
    queryKey: ["document-result", id],
    queryFn: () => documentsApi.getResult(id!),
    enabled: !!id,
  });

  const pageCount = documentResult?.ocr_result?.page_count ?? 1;

  // Fetch page image URL for bbox overlay
  const { data: pageImageData } = useQuery({
    queryKey: ["document-page-image", id, currentPage],
    queryFn: () => documentsApi.getPageImageUrl(id!, currentPage),
    enabled: !!id && !!documentResult,
  });

  const handleFieldSelect = useCallback((fieldKey: string, bbox?: BoundingBox) => {
    setSelectedField(fieldKey);
    setSelectedBbox(bbox);
    if (bbox) {
      const centerX = (bbox.x1 + bbox.x2) / 2;
      const centerY = (bbox.y1 + bbox.y2) / 2;
      setPanOffset({ x: -centerX + 400, y: -centerY + 300 });
    }
  }, []);

  const handleApprove = async () => {
    if (!id) return;
    setIsActioning(true);
    try {
      await documentsApi.update(id, { decision: "auto" });
      setActionError(null);
      toast.success("Document approved");
      refetchList();
      navigate("/review");
    } catch (e) {
      const msg = "Approve failed: " + (e instanceof Error ? e.message : "Unknown error");
      setActionError(msg);
      toast.error(msg);
    } finally {
      setIsActioning(false);
    }
  };

  const handleReject = async () => {
    if (!id) return;
    setIsActioning(true);
    try {
      await documentsApi.update(id, { decision: "manual" });
      setActionError(null);
      toast.success("Document rejected — moved to manual review");
      refetchList();
      navigate("/review");
    } catch (e) {
      const msg = "Reject failed: " + (e instanceof Error ? e.message : "Unknown error");
      setActionError(msg);
      toast.error(msg);
    } finally {
      setIsActioning(false);
    }
  };

  const handleFieldUpdate = useCallback(async (key: string, value: unknown) => {
    if (!id) return;
    setSavingField(key);
    try {
      await documentsApi.updateFields(id, { [key]: value });
      queryClient.invalidateQueries({ queryKey: ["document-result", id] });
    } catch {
      toast.error("Failed to save field. Please try again.");
    } finally {
      setSavingField(null);
    }
  }, [id, queryClient]);

  // Document list view
  if (!id) {
    const documents = documentList?.items ?? [];

    return (
      <div className="p-6">
        <div className="mb-6">
          <h1 className="text-2xl font-bold text-gray-900">Review Queue</h1>
          <p className="mt-1 text-sm text-gray-500">
            Documents that need manual review before processing is complete
          </p>
        </div>

        {documents.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 bg-white rounded-lg border border-gray-200">
            <Check className="w-12 h-12 text-green-500 mb-4" />
            <h3 className="text-lg font-medium text-gray-900">All caught up!</h3>
            <p className="mt-1 text-sm text-gray-500">
              No documents require review at this time.
            </p>
          </div>
        ) : (
          <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Document
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Type
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Status
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Confidence
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Date
                  </th>
                  <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Action
                  </th>
                </tr>
              </thead>
              <tbody className="bg-white divide-y divide-gray-200">
                {documents.map((doc) => (
                  <tr key={doc.id} className="hover:bg-gray-50">
                    <td className="px-6 py-4 whitespace-nowrap">
                      <div className="flex items-center">
                        <FileText className="h-5 w-5 text-gray-400 mr-3" />
                        <div>
                          <div className="text-sm font-medium text-gray-900">
                            {doc.original_filename}
                          </div>
                          <div className="text-xs text-gray-500">
                            {(doc.file_size / 1024).toFixed(1)} KB
                          </div>
                        </div>
                      </div>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <span className="px-2 py-1 text-xs font-medium bg-blue-100 text-blue-800 rounded-full">
                        {doc.document_type?.replace("_", " ") || "Unknown"}
                      </span>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <span className="px-2 py-1 text-xs font-medium bg-yellow-100 text-yellow-800 rounded-full">
                        Review Required
                      </span>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                      —
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                      {new Date(doc.created_at).toLocaleDateString()}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-right">
                      <a
                        href={`/review/${doc.id}`}
                        className="text-blue-600 hover:text-blue-800 text-sm font-medium"
                      >
                        Review
                      </a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    );
  }

  // Document review detail view
  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <RefreshCw className="w-8 h-8 text-blue-600 animate-spin" />
      </div>
    );
  }

  if (error || !documentResult) {
    return (
      <div className="flex flex-col items-center justify-center h-full">
        <AlertCircle className="w-12 h-12 text-red-500 mb-4" />
        <h3 className="text-lg font-medium text-gray-900">Error loading document</h3>
        <p className="mt-1 text-sm text-gray-500">
          {error instanceof Error ? error.message : "Unknown error occurred"}
        </p>
        <a
          href="/review"
          className="mt-4 text-blue-600 hover:text-blue-800 text-sm font-medium"
        >
          Back to queue
        </a>
      </div>
    );
  }

  // Transform data to fields — use bbox_evidence for direct coordinate mapping
  const fields: FieldData[] = documentResult.structured_data
    ? flattenFields(
        documentResult.structured_data.extracted_data,
        documentResult.structured_data.field_confidences,
        documentResult.structured_data.bbox_evidence ?? {}
      )
    : [];

  // Blocks for current page only
  const currentPageBlocks = (documentResult.ocr_result?.text_blocks ?? []).filter(
    (b) => (b.page ?? 1) === currentPage
  );

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 bg-white border-b border-gray-200">
        <div className="flex items-center gap-4">
          <a
            href="/review"
            className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
          >
            <ChevronLeft className="w-5 h-5" />
          </a>
          <div>
            <h1 className="text-xl font-bold text-gray-900">Review Document</h1>
            <p className="text-sm text-gray-500">
              {documentResult.document_type?.replace("_", " ")} •{" "}
              {documentResult.decision?.toUpperCase() || "PENDING"}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          {/* Page navigation */}
          {pageCount > 1 && (
            <div className="flex items-center gap-1 border border-gray-200 rounded-lg px-2 py-1">
              <button
                onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                disabled={currentPage <= 1}
                aria-label="Previous page"
                className="p-1 disabled:opacity-40 hover:bg-gray-100 rounded"
              >
                <ChevronLeft className="w-4 h-4" />
              </button>
              <span className="text-sm text-gray-600 px-1">
                {currentPage} / {pageCount}
              </span>
              <button
                onClick={() => setCurrentPage((p) => Math.min(pageCount, p + 1))}
                disabled={currentPage >= pageCount}
                aria-label="Next page"
                className="p-1 disabled:opacity-40 hover:bg-gray-100 rounded"
              >
                <ChevronRight className="w-4 h-4" />
              </button>
            </div>
          )}
          <div className="flex flex-col items-end gap-1">
            <div className="flex items-center gap-3">
              <button
                onClick={handleReject}
                disabled={isActioning}
                className="flex items-center gap-2 px-4 py-2 text-red-600 border border-red-200 rounded-lg hover:bg-red-50 transition-colors disabled:opacity-50"
              >
                {isActioning ? <RefreshCw className="w-4 h-4 animate-spin" /> : <X className="w-4 h-4" />}
                Reject
              </button>
              <button
                onClick={handleApprove}
                disabled={isActioning}
                className="flex items-center gap-2 px-4 py-2 text-white bg-green-600 rounded-lg hover:bg-green-700 transition-colors disabled:opacity-50"
              >
                {isActioning ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
                Approve
              </button>
            </div>
            {actionError && (
              <p className="text-xs text-red-600 mt-1">{actionError}</p>
            )}
          </div>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 flex overflow-hidden">
        {/* Document viewer */}
        <div className="flex-1 bg-gray-100 p-4">
          <DocumentViewer
            imageUrl={pageImageData?.url}
            textBlocks={currentPageBlocks}
            selectedBbox={selectedBbox}
            onFieldSelect={handleFieldSelect}
            zoom={zoom}
            panOffset={panOffset}
            onZoomChange={setZoom}
            onPanChange={setPanOffset}
          />
        </div>

        {/* Field editor / OCR fallback */}
        <div className="w-96 bg-gray-50 border-l border-gray-200 overflow-hidden flex flex-col">
          {fields.length > 0 ? (
            <FieldEditor
              fields={fields}
              onFieldUpdate={handleFieldUpdate}
              onFieldSelect={handleFieldSelect}
              selectedField={selectedField}
              savingField={savingField}
            />
          ) : (
            <div className="flex flex-col h-full overflow-hidden">
              <div className="px-4 py-3 border-b border-gray-200 bg-white">
                <h2 className="text-sm font-semibold text-gray-900">OCR Text</h2>
                <p className="text-xs text-gray-500 mt-0.5">
                  No structured data — document routed to manual review
                </p>
              </div>
              <div className="flex-1 overflow-y-auto p-4">
                {documentResult.ocr_result?.full_text ? (
                  <pre className="text-xs text-gray-700 whitespace-pre-wrap font-mono leading-relaxed">
                    {documentResult.ocr_result.full_text}
                  </pre>
                ) : (
                  <p className="text-sm text-gray-400 text-center mt-8">No OCR text extracted.</p>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Reconciliation info */}
      {documentResult.reconciliation && (
        <div className="px-6 py-3 bg-white border-t border-gray-200">
          <div className="flex items-center gap-4 text-sm">
            <span
              className={`px-2 py-1 text-xs font-medium rounded-full ${
                documentResult.reconciliation.status === "pass"
                  ? "bg-green-100 text-green-800"
                  : documentResult.reconciliation.status === "warn"
                  ? "bg-yellow-100 text-yellow-800"
                  : "bg-red-100 text-red-800"
              }`}
            >
              Math: {documentResult.reconciliation.status.toUpperCase()}
            </span>
            {documentResult.reconciliation.subtotal_match !== undefined && (
              <span className="text-gray-600">
                Subtotal:{" "}
                {documentResult.reconciliation.subtotal_match ? (
                  <span className="text-green-600">Match</span>
                ) : (
                  <span className="text-red-600">Mismatch</span>
                )}
              </span>
            )}
            {documentResult.reconciliation.vat_match !== undefined && (
              <span className="text-gray-600">
                VAT:{" "}
                {documentResult.reconciliation.vat_match ? (
                  <span className="text-green-600">Match</span>
                ) : (
                  <span className="text-red-600">Mismatch</span>
                )}
              </span>
            )}
            {documentResult.reconciliation.total_match !== undefined && (
              <span className="text-gray-600">
                Total:{" "}
                {documentResult.reconciliation.total_match ? (
                  <span className="text-green-600">Match</span>
                ) : (
                  <span className="text-red-600">Mismatch</span>
                )}
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
