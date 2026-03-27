import { useQuery } from "@tanstack/react-query";
import { FileText } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { documentsApi } from "../services/api";

const STATUS_LABEL_MAP: Record<string, { label: string; className: string }> = {
  completed: { label: "Completed", className: "bg-green-100 text-green-800" },
  failed: { label: "Failed", className: "bg-red-100 text-red-800" },
  review: { label: "Needs Review", className: "bg-yellow-100 text-yellow-800" },
  manual_review: { label: "Needs Review", className: "bg-yellow-100 text-yellow-800" },
  in_progress: { label: "In Progress", className: "bg-blue-100 text-blue-800" },
  pending: { label: "Pending", className: "bg-gray-100 text-gray-800" },
};

function statusBadge(status: string) {
  const { label, className } = STATUS_LABEL_MAP[status] ?? {
    label: status.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()),
    className: "bg-gray-100 text-gray-800",
  };
  return (
    <span className={`px-2 py-1 text-xs rounded-full font-medium ${className}`}>
      {label}
    </span>
  );
}

export function Documents() {
  const navigate = useNavigate();
  const { data, isLoading } = useQuery({
    queryKey: ["documents"],
    queryFn: () => documentsApi.list(0, 50),
  });

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Documents</h1>
        <button
          onClick={() => navigate("/upload")}
          className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700"
        >
          Upload Document
        </button>
      </div>

      {isLoading ? (
        <div className="text-center py-8 text-gray-500">Loading...</div>
      ) : data?.items.length === 0 ? (
        <div className="text-center py-8 text-gray-500">
          No documents yet. Upload your first document to get started.
        </div>
      ) : (
        <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
          <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                  Document
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                  Type
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                  Status
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                  Date
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {data?.items.map((doc) => (
                <tr
                  key={doc.id}
                  className="hover:bg-gray-50 cursor-pointer"
                  onClick={() => navigate(`/documents/${doc.id}`)}
                >
                  <td className="px-6 py-4">
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
                  <td className="px-6 py-4">
                    <span className="px-2 py-1 text-xs bg-blue-100 text-blue-800 rounded-full">
                      {doc.document_type?.replace("_", " ") || "Unknown"}
                    </span>
                  </td>
                  <td className="px-6 py-4">
                    {statusBadge(doc.status)}
                  </td>
                  <td className="px-6 py-4 text-sm text-gray-500">
                    {new Date(doc.created_at).toLocaleDateString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        </div>
      )}
    </div>
  );
}
