import { useParams, Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, FileText } from "lucide-react";
import { documentsApi } from "../services/api";

const ERROR_MAP: Record<string, string> = {
  unknown_document_type: "Document type could not be determined automatically",
  ocr_pipeline_disabled: "OCR processing is currently disabled",
  ocr_failed: "Text extraction failed — please retry or review manually",
};

const STATUS_LABEL_MAP: Record<string, { label: string; className: string }> = {
  completed: { label: "Completed", className: "bg-green-100 text-green-800" },
  failed: { label: "Failed", className: "bg-red-100 text-red-800" },
  review: { label: "Needs Review", className: "bg-yellow-100 text-yellow-800" },
  manual_review: { label: "Manual Review", className: "bg-yellow-100 text-yellow-800" },
  in_progress: { label: "In Progress", className: "bg-blue-100 text-blue-800" },
  pending: { label: "Pending", className: "bg-gray-100 text-gray-800" },
};

function StatusBadge({ status }: { status: string }) {
  const config = STATUS_LABEL_MAP[status] ?? {
    label: status.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()),
    className: "bg-gray-100 text-gray-800",
  };
  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${config.className}`}>
      {config.label}
    </span>
  );
}

export function DocumentDetail() {
  const { id } = useParams<{ id: string }>();

  const { data: doc, isLoading } = useQuery({
    queryKey: ["document", id],
    queryFn: () => documentsApi.get(id!),
    enabled: !!id,
  });

  if (isLoading) {
    return <div className="p-6">Loading...</div>;
  }

  if (!doc) {
    return <div className="p-6">Document not found</div>;
  }

  const errorMessage = doc.error_message
    ? (ERROR_MAP[doc.error_message] ?? doc.error_message)
    : null;

  return (
    <div className="p-6">
      <Link
        to="/documents"
        className="inline-flex items-center text-gray-600 hover:text-gray-900 mb-6"
      >
        <ChevronLeft className="w-4 h-4 mr-1" />
        Back to documents
      </Link>

      <div className="bg-white rounded-lg border border-gray-200 p-6">
        <div className="flex items-start gap-4">
          <div className="p-3 bg-blue-100 rounded-lg">
            <FileText className="w-8 h-8 text-blue-600" />
          </div>
          <div className="flex-1">
            <h1 className="text-2xl font-bold text-gray-900">
              {doc.original_filename}
            </h1>
            <div className="mt-2 flex items-center gap-4 text-sm text-gray-500">
              <span>{(doc.file_size / 1024).toFixed(1)} KB</span>
              <span>•</span>
              <span>{doc.content_type}</span>
              <span>•</span>
              <span>{new Date(doc.created_at).toLocaleString()}</span>
            </div>
          </div>
        </div>

        <div className="mt-6 grid grid-cols-3 gap-4">
          <div className="p-4 bg-gray-50 rounded-lg">
            <p className="text-sm font-medium text-gray-500">Status</p>
            <div className="mt-2">
              <StatusBadge status={doc.status} />
            </div>
          </div>
          <div className="p-4 bg-gray-50 rounded-lg">
            <p className="text-sm font-medium text-gray-500">Document Type</p>
            <p className="mt-1 font-semibold text-gray-900 capitalize">
              {doc.document_type?.replace(/_/g, " ") || "Unknown"}
            </p>
          </div>
          <div className="p-4 bg-gray-50 rounded-lg">
            <p className="text-sm font-medium text-gray-500">Decision</p>
            <p className="mt-1 font-semibold text-gray-900 capitalize">{doc.decision || "Pending"}</p>
          </div>
        </div>

        {errorMessage && (
          <div className="mt-6 p-4 bg-red-50 border border-red-200 rounded-lg">
            <p className="text-sm font-medium text-red-800">Error</p>
            <p className="mt-1 text-sm text-red-600">{errorMessage}</p>
          </div>
        )}

        <div className="mt-6 flex gap-4">
          {(doc.status === "review" || doc.status === "manual_review") && (
            <Link
              to={`/review/${doc.id}`}
              className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700"
            >
              Review Document
            </Link>
          )}
        </div>
      </div>
    </div>
  );
}
