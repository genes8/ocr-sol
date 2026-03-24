import { AlertTriangle, CheckCircle, Info, XCircle } from "lucide-react";

interface Issue {
  id: string;
  type: "error" | "warning" | "info" | "success";
  field?: string;
  message: string;
  suggestion?: string;
}

interface IssuePanelProps {
  issues: Issue[];
  onDismiss?: (issueId: string) => void;
  onFix?: (issueId: string) => void;
}

const typeConfig = {
  error: {
    icon: XCircle,
    color: "text-red-600 bg-red-50 border-red-200",
    label: "Error",
  },
  warning: {
    icon: AlertTriangle,
    color: "text-yellow-600 bg-yellow-50 border-yellow-200",
    label: "Warning",
  },
  info: {
    icon: Info,
    color: "text-blue-600 bg-blue-50 border-blue-200",
    label: "Info",
  },
  success: {
    icon: CheckCircle,
    color: "text-green-600 bg-green-50 border-green-200",
    label: "Success",
  },
};

export function IssuePanel({ issues, onDismiss, onFix }: IssuePanelProps) {
  if (issues.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-gray-500">
        <CheckCircle className="w-8 h-8 mb-2" />
        <p>No issues detected</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      <div className="p-4 border-b border-gray-200 bg-white">
        <h3 className="text-sm font-semibold text-gray-900">
          Issues ({issues.length})
        </h3>
      </div>
      
      <div className="flex-1 overflow-auto p-4 space-y-3">
        {issues.map((issue) => {
          const config = typeConfig[issue.type];
          const Icon = config.icon;
          
          return (
            <div
              key={issue.id}
              className={`p-3 rounded-lg border ${config.color}`}
            >
              <div className="flex items-start gap-3">
                <Icon className="w-5 h-5 flex-shrink-0 mt-0.5" />
                <div className="flex-1 min-w-0">
                  {issue.field && (
                    <p className="text-xs font-medium opacity-75">
                      {issue.field}
                    </p>
                  )}
                  <p className="text-sm font-medium">{issue.message}</p>
                  {issue.suggestion && (
                    <p className="mt-1 text-xs opacity-75">
                      Suggestion: {issue.suggestion}
                    </p>
                  )}
                </div>
                <div className="flex items-center gap-1">
                  {onFix && (
                    <button
                      onClick={() => onFix(issue.id)}
                      className="p-1 hover:bg-black/10 rounded transition-colors"
                      title="Fix issue"
                    >
                      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                      </svg>
                    </button>
                  )}
                  {onDismiss && (
                    <button
                      onClick={() => onDismiss(issue.id)}
                      className="p-1 hover:bg-black/10 rounded transition-colors"
                      title="Dismiss"
                    >
                      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                      </svg>
                    </button>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// Helper hook to manage issues
export function useIssues() {
  const [issues, setIssues] = React.useState<Issue[]>([]);

  const addIssue = React.useCallback((issue: Omit<Issue, "id">) => {
    setIssues((prev) => [
      ...prev,
      { ...issue, id: crypto.randomUUID() },
    ]);
  }, []);

  const removeIssue = React.useCallback((id: string) => {
    setIssues((prev) => prev.filter((i) => i.id !== id));
  }, []);

  const clearIssues = React.useCallback(() => {
    setIssues([]);
  }, []);

  const addReconciliationIssues = React.useCallback(
    (reconciliation: {
      status: string;
      subtotal_match?: boolean;
      vat_match?: boolean;
      total_match?: boolean;
      discrepancy_details?: Record<string, unknown>;
    }) => {
      if (reconciliation.status === "fail") {
        if (!reconciliation.subtotal_match) {
          addIssue({
            type: "error",
            field: "Subtotal",
            message: "Calculated subtotal doesn't match extracted value",
            suggestion: "Review line items and unit prices",
          });
        }
        if (!reconciliation.vat_match) {
          addIssue({
            type: "warning",
            field: "VAT",
            message: "Calculated VAT doesn't match extracted value",
            suggestion: "Check VAT rate and subtotal calculation",
          });
        }
        if (!reconciliation.total_match) {
          addIssue({
            type: "error",
            field: "Total",
            message: "Calculated total doesn't match extracted value",
            suggestion: "Review all line items and tax calculations",
          });
        }
      }
    },
    [addIssue]
  );

  const addConfidenceIssues = React.useCallback(
    (
      confidences: Record<string, number>,
      threshold = 0.7
    ) => {
      Object.entries(confidences).forEach(([field, confidence]) => {
        if (confidence < threshold) {
          addIssue({
            type: confidence < 0.5 ? "error" : "warning",
            field: field.replace(/_/g, " "),
            message: `Low confidence: ${Math.round(confidence * 100)}%`,
            suggestion:
              confidence < 0.5
                ? "Manual verification recommended"
                : "Review this field",
          });
        }
      });
    },
    [addIssue]
  );

  return {
    issues,
    addIssue,
    removeIssue,
    clearIssues,
    addReconciliationIssues,
    addConfidenceIssues,
  };
}
